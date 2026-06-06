"""
Orchestrator / Supervisor Agent — powered by Nemotron Super (cloud).

This is the top-level LangGraph agent. It:
  1. Receives a LocationQuery from the user (or hardcoded Bank Junction for MVP)
  2. Calls data_retrieval_agent  → CollisionProfile + image_url
  3. Calls perception_agent      → HazardAssessment
  4. Calls feasibility_agent     → FeasibilityBrief
  5. Calls redesign_agent        → RedesignOutput
  6. Streams a final plain-English recommendation back to the UI

Session memory: InMemorySaver (LangGraph checkpoint) — sufficient for MVP.
For persistence across restarts, swap for SqliteSaver.

Model: nvidia/llama-3.1-nemotron-ultra-253b-v1 via NVIDIA API Catalog.
"""

import logging
import re
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef, LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

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


class OrchestratorConfig(FunctionBaseConfig, name="orchestrator_agent"):
    llm_name: LLMRef = Field(..., description="Nemotron Super model reference")
    data_retrieval_tool: FunctionRef = Field(default="data_retrieval_agent")
    perception_tool: FunctionRef = Field(default="perception_agent")
    feasibility_tool: FunctionRef = Field(default="feasibility_agent")
    redesign_tool: FunctionRef = Field(default="redesign_agent")
    location_name: str = Field(default="Bank Junction")
    location_lat: float = Field(default=51.5133)
    location_lon: float = Field(default=-0.0886)
    location_radius_m: int = Field(default=100)


class OrchestratorOutput(BaseModel):
    summary: str
    redesign: RedesignOutput | None = None
    session_id: str | None = None


@register_function(config_type=OrchestratorConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def run_orchestrator(config: OrchestratorConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    # ── One-time setup: resolve sub-functions and the LLM once ──────────────
    data_fn = await builder.get_function(config.data_retrieval_tool)
    perception_fn = await builder.get_function(config.perception_tool)
    feasibility_fn = await builder.get_function(config.feasibility_tool)
    redesign_fn = await builder.get_function(config.redesign_tool)
    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _run(query: str) -> OrchestratorOutput:
        loc = LocationQuery(
            name=config.location_name,
            lat=config.location_lat,
            lon=config.location_lon,
            radius_m=config.location_radius_m,
        )

        # ── Step 1: Data Retrieval ──────────────────────────────────────────
        logger.info("Orchestrator: running data_retrieval_agent for %s", loc.name)
        data_result = await data_fn.ainvoke(
            DataRetrievalAgentInput(location=loc),
            to_type=DataRetrievalAgentOutput,
        )

        # ── Step 2: Perception ─────────────────────────────────────────────
        logger.info("Orchestrator: running perception_agent")
        hazard_result = await perception_fn.ainvoke(
            PerceptionAgentInput(
                image_url=data_result.image_url,
                collision_profile=data_result.collision_profile,
            ),
            to_type=HazardAssessment,
        )

        # ── Step 3: Feasibility ────────────────────────────────────────────
        logger.info("Orchestrator: running feasibility_agent")
        feasibility_result = await feasibility_fn.ainvoke(
            FeasibilityAgentInput(
                collision_profile=data_result.collision_profile,
                hazard_assessment=hazard_result,
            ),
            to_type=FeasibilityBrief,
        )

        # ── Step 4: Redesign ───────────────────────────────────────────────
        logger.info("Orchestrator: running redesign_agent")
        redesign_result = await redesign_fn.ainvoke(
            RedesignAgentInput(
                image_url=data_result.image_url,
                feasibility_brief=feasibility_result,
            ),
            to_type=RedesignOutput,
        )

        # ── Step 5: Final synthesis with Nemotron Super ────────────────────
        synthesis_prompt = _build_synthesis_prompt(
            query, data_result, hazard_result, feasibility_result
        )
        messages = [
            SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
            HumanMessage(content=synthesis_prompt),
        ]
        response = await llm.ainvoke(messages)
        summary = response.content if isinstance(response.content, str) else str(response.content)

        # Persist the FLUX redesign so it can be viewed / served to the dashboard.
        image_path = _save_redesign_image(redesign_result, config.location_name)
        if image_path:
            summary = f"{summary}\n\nRedesign image saved to: {image_path}"

        return OrchestratorOutput(summary=summary, redesign=redesign_result)

    yield FunctionInfo.create(
        single_fn=_run,
        description="Top-level LondonZero agent — coordinates the full road safety analysis pipeline.",
        single_output_schema=OrchestratorOutput,
        # nat run (console front end) needs to render the output as text.
        converters=[_orchestrator_output_to_str],
    )


def _orchestrator_output_to_str(output: OrchestratorOutput) -> str:
    """Render the structured output as text for the nat run console front end."""
    return output.summary


def _save_redesign_image(redesign: RedesignOutput | None, location_name: str) -> str | None:
    """Decode the FLUX base64 redesign and write it to outputs/ for viewing."""
    if redesign is None or not redesign.redesigned_image_b64:
        return None
    import base64
    from pathlib import Path

    slug = re.sub(r"[^a-z0-9]+", "_", location_name.lower()).strip("_") or "junction"
    out_dir = Path.cwd() / "outputs"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"redesign_{slug}.png"
    try:
        path.write_bytes(base64.b64decode(redesign.redesigned_image_b64))
        return str(path)
    except Exception as exc:  # noqa: BLE001 — saving is best-effort
        logger.warning("Could not save redesign image: %s", exc)
        return None


def _build_synthesis_prompt(query, data_result, hazard_result, feasibility_result) -> str:
    return (
        f"User question: {query}\n\n"
        f"Location: {data_result.collision_profile.location}\n"
        f"Collisions: {data_result.collision_profile.total_collisions} total "
        f"({data_result.collision_profile.fatal} fatal, "
        f"{data_result.collision_profile.serious} serious)\n"
        f"Cyclist involvement: {data_result.collision_profile.cyclist_involved_pct:.0%}\n\n"
        f"Identified hazards:\n" + "\n".join(f"- {h}" for h in hazard_result.hazards) + "\n\n"
        f"Recommended intervention: {feasibility_result.recommended_intervention}\n"
        f"Design brief: {feasibility_result.design_brief}\n\n"
        f"Confidence notes: {feasibility_result.confidence_notes}\n\n"
        "Synthesise the above into a clear, plain-English recommendation for a city planner. "
        "Cite evidence. Do not overclaim causality."
    )
