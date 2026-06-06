"""
Jas skill 2 — Mapillary radius search and image fetch.

Calls Mapillary Graph API to:
  1. Find images within radius of a lat/lon
  2. Select best candidate (most recent, facing junction)
  3. Return image URL and metadata

API docs: https://www.mapillary.com/developer/api-documentation
Requires: MAPILLARY_ACCESS_TOKEN in environment.

# TODO (Jas): implement API call; stub returns placeholder.
"""

import logging
import os

import aiohttp
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

MAPILLARY_API_BASE = "https://graph.mapillary.com"


class MapillarySearchConfig(FunctionBaseConfig, name="mapillary_search"):
    access_token: str = Field(
        default_factory=lambda: os.environ.get("MAPILLARY_ACCESS_TOKEN", ""),
        description="Mapillary API access token",
    )
    image_width: int = Field(default=1024, description="Requested image width (thumb_1024_url)")


class MapillarySearchInput(BaseModel):
    location: LocationQuery


class MapillarySearchOutput(BaseModel):
    image_url: str = Field(description="Best candidate street-level image URL")
    image_id: str = Field(description="Mapillary image ID")
    captured_at: str | None = Field(default=None, description="ISO timestamp of image capture")
    compass_angle: float | None = Field(default=None)
    lat: float | None = None
    lon: float | None = None


@register_function(
    FunctionInfo(
        name="mapillary_search",
        description=(
            "Search Mapillary for the best street-level image near a location "
            "and return its URL for VLM analysis."
        ),
    )
)
async def mapillary_search(
    config: MapillarySearchConfig,
    input: MapillarySearchInput,
) -> MapillarySearchOutput:
    # TODO (Jas): call Mapillary /images endpoint with bbox or radius,
    # rank results, fetch thumb_1024_url for best candidate.
    logger.warning("mapillary_search: stub — returning placeholder image")
    return MapillarySearchOutput(
        image_url="https://images.mapillary.com/placeholder",
        image_id="stub-id",
    )
