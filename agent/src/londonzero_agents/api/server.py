"""
FastAPI server — minimal entrypoint for the LondonZero agent API.

Adapted from vss_agents/api/custom_fastapi_worker.py.
Exposes:
  POST /analyse   — run the full pipeline for a location
  GET  /health    — liveness check

TODO: add streaming endpoint (/analyse/stream) when LangGraph streaming is wired.
TODO: add session replay endpoint for voice/ElevenLabs integration (stretch).
"""

import logging
import os

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from londonzero_agents.data_models.location import LocationQuery
from londonzero_agents.data_models.redesign_output import RedesignOutput

logger = logging.getLogger(__name__)

app = FastAPI(title="LondonZero Road Safety Agent", version="0.1.0")

# Load default location from config
_config_path = os.path.join(os.path.dirname(__file__), "../../../../config/locations.yaml")
with open(_config_path) as f:
    _loc_cfg = yaml.safe_load(f)["default_location"]

DEFAULT_LOCATION = LocationQuery(
    name=_loc_cfg["name"],
    lat=_loc_cfg["lat"],
    lon=_loc_cfg["lon"],
    radius_m=_loc_cfg["radius_m"],
)


class AnalyseRequest(BaseModel):
    query: str = "Why is this junction risky and what would make it safer?"
    # TODO: accept location from UI heatmap click; defaults to Bank Junction for MVP
    location: LocationQuery = DEFAULT_LOCATION


class AnalyseResponse(BaseModel):
    summary: str
    redesign: RedesignOutput | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyse", response_model=AnalyseResponse)
async def analyse(request: AnalyseRequest):
    # TODO: initialise OrchestratorConfig from env/config and cache it at startup
    # For now this raises NotImplementedError so the skeleton is testable
    raise HTTPException(
        status_code=501,
        detail="Orchestrator not yet wired to server — implement startup config in server.py",
    )
