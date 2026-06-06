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
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.agents.data_retrieval_agent import (
    DataRetrievalAgentConfig,
    DataRetrievalAgentInput,
    run_data_retrieval_agent,
)
from londonzero_agents.agents.feasibility_agent import (
    FeasibilityAgentConfig,
    FeasibilityAgentInput,
    run_feasibility_agent,
)
from londonzero_agents.agents.perception_agent import (
    PerceptionAgentConfig,
    PerceptionAgentInput,
    run_perception_agent,
)
from londonzero_agents.agents.redesign_agent import (
    RedesignAgentConfig,
    RedesignAgentInput,
    run_redesign_agent,
)
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.data_models.redesign_output import RedesignOutput
from londonzero_agents.prompt import ORCHESTRATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# ── Bank Junction default — see config/locations.yaml for coordinates ──
# TODO: replace with dynamic location when UI heatmap is wired up
BANK_JUNCTION = LocationQuery(name="Bank Junction", lat=51.5133, lon=-0.0886, radius_m=100)


class OrchestratorState(TypedDict):
    location: dict
    collision_profile: dict | None
    image_url: str | None
    hazard_assessment: dict | None
    feasibility_brief: dict | None
    redesign_output: dict | None
    final_summary: str | None


class OrchestratorConfig(FunctionBaseConfig, name="orchestrator_agent"):
    llm_name: LLMRef = Field(..., description="Nemotron Super model reference")
    data_retrieval: DataRetrievalAgentConfig = Field(default_factory=DataRetrievalAgentConfig)
    perception: PerceptionAgentConfig = Field(...)
    feasibility: FeasibilityAgentConfig = Field(...)
    redesign: RedesignAgentConfig = Field(default_factory=RedesignAgentConfig)


class OrchestratorInput(BaseModel):
    query: str = Field(description="User's free-text question about the location")
    location: LocationQuery = Field(default=BANK_JUNCTION)


class OrchestratorOutput(BaseModel):
    summary: str
    redesign: RedesignOutput | None = None
    session_id: str | None = None


@register_function(
    FunctionInfo(
        name="orchestrator_agent",
        description="Top-level LondonZero agent — coordinates the full road safety analysis pipeline.",
    )
)
async def run_orchestrator(
    config: OrchestratorConfig,
    input: OrchestratorInput,
) -> OrchestratorOutput:
    llm = Builder.get_llm(LLMFrameworkEnum.LANGCHAIN, config.llm_name)

    # ── Step 1: Data Retrieval ──────────────────────────────────────────────
    logger.info("Orchestrator: running data_retrieval_agent for %s", input.location.name)
    data_result = await run_data_retrieval_agent(
        config.data_retrieval,
        DataRetrievalAgentInput(location=input.location),
    )

    # ── Step 2: Perception ─────────────────────────────────────────────────
    logger.info("Orchestrator: running perception_agent")
    hazard_result = await run_perception_agent(
        config.perception,
        PerceptionAgentInput(
            image_url=data_result.image_url,
            collision_profile=data_result.collision_profile,
        ),
    )

    # ── Step 3: Feasibility ────────────────────────────────────────────────
    logger.info("Orchestrator: running feasibility_agent")
    feasibility_result = await run_feasibility_agent(
        config.feasibility,
        FeasibilityAgentInput(
            collision_profile=data_result.collision_profile,
            hazard_assessment=hazard_result,
        ),
    )

    # ── Step 4: Redesign ───────────────────────────────────────────────────
    logger.info("Orchestrator: running redesign_agent")
    redesign_result = await run_redesign_agent(
        config.redesign,
        RedesignAgentInput(
            image_url=data_result.image_url,
            feasibility_brief=feasibility_result,
        ),
    )

    # ── Step 5: Final synthesis with Nemotron Super ────────────────────────
    synthesis_prompt = _build_synthesis_prompt(
        input.query, data_result, hazard_result, feasibility_result
    )
    messages = [
        SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
        HumanMessage(content=synthesis_prompt),
    ]
    response = await llm.ainvoke(messages)
    summary = response.content if isinstance(response.content, str) else str(response.content)

    return OrchestratorOutput(summary=summary, redesign=redesign_result)


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
