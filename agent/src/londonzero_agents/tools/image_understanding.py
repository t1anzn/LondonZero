"""
Image Understanding tool — adapted from vss_agents/tools/video_understanding.py.

Key simplification: single Mapillary JPEG instead of a multi-frame video.
- No VST, no MinIO, no frame selection
- Image is fetched by URL and base64-encoded before the VLM call
- Uses Cosmos Reasoning 8B via NVIDIA API Catalog
- The perception agent builds the collision-aware prompt before calling this tool
"""

import base64
import logging

import aiohttp
from langchain_core.messages import HumanMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.hazard_assessment import HazardAssessment

logger = logging.getLogger(__name__)


def _parse_thinking(content: str) -> tuple[str | None, str]:
    """Extract <think>...</think> reasoning trace and remaining answer."""
    if "<think>" in content and "</think>" in content:
        t_start = content.find("<think>")
        t_end = content.find("</think>")
        thinking = content[t_start + len("<think>") : t_end].strip()
        answer = content[t_end + len("</think>") :].strip()
        return thinking, answer
    return None, content


class ImageUnderstandingConfig(FunctionBaseConfig, name="image_understanding"):
    vlm_name: LLMRef = Field(..., description="VLM model reference (Cosmos Reasoning 8B)")
    reasoning: bool = Field(default=True, description="Enable Cosmos <think> reasoning trace")
    max_tokens: int = Field(default=2048)


class ImageUnderstandingInput(BaseModel):
    image_url: str = Field(description="Publicly accessible Mapillary image URL")
    prompt: str = Field(
        description=(
            "Collision-aware hazard detection prompt — constructed by perception_agent "
            "from the CollisionProfile before calling this tool."
        )
    )


@register_function(
    FunctionInfo(
        name="image_understanding",
        description=(
            "Analyse a single street-level image with a VLM (Cosmos Reasoning 8B). "
            "Returns identified hazards and missing infrastructure as structured JSON."
        ),
    )
)
async def image_understanding(
    config: ImageUnderstandingConfig,
    input: ImageUnderstandingInput,
) -> HazardAssessment:
    llm = Builder.get_llm(LLMFrameworkEnum.LANGCHAIN, config.vlm_name)

    # Fetch image and base64-encode
    async with aiohttp.ClientSession() as session:
        async with session.get(input.image_url) as resp:
            resp.raise_for_status()
            image_bytes = await resp.read()
    image_b64 = base64.b64encode(image_bytes).decode()

    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
            {"type": "text", "text": input.prompt},
        ]
    )

    response = await llm.ainvoke([message])
    raw_content = response.content if isinstance(response.content, str) else str(response.content)
    thinking, answer = _parse_thinking(raw_content)

    # TODO: parse answer into structured hazard lists (currently returns raw text)
    # Perception agent can post-process or a second LLM call can extract JSON
    return HazardAssessment(
        image_url=input.image_url,
        hazards=[answer],  # TODO: parse into list
        vlm_reasoning=thinking,
        raw_vlm_response=raw_content,
    )
