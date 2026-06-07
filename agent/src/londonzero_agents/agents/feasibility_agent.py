"""
Urban Infrastructure & Intervention Feasibility Agent - owned by He Xiao (HX).

Responsibilities:
  - Receive CollisionProfile + HazardAssessment from orchestrator
  - Derive a planning-level feasibility brief from the evidence already on hand
  - Optionally refine the wording with an LLM, without changing deterministic fields
  - Produce FeasibilityBrief for the redesign agent

Interface contract (do not change input/output model names without coordinating
with orchestrator_agent.py which calls this function by name):
  Input:  FeasibilityAgentInput
  Output: FeasibilityBrief
"""

from collections.abc import AsyncGenerator
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef, LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.hazard_assessment import HazardAssessment
from londonzero_agents.prompt import FEASIBILITY_SYSTEM_PROMPT
from londonzero_agents.tools.guidance_rag import GuidanceRagInput, GuidanceRagOutput

logger = logging.getLogger(__name__)


class FeasibilityAgentConfig(FunctionBaseConfig, name="feasibility_agent"):
    llm_name: LLMRef = Field(..., description="LLM for feasibility reasoning (lighter Nemotron)")
    guidance_tool: FunctionRef = Field(
        default="guidance_rag",
        description="RAG tool that retrieves TfL / London planning guidance to ground the brief",
    )
    use_rag: bool = Field(
        default=True,
        description="Whether to retrieve and apply planning guidance via the RAG tool",
    )
    use_llm: bool = Field(
        default=True,
        description="Whether to apply an optional LLM wording pass over the brief",
    )


class FeasibilityAgentInput(BaseModel):
    collision_profile: CollisionProfile
    hazard_assessment: HazardAssessment


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _derive_risk_factors(profile: CollisionProfile, hazards: HazardAssessment) -> list[str]:
    factors = list(hazards.hazards)
    factors.extend(hazards.missing_infrastructure)
    factors.extend(hazards.visibility_issues)

    if profile.fatal > 0:
        factors.append(f"{profile.fatal} fatal collision(s) recorded")
    if profile.serious > 0:
        factors.append(f"{profile.serious} serious collision(s) recorded")
    if profile.cyclist_involved_pct >= 0.2:
        factors.append(f"Cyclists involved in {profile.cyclist_involved_pct:.0%} of collisions")
    if profile.pedestrian_involved_pct >= 0.2:
        factors.append(f"Pedestrians involved in {profile.pedestrian_involved_pct:.0%} of collisions")
    if profile.dominant_manoeuvre:
        factors.append(f"Dominant manoeuvre: {profile.dominant_manoeuvre}")
    if hazards.junction_complexity:
        factors.append(f"Perceived junction complexity: {hazards.junction_complexity}")

    return _dedupe(factors)


def _derive_constraints(profile: CollisionProfile, hazards: HazardAssessment) -> list[str]:
    constraints: list[str] = []

    for key, value in list(profile.osm_context.items())[:5]:
        label = key.replace("_", " ").strip()
        if isinstance(value, bool):
            if value:
                constraints.append(label)
        elif value not in (None, ""):
            constraints.append(f"{label}: {value}")

    if hazards.missing_infrastructure:
        constraints.extend(f"missing infrastructure: {item}" for item in hazards.missing_infrastructure)
    if hazards.visibility_issues:
        constraints.extend(f"visibility issue: {item}" for item in hazards.visibility_issues)
    if hazards.junction_complexity:
        constraints.append(f"junction complexity: {hazards.junction_complexity}")

    if not constraints:
        constraints.append("No explicit infrastructure constraints were supplied in the input models")

    return _dedupe(constraints)


def _compute_feasibility_score(profile: CollisionProfile, hazards: HazardAssessment) -> float:
    score = 0.84

    if profile.total_collisions >= 15:
        score -= 0.18
    elif profile.total_collisions >= 8:
        score -= 0.12
    elif profile.total_collisions >= 4:
        score -= 0.06

    score -= min(0.12, 0.05 * profile.fatal)
    score -= min(0.12, 0.03 * profile.serious)
    score -= min(0.08, 0.02 * len(hazards.hazards))

    if profile.cyclist_involved_pct >= 0.2:
        score -= 0.05
    if profile.pedestrian_involved_pct >= 0.2:
        score -= 0.04
    if hazards.junction_complexity == "High":
        score -= 0.07
    elif hazards.junction_complexity == "Medium":
        score -= 0.03

    return round(max(0.35, min(0.9, score)), 2)


