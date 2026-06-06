from pydantic import BaseModel, Field


class FeasibilityBrief(BaseModel):
    """
    Output of He Xiao's Urban Infrastructure & Intervention Feasibility Agent.

    # TODO (He Xiao): populate fields to match your agent's output JSON contract.
    # The redesign_agent consumes this as its primary prompt context.
    """

    risk_factors: list[str] = Field(default_factory=list, description="Identified risk factors at this location")
    infrastructure_constraints: list[str] = Field(
        default_factory=list, description="Physical or regulatory constraints on intervention"
    )
    feasibility_score: float | None = Field(
        default=None, description="0.0–1.0 confidence that an intervention is feasible"
    )
    recommended_intervention: str | None = Field(
        default=None,
        description="Primary recommended intervention type (e.g. 'Add protected cycle lane')",
    )
    design_brief: str = Field(
        default="",
        description="Plain-English brief for the FLUX inpainting prompt. This is the key output.",
    )
    plain_explanation: str = Field(
        default="", description="User-facing explanation of the recommendation"
    )
    confidence_notes: str = Field(
        default="", description="Uncertainty and data-gap caveats"
    )
