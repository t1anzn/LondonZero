from pydantic import BaseModel, Field


class LocationQuery(BaseModel):
    """User-supplied location. MVP is hardcoded to Bank Junction — see config/locations.yaml."""

    name: str = Field(description="Human-readable junction or road name")
    lat: float = Field(description="Latitude")
    lon: float = Field(description="Longitude")
    radius_m: int = Field(default=100, description="Search radius in metres for data retrieval")
