"""
Road Redesign / Visual Output Agent.

Responsibilities:
  - Receive FeasibilityBrief + image_url from orchestrator
  - Call flux_inpaint tool with the design_brief as prompt context
  - Return RedesignOutput (base64 image + explanation) to orchestrator
"""

import logging

from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.redesign_output import RedesignOutput
from londonzero_agents.tools.flux_inpaint import FluxInpaintConfig, FluxInpaintInput, flux_inpaint

logger = logging.getLogger(__name__)


class RedesignAgentConfig(FunctionBaseConfig, name="redesign_agent"):
    flux: FluxInpaintConfig = Field(default_factory=FluxInpaintConfig)


class RedesignAgentInput(BaseModel):
    image_url: str = Field(description="Mapillary base image to inpaint")
    feasibility_brief: FeasibilityBrief


@register_function(
    FunctionInfo(
        name="redesign_agent",
        description=(
            "Generate a visual road redesign by inpainting a Mapillary image with FLUX, "
            "guided by the feasibility agent's design brief."
        ),
    )
)
async def run_redesign_agent(
    config: RedesignAgentConfig,
    input: RedesignAgentInput,
) -> RedesignOutput:
    return await flux_inpaint(
        config.flux,
        FluxInpaintInput(
            image_url=input.image_url,
            design_brief=input.feasibility_brief.design_brief,
            explanation=input.feasibility_brief.plain_explanation,
        ),
    )
