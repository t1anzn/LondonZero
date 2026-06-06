"""
Jas skill 1 — Load STATS19 collision data from DfT download.

Downloads or reads cached CSV extracts from:
  https://www.gov.uk/government/statistical-data-sets/road-safety-open-data

# TODO (Jas): implement load logic; stub returns empty profile.
"""

import logging
from typing import Any

from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)


class LoadCollisionDataConfig(FunctionBaseConfig, name="load_collision_data"):
    data_dir: str = Field(
        default="data/stats19",
        description="Local path to downloaded STATS19 CSV files",
    )
    local_authority_code: str = Field(
        default="E09000001",
        description="ONS local authority code — default is City of London",
    )


class LoadCollisionDataInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2019)
    year_to: int = Field(default=2023)


class LoadCollisionDataOutput(BaseModel):
    raw_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Filtered STATS19 rows near the requested location",
    )
    record_count: int = 0


@register_function(
    FunctionInfo(
        name="load_collision_data",
        description=(
            "Load raw STATS19 collision records from DfT CSV extracts "
            "filtered to a radius around a given location."
        ),
    )
)
async def load_collision_data(
    config: LoadCollisionDataConfig,
    input: LoadCollisionDataInput,
) -> LoadCollisionDataOutput:
    # TODO (Jas): implement CSV load, spatial filter by radius, return records
    logger.warning("load_collision_data: stub — returning empty dataset")
    return LoadCollisionDataOutput()