def _select_intervention(profile: CollisionProfile, hazards: HazardAssessment) -> str:
    missing = " ".join(hazards.missing_infrastructure).lower()
    cyclist_pressure = profile.cyclist_involved_pct >= 0.2 or any(
        token in missing for token in ("cycle", "bike", "lane")
    )
    pedestrian_pressure = profile.pedestrian_involved_pct >= 0.2 or any(
        token in missing for token in ("crossing", "pedestrian", "footway")
    )
    complex_junction = (hazards.junction_complexity or "").lower() == "high"

    if cyclist_pressure:
        return "Protected cycle separation and turn management"
    if pedestrian_pressure and complex_junction:
        return "Pedestrian priority and crossing simplification"
    if complex_junction or profile.total_collisions >= 10:
        return "Junction simplification and movement separation"
    return "Vulnerable road user priority treatment"


def _build_design_brief(
    profile: CollisionProfile,
    risk_factors: list[str],
    constraints: list[str],
    intervention: str,
) -> str:
    risk_text = "; ".join(risk_factors[:4]) if risk_factors else "the available collision and hazard evidence"
    constraint_text = "; ".join(constraints[:3]) if constraints else "the supplied junction context"

    return (
        f"Concept-level {intervention.lower()} for {profile.location}. "
        f"The brief should respond to {profile.total_collisions} recorded collisions "
        f"({profile.fatal} fatal, {profile.serious} serious) and the following risks: {risk_text}. "
        f"Respect these constraints: {constraint_text}. "
        "Show clearer pedestrian space, safer movement paths, and a more legible junction layout. "
        "This is planning support only and is not final engineering design."
    )


def _build_plain_explanation(profile: CollisionProfile, intervention: str, feasibility_score: float) -> str:
    return (
        f"Based on the available evidence at {profile.location}, the junction shows enough risk "
        f"to justify a planning-level feasibility score of {feasibility_score:.0%}. "
        f"The preferred concept is {intervention.lower()}, because it best matches the recorded "
        "collision pattern and the observed street-level hazards."
    )


def _build_confidence_notes(profile: CollisionProfile, hazards: HazardAssessment, llm_used: bool) -> str:
    notes = [
        "Collision records describe association, not causation.",
        "Feasibility scoring is heuristic and planning-level only.",
        "Visual outputs are conceptual and not construction-ready.",
    ]

    if profile.raw:
        notes.append("Raw collision context was available for downstream audit.")
    if hazards.raw_vlm_response:
        notes.append("Perception agent supplied a raw VLM response for auditability.")
    if llm_used:
        notes.append("Optional LLM wording pass applied; deterministic fields remain source of truth.")
    else:
        notes.append("Deterministic rule-based reasoning used without LLM refinement.")

    return " ".join(notes)


def _build_guidance_query(
    profile: CollisionProfile,
    hazards: HazardAssessment,
    intervention: str,
) -> str:
    parts = [intervention, f"{profile.location} junction safety intervention"]
    if profile.cyclist_involved_pct >= 0.2:
        parts.append("cyclist protection separation cycle lane")
    if profile.pedestrian_involved_pct >= 0.2:
        parts.append("pedestrian crossing visibility")
    if (hazards.junction_complexity or "").lower() == "high":
        parts.append("complex multi-arm junction simplification")
    parts.extend(hazards.missing_infrastructure[:3])
    return ". ".join(p for p in parts if p)


