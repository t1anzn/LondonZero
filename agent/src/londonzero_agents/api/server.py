"""
FastAPI server — entrypoint for the LondonZero agent API (plain uvicorn).

The agents themselves are built by the NeMo Agent Toolkit (`nat`): at startup we
load the workflow config, construct a WorkflowBuilder, and resolve the five
sub-agent functions + the orchestrator LLM once. Both endpoints then drive the
shared `stream_pipeline` generator so the streamed dashboard view and the
single-shot result can never diverge.

Exposes:
  POST /analyse/stream — SSE: one structured event per pipeline stage (primary)
  POST /analyse        — full pipeline result in one JSON payload (fallback)
  GET  /health         — liveness check

Run:
  cd agent && set -a && . ../.env && set +a && \
    uv run uvicorn londonzero_agents.api.server:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Import agent + tool packages so their @register_function decorators run before
# the WorkflowBuilder reads the config.
import londonzero_agents.agents  # noqa: F401
import londonzero_agents.tools  # noqa: F401
from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.feasibility_brief import FeasibilityBrief
from londonzero_agents.data_models.hazard_assessment import HazardAssessment
from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.data_models.redesign_output import RedesignOutput
from londonzero_agents.pipeline import stream_pipeline

from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.workflow_builder import WorkflowBuilder
from nat.runtime.loader import load_config

logger = logging.getLogger(__name__)

# ── Paths / config ──────────────────────────────────────────────────────────
_API_DIR = Path(__file__).resolve().parent
_AGENT_DIR = _API_DIR.parents[2]  # api → londonzero_agents → src → agent
_REPO_ROOT = _AGENT_DIR.parent

CONFIG_PATH = os.environ.get("LONDONZERO_CONFIG", str(_AGENT_DIR / "configs" / "londonzero.yml"))

# Sub-agent + LLM names as declared in configs/londonzero.yml
_FUNC_NAMES = {
    "data": "data_retrieval_agent",
    "perception": "perception_agent",
    "feasibility": "feasibility_agent",
    "redesign": "redesign_agent",
}
_ORCHESTRATOR_LLM = "orchestrator_llm"

# Default location (Bank Junction) — the pipeline is locked to it for the MVP.
with open(_REPO_ROOT / "config" / "locations.yaml") as f:
    _loc_cfg = yaml.safe_load(f)["default_location"]

DEFAULT_LOCATION = LocationQuery(
    name=_loc_cfg["name"],
    lat=_loc_cfg["lat"],
    lon=_loc_cfg["lon"],
    radius_m=_loc_cfg["radius_m"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the nat workflow once and keep the builder open for the app lifetime."""
    logger.info("LondonZero: loading workflow config from %s", CONFIG_PATH)
    config = load_config(CONFIG_PATH)
    async with AsyncExitStack() as stack:
        builder = await stack.enter_async_context(WorkflowBuilder.from_config(config))
        app.state.funcs = {
            key: await builder.get_function(name) for key, name in _FUNC_NAMES.items()
        }
        app.state.llm = await builder.get_llm(
            _ORCHESTRATOR_LLM, wrapper_type=LLMFrameworkEnum.LANGCHAIN
        )
        logger.info("LondonZero: agents ready (%s)", ", ".join(_FUNC_NAMES.values()))
        yield
    logger.info("LondonZero: workflow builder torn down")


app = FastAPI(title="LondonZero Road Safety Agent", version="0.2.0", lifespan=lifespan)

# Default to "*" so the UI works regardless of how it's reached (Tailscale IP,
# scan-13.local on the LAN, localhost). No credentials/cookies are used, so this
# is safe. Override with LONDONZERO_CORS_ORIGINS (comma-separated) to lock down.
_cors_origins = os.environ.get("LONDONZERO_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyseRequest(BaseModel):
    query: str = "Why is this junction risky and what would make it safer?"
    location: LocationQuery = DEFAULT_LOCATION


class AnalyseResponse(BaseModel):
    summary: str
    collision_profile: CollisionProfile | None = None
    hazard_assessment: HazardAssessment | None = None
    feasibility_brief: FeasibilityBrief | None = None
    redesign: RedesignOutput | None = None


def _json_default(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def _sse(event_name: str, data: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, default=_json_default)}\n\n"


def _pipeline_kwargs(app: FastAPI, request: AnalyseRequest) -> dict:
    funcs = app.state.funcs
    return dict(
        data_fn=funcs["data"],
        perception_fn=funcs["perception"],
        feasibility_fn=funcs["feasibility"],
        redesign_fn=funcs["redesign"],
        llm=app.state.llm,
        loc=request.location,
        query=request.query,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyse/stream")
async def analyse_stream(request: AnalyseRequest, http_request: Request):
    """Stream the pipeline as SSE — one structured event per stage."""

    async def event_gen():
        try:
            async for event in stream_pipeline(**_pipeline_kwargs(app, request)):
                if await http_request.is_disconnected():
                    logger.info("LondonZero: client disconnected, aborting stream")
                    break
                etype = event["type"]
                if etype == "stage":
                    yield _sse("stage", {"stage": event["stage"], **event["payload"]})
                elif etype == "status":
                    yield _sse("status", {k: v for k, v in event.items() if k != "type"})
                elif etype == "done":
                    yield _sse("done", event["payload"])
        except Exception as exc:  # noqa: BLE001 — surface failure to the client, keep partials
            logger.exception("LondonZero: pipeline error")
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/analyse", response_model=AnalyseResponse)
async def analyse(request: AnalyseRequest):
    """Run the full pipeline and return everything in one payload (fallback)."""
    result: dict = {}
    try:
        async for event in stream_pipeline(**_pipeline_kwargs(app, request)):
            if event["type"] == "done":
                result = event["payload"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("LondonZero: pipeline error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not result:
        raise HTTPException(status_code=500, detail="Pipeline produced no result")

    return AnalyseResponse(**result)
