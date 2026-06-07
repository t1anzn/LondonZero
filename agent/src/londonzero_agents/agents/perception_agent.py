"""
Multimodal Perception Agent — owned by Nishit.

Responsibilities:
  - Receive a Mapillary image URL + CollisionProfile from the orchestrator
  - Build a collision-aware VLM prompt (conditions the model on known risk patterns)
  - Call image_understanding tool (Cosmos Reasoning 8B)
  - Parse and return HazardAssessment

The key insight: the prompt is built HERE, not in the tool, because the agent
has the collision context needed to direct what the VLM looks for.
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
from londonzero_agents.data_models.hazard_assessment import HazardAssessment
from londonzero_agents.tools.image_understanding import ImageUnderstandingInput
from londonzero_agents.prompt import build_perception_prompt

logger = logging.getLogger(__name__)


class PerceptionAgentConfig(FunctionBaseConfig, name="perception_agent"):
    image_tool: FunctionRef = Field(default="image_understanding")


class PerceptionAgentInput(BaseModel):
    image_url: str
    collision_profile: CollisionProfile


@register_function(config_type=PerceptionAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def run_perception_agent(
    config: PerceptionAgentConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    image_fn = await builder.get_function(config.image_tool)

    async def _run(input: PerceptionAgentInput) -> HazardAssessment:
        prompt = build_perception_prompt(input.collision_profile)

        return await image_fn.ainvoke(
            ImageUnderstandingInput(image_url=input.image_url, prompt=prompt),
            to_type=HazardAssessment,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Analyse a Mapillary street image conditioned on collision history. "
            "Returns a HazardAssessment with identified road hazards."
        ),
        input_schema=PerceptionAgentInput,
        single_output_schema=HazardAssessment,
    )
