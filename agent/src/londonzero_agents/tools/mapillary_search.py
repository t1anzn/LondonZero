"""
Jas skill 2 — Mapillary radius search and image fetch.

Ported from the manual pipeline (utils/street_images.py). The batch pipeline
fetched N images for the top-N national hotspots; here we fetch images within
radius of a *single* location and return the single best candidate for the VLM.

Calls Mapillary Graph API to:
  1. Find images within radius of a lat/lon (Image Radius Search, max 50 m)
  2. Rank candidates (highest quality_score, prefer non-pano, most recent)
  3. Return the best image's thumb URL and metadata

API docs: https://www.mapillary.com/developer/api-documentation
Requires: MAPILLARY_ACCESS_TOKEN in environment.
"""

from datetime import UTC
from datetime import datetime
import logging
import os

import aiohttp
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

MAPILLARY_API_BASE = "https://graph.mapillary.com"

# Max radius the Image Radius Search endpoint permits per call (metres).
MAX_RADIUS_M = 50

# Fields requested per image — mirrors street_images.py plus compass_angle.
IMAGE_FIELDS = "id,captured_at,geometry,compass_angle,thumb_1024_url,thumb_2048_url,is_pano,quality_score"


class MapillarySearchConfig(FunctionBaseConfig, name="mapillary_search"):
    access_token: str = Field(
        default_factory=lambda: os.environ.get("MAPILLARY_ACCESS_TOKEN", ""),
        description="Mapillary API access token",
    )
    image_width: int = Field(
        default=1024,
        description="Preferred thumb width (1024 or 2048); falls back if unavailable",
    )
    radius_m: int = Field(
        default=MAX_RADIUS_M,
        description=f"Search radius in metres (capped at {MAX_RADIUS_M} by the API)",
    )
    candidate_limit: int = Field(
        default=10,
        description="How many images to retrieve before ranking down to the best one",
    )
    timeout_s: int = Field(default=30)
    max_retries: int = Field(default=3)


class MapillarySearchInput(BaseModel):
    location: LocationQuery


class MapillarySearchOutput(BaseModel):
    image_url: str = Field(description="Best candidate street-level image URL")
    image_id: str = Field(description="Mapillary image ID")
    captured_at: str | None = Field(default=None, description="ISO timestamp of image capture")
    compass_angle: float | None = Field(default=None)
    lat: float | None = None
    lon: float | None = None
    candidates_found: int = Field(default=0, description="Number of images returned by the radius search")


def _thumb_url(img: dict, preferred_width: int) -> str | None:
    """Pick the requested thumb width, falling back to whatever is present."""
    order = ["thumb_1024_url", "thumb_2048_url"] if preferred_width <= 1024 else ["thumb_2048_url", "thumb_1024_url"]
    for key in order:
        if img.get(key):
            return img[key]
    return None


def _rank_key(img: dict) -> tuple[float, int]:
    """Higher is better: best quality_score, then most recent capture."""
    q = img.get("quality_score")
    q = float(q) if isinstance(q, (int, float)) else -1.0
    ts = img.get("captured_at") or 0
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        ts = 0
    return (q, ts)


def _to_iso(captured_at_ms: object) -> str | None:
    try:
        ms = int(captured_at_ms)  # Mapillary returns ms since epoch
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


async def _search_images_near(config: MapillarySearchConfig, lat: float, lon: float) -> list[dict]:
    """Mapillary Image Radius Search with retry — async port of street_images.search_images_near."""
    radius = min(config.radius_m, MAX_RADIUS_M)
    params = {
        "lat": lat,
        "lng": lon,
        "radius": radius,
        "limit": min(config.candidate_limit, 100),
        "fields": IMAGE_FIELDS,
    }
    headers = {
        "Authorization": f"OAuth {config.access_token}",
        # Mapillary serves Brotli ('br') by default, which aiohttp fails to decode
        # ("Can not decode content-encoding: br"). Request gzip/deflate explicitly —
        # this is what the original requests-based pipeline did implicitly.
        "Accept-Encoding": "gzip, deflate",
    }
    timeout = aiohttp.ClientTimeout(total=config.timeout_s)

    for attempt in range(1, config.max_retries + 1):
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(f"{MAPILLARY_API_BASE}/images", params=params, headers=headers) as resp,
            ):
                if resp.status in (400, 401, 403):
                    logger.error(
                        "mapillary_search: non-retryable HTTP %d — check token/params",
                        resp.status,
                    )
                    return []
                resp.raise_for_status()
                payload = await resp.json()
                return payload.get("data", [])
        except aiohttp.ClientError as exc:
            logger.warning(
                "mapillary_search: request error %s (attempt %d/%d)",
                exc,
                attempt,
                config.max_retries,
            )
    logger.error("mapillary_search: exhausted retries at (%.5f, %.5f)", lat, lon)
    return []


async def mapillary_search(
    config: MapillarySearchConfig,
    input: MapillarySearchInput,
) -> MapillarySearchOutput:
    """Search Mapillary for the best street-level image near a location and return
    its URL for VLM analysis. Called directly by data_retrieval_agent."""
    if not config.access_token:
        logger.warning("mapillary_search: MAPILLARY_ACCESS_TOKEN not set — returning empty result")
        return MapillarySearchOutput(image_url="", image_id="")

    loc = input.location
    images = await _search_images_near(config, loc.lat, loc.lon)

    if not images:
        logger.warning(
            "mapillary_search: no images within %dm of %s (%.5f, %.5f)",
            min(config.radius_m, MAX_RADIUS_M),
            loc.name,
            loc.lat,
            loc.lon,
        )
        return MapillarySearchOutput(image_url="", image_id="", candidates_found=0)

    # Prefer non-panoramic frames; fall back to panos only if nothing else.
    non_pano = [i for i in images if not i.get("is_pano")]
    pool = non_pano or images
    best = max(pool, key=_rank_key)

    geom = best.get("geometry", {}) or {}
    coords = geom.get("coordinates", [None, None])
    img_lon, img_lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)

    image_url = _thumb_url(best, config.image_width) or ""
    logger.info(
        "mapillary_search: %d candidates near %s, selected image %s (quality=%s)",
        len(images),
        loc.name,
        best.get("id"),
        best.get("quality_score"),
    )

    return MapillarySearchOutput(
        image_url=image_url,
        image_id=str(best.get("id", "")),
        captured_at=_to_iso(best.get("captured_at")),
        compass_angle=best.get("compass_angle"),
        lat=img_lat,
        lon=img_lon,
        candidates_found=len(images),
    )
