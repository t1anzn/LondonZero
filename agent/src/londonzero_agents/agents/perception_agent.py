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

from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.hazard_assessment import HazardAssessment
from londonzero_agents.tools.image_understanding import (
    ImageUnderstandingConfig,
    ImageUnderstandingInput,
    image_understanding,
)
from londonzero_agents.prompt import build_perception_prompt

logger = logging.getLogger(__name__)


class PerceptionAgentConfig(FunctionBaseConfig, name="perception_agent"):
    vlm_name: LLMRef = Field(..., description="Cosmos Reasoning 8B model reference")
    reasoning: bool = Field(default=True)


class PerceptionAgentInput(BaseModel):
    image_url: str
    collision_profile: CollisionProfile


@register_function(
    FunctionInfo(
        name="perception_agent",
        description=(
            "Analyse a Mapillary street image conditioned on collision history. "
            "Returns a HazardAssessment with identified road hazards."
        ),
    )
)
async def run_perception_agent(
    config: PerceptionAgentConfig,
    input: PerceptionAgentInput,
) -> HazardAssessment:
    prompt = build_perception_prompt(input.collision_profile)

    tool_config = ImageUnderstandingConfig(
        vlm_name=config.vlm_name,
        reasoning=config.reasoning,
    )
    return await image_understanding(
        tool_config,
        ImageUnderstandingInput(image_url=input.image_url, prompt=prompt),
    )
