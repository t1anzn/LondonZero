"""
Road Redesign / Visual Output Agent.

Responsibilities:
  - Receive FeasibilityBrief + image_url from orchestrator
  - Call flux_inpaint tool with the design_brief as prompt context
  - Return RedesignOutput (base64 image + explanation) to orchestrator
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

from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.redesign_output import RedesignOutput
from londonzero_agents.tools.flux_inpaint import FluxInpaintInput

logger = logging.getLogger(__name__)


class RedesignAgentConfig(FunctionBaseConfig, name="redesign_agent"):
    flux_tool: FunctionRef = Field(default="flux_inpaint")


class RedesignAgentInput(BaseModel):
    image_url: str = Field(description="Mapillary base image to inpaint")
    feasibility_brief: FeasibilityBrief


@register_function(config_type=RedesignAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def run_redesign_agent(
    config: RedesignAgentConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    flux_fn = await builder.get_function(config.flux_tool)

    async def _run(input: RedesignAgentInput) -> RedesignOutput:
        return await flux_fn.ainvoke(
            FluxInpaintInput(
                image_url=input.image_url,
                design_brief=input.feasibility_brief.design_brief,
                explanation=input.feasibility_brief.plain_explanation,
            ),
            to_type=RedesignOutput,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Generate a visual road redesign by inpainting a Mapillary image with FLUX, "
            "guided by the feasibility agent's design brief."
        ),
        input_schema=RedesignAgentInput,
        single_output_schema=RedesignOutput,
    )
