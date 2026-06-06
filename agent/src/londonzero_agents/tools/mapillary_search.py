"""
Mapillary radius search and image fetch.

Calls the Mapillary Graph API to:
  1. Find street-level images within a radius of a lat/lon (bbox query).
  2. Reject 360° panoramas (is_pano) and pick the image nearest the point.
  3. Return that image's thumbnail URL + metadata.

Ported from the proven logic in nishit/junction_audit.py.
API docs: https://www.mapillary.com/developer/api-documentation
Requires: MAPILLARY_ACCESS_TOKEN in environment.
"""

import logging
import math
import os
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

MAPILLARY_API_BASE = "https://graph.mapillary.com"


class MapillarySearchConfig(FunctionBaseConfig, name="mapillary_search"):
    access_token: str = Field(
        default_factory=lambda: os.environ.get("MAPILLARY_ACCESS_TOKEN") or os.environ.get("MAPILLARY_TOKEN", ""),
        description="Mapillary API access token (MLY|...)",
    )
    image_width: int = Field(default=1024, description="Thumbnail width: 256, 1024, or 2048")


class MapillarySearchInput(BaseModel):
    location: LocationQuery


class MapillarySearchOutput(BaseModel):
    image_url: str = Field(description="Best candidate street-level image URL")
    image_id: str = Field(description="Mapillary image ID")
    captured_at: str | None = Field(default=None, description="ISO timestamp of image capture")
    compass_angle: float | None = Field(default=None)
    lat: float | None = None
    lon: float | None = None


def _bbox(lat: float, lon: float, radius_m: int) -> str:
    """Convert a centre point + radius (metres) to a 'minLon,minLat,maxLon,maxLat' bbox."""
    dlat = radius_m / 111_000.0
    dlon = radius_m / (111_000.0 * max(math.cos(math.radians(lat)), 1e-6))
    return f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"


def _to_iso(captured_at) -> str | None:
    """Mapillary returns captured_at as epoch milliseconds; render it as an ISO string."""
    if captured_at is None:
        return None
    try:
        return datetime.fromtimestamp(int(captured_at) / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(captured_at)


@register_function(config_type=MapillarySearchConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def mapillary_search(
    config: MapillarySearchConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    async def _run(input: MapillarySearchInput) -> MapillarySearchOutput:
        loc = input.location
        thumb_field = f"thumb_{config.image_width}_url"
        params = {
            "access_token": config.access_token,
            "bbox": _bbox(loc.lat, loc.lon, loc.radius_m),
            "fields": f"id,geometry,is_pano,captured_at,compass_angle,{thumb_field}",
            "limit": 50,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MAPILLARY_API_BASE}/images", params=params) as resp:
                resp.raise_for_status()
                images = (await resp.json()).get("data", [])

        # Prefer flat images; fall back to whatever exists (incl. panos) if that's all there is.
        flat = [im for im in images if not im.get("is_pano")] or images
        if not flat:
            raise RuntimeError(f"Mapillary returned no images within {loc.radius_m} m of {loc.name}")

        def _dist(im: dict) -> float:
            x, y = im["geometry"]["coordinates"]  # [lon, lat]
            return math.hypot((y - loc.lat) * 111_000, (x - loc.lon) * 111_000 * math.cos(math.radians(loc.lat)))

        best = min(flat, key=_dist)
        bx, by = best["geometry"]["coordinates"]
        logger.info("mapillary_search: chose image %s near %s", best["id"], loc.name)

        return MapillarySearchOutput(
            image_url=best[thumb_field],
            image_id=str(best["id"]),
            captured_at=_to_iso(best.get("captured_at")),
            compass_angle=best.get("compass_angle"),
            lat=by,
            lon=bx,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Search Mapillary for the best street-level image near a location "
            "and return its URL for VLM analysis."
        ),
        input_schema=MapillarySearchInput,
        single_output_schema=MapillarySearchOutput,
    )
