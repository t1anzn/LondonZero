"""
Data Retrieval Agent — owned by Jas / Balmee.

Responsibilities:
  1. Load raw STATS19 collision records (load_collision_data tool)
  2. Fetch best Mapillary image for the location (mapillary_search tool)
  3. Aggregate everything into a CollisionProfile JSON (aggregate_context tool)

Returns CollisionProfile + image URL back to the orchestrator.
"""

import logging
from collections.abc import AsyncGenerator

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.tools.aggregate_context import AggregateContextInput
from londonzero_agents.tools.load_collision_data import LoadCollisionDataInput, LoadCollisionDataOutput
from londonzero_agents.tools.mapillary_search import MapillarySearchInput, MapillarySearchOutput

logger = logging.getLogger(__name__)


class DataRetrievalAgentConfig(FunctionBaseConfig, name="data_retrieval_agent"):
    collision_tool: FunctionRef = Field(default="load_collision_data")
    mapillary_tool: FunctionRef = Field(default="mapillary_search")
    aggregate_tool: FunctionRef = Field(default="aggregate_context")


class DataRetrievalAgentInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2019)
    year_to: int = Field(default=2023)


class DataRetrievalAgentOutput(BaseModel):
    collision_profile: CollisionProfile
    image_url: str = Field(description="Best Mapillary image URL for this location")
    image_id: str


@register_function(config_type=DataRetrievalAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def run_data_retrieval_agent(
    config: DataRetrievalAgentConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    collision_tool = await builder.get_function(config.collision_tool)
    mapillary_tool = await builder.get_function(config.mapillary_tool)
    aggregate_tool = await builder.get_function(config.aggregate_tool)

    async def _run(input: DataRetrievalAgentInput) -> DataRetrievalAgentOutput:
        # Step 1 — load raw STATS19 records
        collision_raw = await collision_tool.ainvoke(
            LoadCollisionDataInput(location=input.location, year_from=input.year_from, year_to=input.year_to),
            to_type=LoadCollisionDataOutput,
        )

        # Step 2 — fetch Mapillary image
        mapillary_result = await mapillary_tool.ainvoke(
            MapillarySearchInput(location=input.location),
            to_type=MapillarySearchOutput,
        )

        # Step 3 — aggregate into CollisionProfile
        profile = await aggregate_tool.ainvoke(
            AggregateContextInput(location=input.location, raw_records=collision_raw.raw_records),
            to_type=CollisionProfile,
        )

        return DataRetrievalAgentOutput(
            collision_profile=profile,
            image_url=mapillary_result.image_url,
            image_id=mapillary_result.image_id,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Retrieve collision history and street imagery for a location. "
            "Returns a structured CollisionProfile and a Mapillary image URL."
        ),
        input_schema=DataRetrievalAgentInput,
        single_output_schema=DataRetrievalAgentOutput,
    )
