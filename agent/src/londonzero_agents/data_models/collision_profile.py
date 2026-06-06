from typing import Any
from pydantic import BaseModel, Field


class CollisionProfile(BaseModel):
    """
    Structured collision summary produced by the data retrieval agent.

    # TODO (Jas/Balmee): finalise schema once aggregate_context tool output is confirmed.
    # Fields below are indicative — align with aggregate_context.py output JSON keys.
    """

    location: str = Field(description="Junction or road segment name")
    total_collisions: int = Field(description="Total collision count in dataset range")
    fatal: int = Field(default=0)
    serious: int = Field(default=0)
    slight: int = Field(default=0)
    cyclist_involved_pct: float = Field(default=0.0, description="Percentage of collisions involving cyclists")
    pedestrian_involved_pct: float = Field(default=0.0)
    dominant_manoeuvre: str | None = Field(default=None, description="Most common collision manoeuvre type")
    year_range: tuple[int, int] | None = Field(default=None, description="(start_year, end_year) of dataset")
    osm_context: dict[str, Any] = Field(default_factory=dict, description="Road layout features from OSM")
    raw: dict[str, Any] = Field(default_factory=dict, description="Full aggregated JSON from data agent")
