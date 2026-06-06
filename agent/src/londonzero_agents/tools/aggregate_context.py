"""
Jas skill 3 — Aggregate collision records + OSM context into a structured summary JSON.

Takes raw STATS19 records and OSM road features, produces a CollisionProfile
that all downstream agents consume.

# TODO (Jas/Balmee): implement aggregation logic; stub returns empty profile.
"""

import logging
from typing import Any

from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)


class AggregateContextConfig(FunctionBaseConfig, name="aggregate_context"):
    osm_pbf_path: str = Field(
        default="data/osm/greater-london-latest.osm.pbf",
        description="Path to Geofabrik Greater London OSM extract",
    )


class AggregateContextInput(BaseModel):
    location: LocationQuery
    raw_records: list[dict[str, Any]] = Field(
        description="STATS19 rows from load_collision_data"
    )


@register_function(
    FunctionInfo(
        name="aggregate_context",
        description=(
            "Aggregate raw STATS19 collision records and OSM road context into "
            "a structured CollisionProfile JSON for downstream agents."
        ),
    )
)
async def aggregate_context(
    config: AggregateContextConfig,
    input: AggregateContextInput,
) -> CollisionProfile:
    # TODO (Jas/Balmee): compute severity counts, cyclist/ped percentages,
    # dominant manoeuvre, and attach OSM features (crossings, lanes, etc.)
    logger.warning("aggregate_context: stub — returning empty CollisionProfile")
    return CollisionProfile(
        location=input.location.name,
        total_collisions=len(input.raw_records),
    )
