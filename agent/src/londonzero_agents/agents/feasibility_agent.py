"""
Urban Infrastructure & Intervention Feasibility Agent — owned by He Xiao (HX).

Responsibilities:
  - Receive CollisionProfile + HazardAssessment from orchestrator
  - Apply RAG over City of London policy docs, OSM constraints, CID cycling data
  - Produce FeasibilityBrief: risk factors, constraints, intervention type, design_brief
  - design_brief feeds directly into the FLUX inpainting prompt

Interface contract (do not change input/output model names without coordinating
with orchestrator_agent.py which calls this function by name):
  Input:  FeasibilityAgentInput
  Output: FeasibilityBrief

# TODO (He Xiao): implement RAG retrieval and LLM reasoning below.
# The stub returns a placeholder FeasibilityBrief so the pipeline can be tested end-to-end.
"""

import logging

from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.hazard_assessment import HazardAssessment

logger = logging.getLogger(__name__)


class FeasibilityAgentConfig(FunctionBaseConfig, name="feasibility_agent"):
    llm_name: LLMRef = Field(..., description="LLM for feasibility reasoning (lighter Nemotron)")
    # TODO (He Xiao): add RAG config — vector store path, embedding model, top-k
    rag_data_dir: str = Field(
        default="data/rag",
        description="Directory containing policy docs, CID data, PTAL data for RAG",
    )


class FeasibilityAgentInput(BaseModel):
    collision_profile: CollisionProfile
    hazard_assessment: HazardAssessment


@register_function(
    FunctionInfo(
        name="feasibility_agent",
        description=(
            "Assess infrastructure intervention feasibility from collision evidence "
            "and street-level hazards. Returns a design brief for the road redesign agent."
        ),
    )
)
async def run_feasibility_agent(
    config: FeasibilityAgentConfig,
    input: FeasibilityAgentInput,
) -> FeasibilityBrief:
    # TODO (He Xiao): implement RAG retrieval and LLM call
    # Suggested steps:
    #   1. Embed collision_profile + hazard_assessment summary
    #   2. Retrieve relevant policy/constraint chunks from rag_data_dir
    #   3. Call LLM with collision + hazard + retrieved context
    #   4. Parse structured FeasibilityBrief from response

    logger.warning("feasibility_agent: stub — returning placeholder brief")
    return FeasibilityBrief(
        risk_factors=input.hazard_assessment.hazards,
        infrastructure_constraints=["stub constraint — implement RAG"],
        feasibility_score=0.0,
        recommended_intervention="stub — implement feasibility_agent",
        design_brief=(
            f"Road safety redesign at {input.collision_profile.location}. "
            "Add protected cycle lanes and improve pedestrian crossing visibility."
        ),
        plain_explanation="Stub explanation — He Xiao's agent will populate this.",
        confidence_notes="Stub output — data pipeline not yet connected.",
    )
