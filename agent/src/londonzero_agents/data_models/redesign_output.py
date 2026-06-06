from pydantic import BaseModel, Field


class RedesignOutput(BaseModel):
    """Final output returned to the orchestrator and dashboard."""

    original_image_url: str = Field(description="Mapillary base image that was inpainted")
    redesigned_image_b64: str = Field(description="Base64-encoded FLUX inpainted result")
    inpaint_prompt: str = Field(description="Prompt used for FLUX inpainting")
    design_brief: str = Field(description="Human-readable brief that drove the inpaint prompt")
    explanation: str = Field(description="Plain-English explanation for dashboard display")
