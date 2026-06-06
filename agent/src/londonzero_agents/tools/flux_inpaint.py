"""
FLUX Inpainting tool — generates road redesign by inpainting the Mapillary base image.

Uses FLUX.1-fill-dev (inpainting variant) via NVIDIA API Catalog.
Input: original Mapillary image + text prompt built from FeasibilityBrief.design_brief.
Output: base64-encoded redesigned image.

Check model availability at build.nvidia.com/models — model ID may vary.
"""

import base64
import logging
import os

import aiohttp
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.redesign_output import RedesignOutput

logger = logging.getLogger(__name__)

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"


class FluxInpaintConfig(FunctionBaseConfig, name="flux_inpaint"):
    model_id: str = Field(
        default="black-forest-labs/flux-fill-dev",
        description="FLUX inpainting model ID on NVIDIA API Catalog",
    )
    steps: int = Field(default=30)
    guidance_scale: float = Field(default=7.5)
    api_key: str = Field(
        default_factory=lambda: os.environ.get("NVIDIA_API_KEY", ""),
        description="NVIDIA API key",
    )


class FluxInpaintInput(BaseModel):
    image_url: str = Field(description="Mapillary base image URL")
    design_brief: str = Field(description="FeasibilityBrief.design_brief — drives the inpaint prompt")
    explanation: str = Field(default="", description="Plain-English explanation for the dashboard")


def _build_inpaint_prompt(design_brief: str) -> str:
    # TODO: refine prompt engineering — prepend style tokens, append quality suffix
    return (
        f"Aerial street-level urban redesign photograph. "
        f"{design_brief}. "
        "Photorealistic, daytime, London street, high detail."
    )


@register_function(
    FunctionInfo(
        name="flux_inpaint",
        description=(
            "Inpaint a Mapillary street image with FLUX to visualise a road redesign "
            "proposed by the feasibility agent."
        ),
    )
)
async def flux_inpaint(
    config: FluxInpaintConfig,
    input: FluxInpaintInput,
) -> RedesignOutput:
    prompt = _build_inpaint_prompt(input.design_brief)

    # Fetch and encode base image
    async with aiohttp.ClientSession() as session:
        async with session.get(input.image_url) as resp:
            resp.raise_for_status()
            base_bytes = await resp.read()
    base_b64 = base64.b64encode(base_bytes).decode()

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model_id,
        "prompt": prompt,
        "image": base_b64,
        "num_inference_steps": config.steps,
        "guidance_scale": config.guidance_scale,
        # TODO: add mask field once mask generation is defined
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{NVIDIA_API_BASE}/images/generations",
            json=payload,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

    # TODO: confirm response schema from NVIDIA API — adjust key path as needed
    result_b64 = data["data"][0]["b64_json"]

    return RedesignOutput(
        original_image_url=input.image_url,
        redesigned_image_b64=result_b64,
        inpaint_prompt=prompt,
        design_brief=input.design_brief,
        explanation=input.explanation,
    )
