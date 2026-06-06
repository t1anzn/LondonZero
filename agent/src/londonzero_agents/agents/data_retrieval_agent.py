"""
Data Retrieval Agent — owned by Jas / Balmee.

Responsibilities:
  1. Load raw STATS19 collision records (load_collision_data tool)
  2. Fetch best Mapillary image for the location (mapillary_search tool)
  3. Aggregate everything into a CollisionProfile JSON (aggregate_context tool)

Returns CollisionProfile + image URL back to the orchestrator.

Logic ported from the standalone Skill 1/Skill 2 pipeline (DfT STATS19 download +
decode + radius filter; Mapillary radius search). The three tools are implemented
in tools/{load_collision_data,mapillary_search,aggregate_context}.py.
"""

import logging

from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.tools.aggregate_context import AggregateContextConfig
from londonzero_agents.tools.aggregate_context import AggregateContextInput
from londonzero_agents.tools.aggregate_context import aggregate_context
from londonzero_agents.tools.load_collision_data import LoadCollisionDataConfig
from londonzero_agents.tools.load_collision_data import LoadCollisionDataInput
from londonzero_agents.tools.load_collision_data import load_collision_data
from londonzero_agents.tools.mapillary_search import MapillarySearchConfig
from londonzero_agents.tools.mapillary_search import MapillarySearchInput
from londonzero_agents.tools.mapillary_search import mapillary_search

logger = logging.getLogger(__name__)


class DataRetrievalAgentConfig(FunctionBaseConfig, name="data_retrieval_agent"):
    collision_data: LoadCollisionDataConfig = Field(default_factory=LoadCollisionDataConfig)
    mapillary: MapillarySearchConfig = Field(default_factory=MapillarySearchConfig)
    aggregation: AggregateContextConfig = Field(default_factory=AggregateContextConfig)


class DataRetrievalAgentInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2020)
    year_to: int = Field(default=2024)


class DataRetrievalAgentOutput(BaseModel):
    collision_profile: CollisionProfile
    image_url: str = Field(description="Best Mapillary image URL for this location")
    image_id: str


async def run_data_retrieval_agent(
    config: DataRetrievalAgentConfig,
    input: DataRetrievalAgentInput,
) -> DataRetrievalAgentOutput:
    """Retrieve collision history and street imagery for a location, returning a
    structured CollisionProfile and a Mapillary image URL. Invoked directly by the
    orchestrator (plain async call, not via the NAT function registry)."""
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
