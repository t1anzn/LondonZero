"""
Data Retrieval Agent — owned by Jas / Balmee.

Responsibilities:
  1. Load raw STATS19 collision records (load_collision_data tool)
  2. Fetch best Mapillary image for the location (mapillary_search tool)
  3. Aggregate everything into a CollisionProfile JSON (aggregate_context tool)

Returns CollisionProfile + image URL back to the orchestrator.

# TODO (Jas/Balmee): implement the three tools this agent wraps,
# then wire them in execute() below.
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.tools.aggregate_context import AggregateContextConfig, aggregate_context, AggregateContextInput
from londonzero_agents.tools.load_collision_data import LoadCollisionDataConfig, load_collision_data, LoadCollisionDataInput
from londonzero_agents.tools.mapillary_search import MapillarySearchConfig, mapillary_search, MapillarySearchInput

logger = logging.getLogger(__name__)


class DataRetrievalAgentConfig(FunctionBaseConfig, name="data_retrieval_agent"):
    collision_data: LoadCollisionDataConfig = Field(default_factory=LoadCollisionDataConfig)
    mapillary: MapillarySearchConfig = Field(default_factory=MapillarySearchConfig)
    aggregation: AggregateContextConfig = Field(default_factory=AggregateContextConfig)


class DataRetrievalAgentInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2019)
    year_to: int = Field(default=2023)


class DataRetrievalAgentOutput(BaseModel):
    collision_profile: CollisionProfile
    image_url: str = Field(description="Best Mapillary image URL for this location")
    image_id: str


@register_function(
    FunctionInfo(
        name="data_retrieval_agent",
        description=(
            "Retrieve collision history and street imagery for a location. "
            "Returns a structured CollisionProfile and a Mapillary image URL."
        ),
    )
)
async def run_data_retrieval_agent(
    config: DataRetrievalAgentConfig,
    input: DataRetrievalAgentInput,
) -> DataRetrievalAgentOutput:
    # Step 1 — load raw STATS19 records
    collision_raw = await load_collision_data(
        config.collision_data,
        LoadCollisionDataInput(location=input.location, year_from=input.year_from, year_to=input.year_to),
    )

    # Step 2 — fetch Mapillary image
    mapillary_result = await mapillary_search(
        config.mapillary,
        MapillarySearchInput(location=input.location),
    )

    # Step 3 — aggregate into CollisionProfile
    profile = await aggregate_context(
        config.aggregation,
        AggregateContextInput(location=input.location, raw_records=collision_raw.raw_records),
    )

    return DataRetrievalAgentOutput(
        collision_profile=profile,
        image_url=mapillary_result.image_url,
        image_id=mapillary_result.image_id,
    )
