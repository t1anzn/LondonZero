from pydantic import BaseModel, Field


class HazardAssessment(BaseModel):
    """
    Output of the perception agent — hazards identified from the Mapillary street image
    conditioned on the collision profile.
    """

    image_url: str = Field(description="Mapillary image URL that was analysed")
    hazards: list[str] = Field(description="List of identified hazard descriptions")
    missing_infrastructure: list[str] = Field(
        default_factory=list,
        description="Infrastructure expected but absent (e.g. 'no cycle lane markings')",
    )
    visibility_issues: list[str] = Field(default_factory=list)
    junction_complexity: str | None = Field(
        default=None, description="Low / Medium / High — subjective VLM assessment"
    )
    vlm_reasoning: str | None = Field(default=None, description="Raw reasoning trace from Cosmos if enabled")
    raw_vlm_response: str = Field(default="", description="Full VLM text response before parsing")
