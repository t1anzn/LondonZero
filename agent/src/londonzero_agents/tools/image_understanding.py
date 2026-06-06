"""
Image Understanding tool — cloud VLM hazard reader (Cosmos-Reason2-8B).

Calls the NVIDIA-hosted, OpenAI-compatible chat/completions endpoint with the
Mapillary street image (base64) plus the collision-aware prompt that
perception_agent built. Cosmos returns reasoning in <think>…</think> and its
answer in <answer>…</answer>; we ask for the answer as JSON and parse it into a
structured HazardAssessment.

Cloud path (MVP). Running Cosmos locally on the Spark via transformers is a later
optimisation — the proven local version lives in nishit/junction_audit.py.
"""

import base64
import json
import logging
import os
import re

import aiohttp
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.hazard_assessment import HazardAssessment

logger = logging.getLogger(__name__)

# NVIDIA-hosted, OpenAI-compatible VLM endpoint (verified: integrate.api.nvidia.com).
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Appended to the perception prompt so Cosmos returns parseable, structured output.
# Cosmos-Reason2 is trained to answer as <think>…</think><answer>…</answer>.
_FORMAT_INSTRUCTION = (
    "\n\nAnswer in EXACTLY this format and nothing else:\n"
    "<think>\nbrief reasoning grounded in what is visible\n</think>\n"
    "<answer>\n"
    "{\n"
    '  "hazards": ["short hazard description", "..."],\n'
    '  "missing_infrastructure": ["expected but absent, e.g. no cycle lane", "..."],\n'
    '  "visibility_issues": ["sightline/signage/lighting problems", "..."],\n'
    '  "junction_complexity": "Low|Medium|High"\n'
    "}\n"
    "</answer>\n"
    "Output ONLY valid JSON inside <answer>. Do not invent features not visible in the image."
)


def _split_think_answer(content: str) -> tuple[str | None, str]:
    """Separate the <think> reasoning trace from the <answer> payload."""
    think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
    thinking = think_match.group(1).strip() if think_match else None
    answer_match = re.search(r"<answer>(.*?)</answer>", content, re.DOTALL)
    answer = answer_match.group(1).strip() if answer_match else content.strip()
    return thinking, answer


def _parse_hazard_json(answer: str) -> dict:
    """Best-effort: pull the JSON object out of the answer (tolerates ```json fences)."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", answer, re.DOTALL)
    blob = fenced.group(1) if fenced else answer
    brace = re.search(r"\{.*\}", blob, re.DOTALL)  # outermost {...}
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            logger.warning("image_understanding: answer was not valid JSON, falling back to raw text")
    return {}


class ImageUnderstandingConfig(FunctionBaseConfig, name="image_understanding"):
    # vlm_name kept so perception_agent/orchestrator keep constructing this unchanged;
    # in the cloud-direct path the model is selected by model_id below.
    vlm_name: LLMRef = Field(..., description="(legacy NAT ref — ignored in cloud-direct mode)")
    reasoning: bool = Field(default=True, description="Ask Cosmos for a <think> reasoning trace")
    # Cosmos-Reason2 is listed on the catalog but NOT enabled for hosted inference on this
    # account (404 "Function not found"). nvidia/nemotron-nano-12b-v2-vl IS hosted and is
    # NVIDIA-built. The proven LOCAL Spark path still uses real Cosmos (see junction_audit.py).
    model_id: str = Field(
        default="nvidia/nemotron-nano-12b-v2-vl",
        description="Hosted VLM id (e.g. nvidia/nemotron-nano-12b-v2-vl or meta/llama-3.2-90b-vision-instruct)",
    )
    api_key: str = Field(
        default_factory=lambda: os.environ.get("NVIDIA_API_KEY", ""),
        description="NVIDIA API key (Bearer token)",
    )
    base_url: str = Field(default=NVIDIA_CHAT_URL, description="OpenAI-compatible chat/completions URL")
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.2)


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
            "Analyse a single street-level image with a VLM (Cosmos-Reason2-8B) and return "
            "structured road-safety hazards as a HazardAssessment."
        ),
    )
)
async def image_understanding(
    config: ImageUnderstandingConfig,
    input: ImageUnderstandingInput,
) -> HazardAssessment:
    # 1. Fetch the Mapillary image and base64-encode it (catalog accepts JPG/PNG).
    async with aiohttp.ClientSession() as session:
        async with session.get(input.image_url) as resp:
            resp.raise_for_status()
            image_bytes = await resp.read()
    image_b64 = base64.b64encode(image_bytes).decode()

    # 2. Build the OpenAI-compatible payload: image + collision-aware prompt + JSON format ask.
    full_prompt = input.prompt + _FORMAT_INSTRUCTION
    payload = {
        "model": config.model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": full_prompt},
                ],
            }
        ],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        # Local Cosmos needed repetition_penalty/no_repeat_ngram to stop hazard-loops; on the
        # OpenAI-compatible cloud API the nearest knob is frequency_penalty.
        "frequency_penalty": 0.3,
    }
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}

    # 3. Call the hosted VLM.
    async with aiohttp.ClientSession() as session:
        async with session.post(config.base_url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    raw_content = data["choices"][0]["message"]["content"]

    # 4. Split reasoning vs answer, parse the answer JSON into the structured fields.
    thinking, answer = _split_think_answer(raw_content)
    parsed = _parse_hazard_json(answer)
    hazards = parsed.get("hazards") or ([answer] if answer else [])

    return HazardAssessment(
        image_url=input.image_url,
        hazards=hazards if isinstance(hazards, list) else [str(hazards)],
        missing_infrastructure=parsed.get("missing_infrastructure") or [],
        visibility_issues=parsed.get("visibility_issues") or [],
        junction_complexity=parsed.get("junction_complexity"),
        vlm_reasoning=thinking,
        raw_vlm_response=raw_content,
    )
