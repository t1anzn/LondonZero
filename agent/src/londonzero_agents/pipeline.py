"""
Shared LondonZero pipeline — single source of truth for the agent flow.

Both the orchestrator agent (``nat run`` / single-shot) and the FastAPI SSE
endpoint drive this generator, so the streamed dashboard view and the console
output can never diverge.

Flow (the authoritative order):
  1. Data retrieval  → CollisionProfile + Mapillary image (the "before")
  2. Perception      → HazardAssessment (VLM sees image + collision context)
  3. Feasibility     → FeasibilityBrief (grounded by NVIDIA-embedding RAG over
                       TfL / London planning guidance)
  4. Recommendation  → orchestrator LLM reasons over ALL of the above (incl. the
                       retrieved guidance) → final recommendation + a distilled
                       visual brief
  5. Redesign        → FLUX inpaints the image using the orchestrator's visual
                       brief (the "after")

The generator yields plain dict events so callers can stream them (SSE) or
collect them (single-shot). Event shapes:
  {"type": "status", "stage": <s>, "state": "running"|"done", "step": n, "of": 5}
  {"type": "stage",  "stage": <s>, "payload": {...}}   # payload values may be pydantic models
  {"type": "error",  "stage": <s>, "message": str}
  {"type": "done",   "payload": {...}}                  # assembled final result
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from londonzero_agents.agents.data_retrieval_agent import (
    DataRetrievalAgentInput,
    DataRetrievalAgentOutput,
)
from londonzero_agents.agents.feasibility_agent import FeasibilityAgentInput
from londonzero_agents.agents.perception_agent import PerceptionAgentInput
from londonzero_agents.agents.redesign_agent import RedesignAgentInput
from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.hazard_assessment import HazardAssessment
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.data_models.redesign_output import RedesignOutput
from londonzero_agents.prompt import ORCHESTRATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

STAGES = ["data", "vision", "feasibility", "recommendation", "redesign"]


def _text(response: Any) -> str:
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def build_synthesis_prompt(query, collision_profile, hazards, feasibility) -> str:
    guidance = "\n".join(f"- {c}" for c in feasibility.guidance_citations) or "- (none retrieved)"
    return (
        f"User question: {query}\n\n"
        f"Location: {collision_profile.location}\n"
        f"Collisions: {collision_profile.total_collisions} total "
        f"({collision_profile.fatal} fatal, {collision_profile.serious} serious, "
        f"{collision_profile.slight} slight)\n"
        f"Cyclist involvement: {collision_profile.cyclist_involved_pct:.0%}; "
        f"pedestrian involvement: {collision_profile.pedestrian_involved_pct:.0%}\n\n"
        "Identified hazards:\n" + "\n".join(f"- {h}" for h in hazards.hazards) + "\n\n"
        f"Junction complexity: {hazards.junction_complexity or 'unknown'}\n\n"
        f"Recommended intervention: {feasibility.recommended_intervention}\n"
        f"Feasibility score: {feasibility.feasibility_score}\n"
        f"Feasibility brief: {feasibility.design_brief}\n\n"
        f"Relevant planning guidance (retrieved):\n{guidance}\n\n"
        "Synthesise the above into a clear, plain-English recommendation for a city planner. "
        "Cite the collision evidence and, where relevant, the planning guidance. "
        "Do not overclaim causality. Flag uncertainty and data gaps."
    )


def build_redesign_brief_prompt(summary, feasibility) -> str:
    return (
        "Convert the following road-safety recommendation into a SINGLE concise visual "
        "instruction (1-2 sentences) describing the physical changes to draw on the street "
        "for an image-inpainting model. Focus only on visible infrastructure: protected cycle "
        "lanes, coloured surfacing, pedestrian crossings, refuge islands, lane markings, kerbs, "
        "materials. No preamble, no caveats.\n\n"
        f"Primary intervention: {feasibility.recommended_intervention}\n"
        f"Recommendation:\n{summary}"
    )


async def stream_pipeline(
    *,
    data_fn,
    perception_fn,
    feasibility_fn,
    redesign_fn,
    llm,
    loc: LocationQuery,
    query: str,
) -> AsyncGenerator[dict, None]:
    """Run the full pipeline, yielding status/stage/done/error events."""

    # ── Step 1: Data Retrieval ──────────────────────────────────────────────
    yield {"type": "status", "stage": "data", "state": "running", "step": 1, "of": 5}
    data_result = await data_fn.ainvoke(
        DataRetrievalAgentInput(location=loc), to_type=DataRetrievalAgentOutput
    )
    collision_profile = data_result.collision_profile
    original_image_url = data_result.image_url
    yield {
        "type": "stage",
        "stage": "data",
        "payload": {"collision_profile": collision_profile, "original_image_url": original_image_url},
    }
    yield {"type": "status", "stage": "data", "state": "done", "step": 1, "of": 5}

    # ── Step 2: Perception ──────────────────────────────────────────────────
    yield {"type": "status", "stage": "vision", "state": "running", "step": 2, "of": 5}
    hazard_result: HazardAssessment = await perception_fn.ainvoke(
        PerceptionAgentInput(image_url=original_image_url, collision_profile=collision_profile),
        to_type=HazardAssessment,
    )
    yield {"type": "stage", "stage": "vision", "payload": {"hazard_assessment": hazard_result}}
    yield {"type": "status", "stage": "vision", "state": "done", "step": 2, "of": 5}

    # ── Step 3: Feasibility (RAG-grounded) ──────────────────────────────────
    yield {"type": "status", "stage": "feasibility", "state": "running", "step": 3, "of": 5}
    feasibility_result: FeasibilityBrief = await feasibility_fn.ainvoke(
        FeasibilityAgentInput(collision_profile=collision_profile, hazard_assessment=hazard_result),
        to_type=FeasibilityBrief,
    )
    yield {
        "type": "stage",
        "stage": "feasibility",
        "payload": {"feasibility_brief": feasibility_result},
    }
    yield {"type": "status", "stage": "feasibility", "state": "done", "step": 3, "of": 5}

    # ── Step 4: Orchestrator recommendation (reasons over everything) ───────
    yield {"type": "status", "stage": "recommendation", "state": "running", "step": 4, "of": 5}
    summary = _text(
        await llm.ainvoke(
            [
                SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
                HumanMessage(
                    content=build_synthesis_prompt(
                        query, collision_profile, hazard_result, feasibility_result
                    )
                ),
            ]
        )
    ).strip()

    # Distil the recommendation into a visual brief that drives FLUX.
    redesign_brief = feasibility_result.design_brief
    try:
        distilled = _text(
            await llm.ainvoke(
                [
                    SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
                    HumanMessage(content=build_redesign_brief_prompt(summary, feasibility_result)),
                ]
            )
        ).strip()
        if distilled:
            redesign_brief = distilled
    except Exception as exc:  # noqa: BLE001 — fall back to the feasibility brief
        logger.warning("pipeline: redesign-brief distillation failed, using feasibility brief: %s", exc)

    yield {
        "type": "stage",
        "stage": "recommendation",
        "payload": {"summary": summary, "design_brief": redesign_brief},
    }
    yield {"type": "status", "stage": "recommendation", "state": "done", "step": 4, "of": 5}

    # ── Step 5: Redesign (FLUX, driven by the orchestrator's brief) ─────────
    yield {"type": "status", "stage": "redesign", "state": "running", "step": 5, "of": 5}
    redesign_result: RedesignOutput = await redesign_fn.ainvoke(
        RedesignAgentInput(
            image_url=original_image_url,
            design_brief=redesign_brief,
            explanation=feasibility_result.plain_explanation,
        ),
        to_type=RedesignOutput,
    )
    yield {"type": "stage", "stage": "redesign", "payload": {"redesign": redesign_result}}
    yield {"type": "status", "stage": "redesign", "state": "done", "step": 5, "of": 5}

    # ── Final assembled result ──────────────────────────────────────────────
    yield {
        "type": "done",
        "payload": {
            "summary": summary,
            "collision_profile": collision_profile,
            "hazard_assessment": hazard_result,
            "feasibility_brief": feasibility_result,
            "redesign": redesign_result,
        },
    }