def _build_llm_refinement_prompt(
    profile: CollisionProfile,
    hazards: HazardAssessment,
    risk_factors: list[str],
    constraints: list[str],
    intervention: str,
    feasibility_score: float,
    design_brief: str,
    plain_explanation: str,
    guidance_text: str = "",
) -> str:
    guidance_block = (
        f"\nRelevant planning guidance (TfL / LCDS / City of London):\n{guidance_text}\n"
        if guidance_text
        else ""
    )
    return (
        f"Location: {profile.location}\n"
        f"Collision summary: {profile.total_collisions} total, {profile.fatal} fatal, {profile.serious} serious, {profile.slight} slight.\n"
        f"Cyclist involvement: {profile.cyclist_involved_pct:.0%}\n"
        f"Pedestrian involvement: {profile.pedestrian_involved_pct:.0%}\n"
        f"Junction complexity: {hazards.junction_complexity or 'unknown'}\n"
        f"Risk factors: {', '.join(risk_factors) if risk_factors else 'none'}\n"
        f"Constraints: {', '.join(constraints) if constraints else 'none'}\n"
        f"Selected intervention: {intervention}\n"
        f"Feasibility score: {feasibility_score:.2f}\n"
        f"{guidance_block}\n"
        f"Draft design brief: {design_brief}\n"
        f"Draft explanation: {plain_explanation}\n\n"
        "Rewrite the design brief in concise planning language for a downstream visual redesign agent. "
        "Where the planning guidance above is relevant, align the brief with it. "
        "Do not invent new facts, do not change the intervention, and do not return JSON. "
        "Keep the result to 4-6 sentences and avoid bullet points."
    )


@register_function(config_type=FeasibilityAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def run_feasibility_agent(
    config: FeasibilityAgentConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    guidance_fn = await builder.get_function(config.guidance_tool) if config.use_rag else None

    async def _run(input: FeasibilityAgentInput) -> FeasibilityBrief:
        risk_factors = _derive_risk_factors(input.collision_profile, input.hazard_assessment)
        constraints = _derive_constraints(input.collision_profile, input.hazard_assessment)
        feasibility_score = _compute_feasibility_score(input.collision_profile, input.hazard_assessment)
        recommended_intervention = _select_intervention(input.collision_profile, input.hazard_assessment)

        design_brief = _build_design_brief(
            input.collision_profile,
            risk_factors,
            constraints,
            recommended_intervention,
        )
        plain_explanation = _build_plain_explanation(
            input.collision_profile,
            recommended_intervention,
            feasibility_score,
        )

        # ── RAG: ground the brief in TfL / London planning guidance ──────────
        guidance_citations: list[str] = []
        guidance_text = ""
        if guidance_fn is not None:
            try:
                query = _build_guidance_query(
                    input.collision_profile, input.hazard_assessment, recommended_intervention
                )
                rag_result = await guidance_fn.ainvoke(
                    GuidanceRagInput(query=query), to_type=GuidanceRagOutput
                )
                guidance_citations = [
                    f"{s.source}: {s.text}" for s in rag_result.snippets
                ]
                guidance_text = "\n".join(
                    f"- ({s.source}) {s.text}" for s in rag_result.snippets
                )
            except Exception as exc:  # pragma: no cover - environment-specific
                logger.warning("feasibility_agent: guidance retrieval skipped: %s", exc)

        llm_used = False
        if config.use_llm:
            try:
                response = await llm.ainvoke(
                    [
                        SystemMessage(content=FEASIBILITY_SYSTEM_PROMPT),
                        HumanMessage(
                            content=_build_llm_refinement_prompt(
                                input.collision_profile,
                                input.hazard_assessment,
                                risk_factors,
                                constraints,
                                recommended_intervention,
                                feasibility_score,
                                design_brief,
                                plain_explanation,
                                guidance_text,
                            )
                        ),
                    ]
                )
                response_text = response.content if isinstance(response.content, str) else str(response.content)
                refined_text = response_text.strip()
                if refined_text:
                    design_brief = refined_text
                    llm_used = True
            except Exception as exc:  # pragma: no cover - network/model failures are environment-specific
                logger.warning("feasibility_agent: LLM refinement skipped: %s", exc)

        return FeasibilityBrief(
            risk_factors=risk_factors,
            infrastructure_constraints=constraints,
            feasibility_score=feasibility_score,
            recommended_intervention=recommended_intervention,
            design_brief=design_brief,
            plain_explanation=plain_explanation,
            confidence_notes=_build_confidence_notes(input.collision_profile, input.hazard_assessment, llm_used),
            guidance_citations=guidance_citations,
        )

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Assess infrastructure intervention feasibility from collision evidence "
            "and street-level hazards. Returns a design brief for the road redesign agent."
        ),
        input_schema=FeasibilityAgentInput,
        single_output_schema=FeasibilityBrief,
    )
