"""
FLUX redesign tool — LOCAL FLUX-Fill inpainting on the DGX Spark.

The hosted NVIDIA FLUX endpoints only edit NVIDIA's own demo images (they reject
user/base64/asset images), so the redesign step runs locally via diffusers
FluxFillPipeline — the proven path from nishit/junction_audit.py:
  fetch the Mapillary image -> build a road mask -> inpaint the design brief ->
  return the 'after' image as base64.

This is the Spark-local half of the hybrid pipeline (perception stays on the cloud VLM).
Requirements at runtime:
  - the process runs in an env with CUDA torch + diffusers (the proven Spark venv),
  - black-forest-labs/FLUX.1-Fill-dev is present in ~/.cache/huggingface (already downloaded).
"""

import asyncio
import base64
import io
import logging
import urllib.request
from collections.abc import AsyncGenerator

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.redesign_output import RedesignOutput

logger = logging.getLogger(__name__)

# Lazy singleton: load the (heavy) pipeline once on first call, reuse it after.
_FLUX = None


def _load_flux(model_id: str):
    global _FLUX
    if _FLUX is None:
        import torch
        from diffusers import FluxFillPipeline

        logger.info("Loading %s (once)…", model_id)
        _FLUX = FluxFillPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16).to("cuda")
    return _FLUX


def _make_mask(width: int, height: int):
    """White trapezoid over the road surface (bottom-centre) — the region we let FLUX redraw."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).polygon(
        [(width * 0.06, height * 0.60), (width * 0.78, height * 0.60),
         (width * 0.97, height), (width * 0.02, height)],
        fill=255,
    )
    return mask


def _render_sync(image_bytes: bytes, prompt: str, model_id: str, steps: int, guidance: float,
                 size=(1024, 768)) -> str:
    """Blocking GPU work — run via asyncio.to_thread so it doesn't stall the event loop."""
    from PIL import Image

    pipe = _load_flux(model_id)
    base = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize(size)
    mask = _make_mask(*size)
    result = pipe(
        prompt=prompt, image=base, mask_image=mask,
        height=size[1], width=size[0],
        num_inference_steps=steps, guidance_scale=guidance,
    ).images[0]
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


class FluxInpaintConfig(FunctionBaseConfig, name="flux_inpaint"):
    model_id: str = Field(
        default="black-forest-labs/FLUX.1-Fill-dev",
        description="Local diffusers FLUX-Fill model id (must be in the HF cache)",
    )
    steps: int = Field(default=30, description="Inference steps")
    guidance_scale: float = Field(default=30.0, description="FLUX-Fill guidance (proven value ~30)")


class FluxInpaintInput(BaseModel):
    image_url: str = Field(description="Mapillary base image URL")
    design_brief: str = Field(description="FeasibilityBrief.design_brief — drives the inpaint prompt")
    explanation: str = Field(default="", description="Plain-English explanation for the dashboard")


def _build_inpaint_prompt(design_brief: str) -> str:
    # Ground the render in real City of London street design (LTN 1/20 + the "All Change at
    # Bank" public-realm scheme): plain asphalt + stone paving, NOT bright-green whole lanes.
    return (
        f"{design_brief}. Rendered as a realistic City of London street: plain smooth dark "
        "asphalt carriageway, pale Yorkstone stone-paved footways, granite kerbs, street trees "
        "and planters, a level stone pedestrian crossing, minimal clean road markings, no bright "
        "green paint. Photorealistic, daytime, high detail."
    )


@register_function(config_type=FluxInpaintConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def flux_inpaint(
    config: FluxInpaintConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    async def _run(input: FluxInpaintInput) -> RedesignOutput:
        prompt = _build_inpaint_prompt(input.design_brief)

        # Fetch the base image, then run the heavy GPU inpaint off the event loop.
        def _fetch(url: str) -> bytes:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()

        image_bytes = await asyncio.to_thread(_fetch, input.image_url)
        result_b64 = await asyncio.to_thread(
            _render_sync, image_bytes, prompt, config.model_id, config.steps, config.guidance_scale
        )

        return RedesignOutput(
            original_image_url=input.image_url,
            redesigned_image_b64=result_b64,
            inpaint_prompt=prompt,
            design_brief=input.design_brief,
            explanation=input.explanation,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Inpaint a Mapillary street image with local FLUX-Fill to visualise the road "
            "redesign from the feasibility agent's design brief."
        ),
        input_schema=FluxInpaintInput,
        single_output_schema=RedesignOutput,
    )
