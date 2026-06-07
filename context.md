# LondonZero — Context

AI-powered road-safety analysis platform for London junctions. A multi-agent pipeline ingests
real collision data, "sees" the street, assesses intervention feasibility, and generates an
AI "after" redesign image — giving city planners evidence-based recommendations. NVIDIA
hackathon project; MVP target is **Bank Junction** (51.5133, -0.0886), the City's worst
casualty hotspot.

## Stack

- **Backend:** Python 3.13, NVIDIA NeMo Agent Toolkit (`nvidia-nat==1.5.0a`), LangGraph,
  FastAPI/Uvicorn, `uv`. Models via NVIDIA API Catalog (build.nvidia.com); some run locally
  on the DGX Spark / GB10.
- **Frontend:** Next.js 15 + React 18 + TypeScript, Tailwind (dark NVIDIA-green theme),
  React-Leaflet (Esri satellite tiles). Turbo monorepo.
- **Data:** DfT STATS19 collisions (`data/stats19/`), OpenStreetMap PBF (`data/osm/`, parsed
  with pyosmium), Mapillary street imagery, mock TfL/London planning guidance for RAG.

## Pipeline (the core flow)

`stream_pipeline()` in `agent/src/londonzero_agents/pipeline.py` is the single source of truth.
Five stages, streamed to the UI as SSE `stage` events:

1. **data_retrieval_agent** — STATS19 collisions + best Mapillary image + OSM context → `CollisionProfile` + image_url (the "before"). Tools: `load_collision_data`, `mapillary_search`, `aggregate_context`. (no LLM)
2. **perception_agent** — VLM sees the image *grounded by the collision profile* → `HazardAssessment`. Tool: `image_understanding`. Model: `nemotron-nano-12b-v2-vl`.
3. **feasibility_agent** — RAG over guidance docs → `FeasibilityBrief` (risk factors, constraints, recommended intervention, citations). Tool: `guidance_rag`. Model: `llama-3.3-nemotron-super-49b-v1`.
4. **orchestrator** — reasons over collision + hazards + feasibility brief → **final recommendation**. Model: `llama-3.3-nemotron-super-49b-v1`.
5. **redesign_agent** — orchestrator recommendation drives **FLUX.1-Fill-dev** (local on GB10, full-image inpaint, no mask) → `RedesignOutput` (the "after" image, base64). Tool: `flux_inpaint`.

## Key files

| Path | Purpose |
|------|---------|
| `agent/configs/londonzero.yml` | Master NAT config: agents, tools, LLMs, workflow |
| `agent/src/londonzero_agents/pipeline.py` | Pipeline orchestration (SSE + single-shot) |
| `agent/src/londonzero_agents/api/server.py` | FastAPI: `POST /analyse/stream` (SSE), `POST /analyse`, `GET /health` |
| `agent/src/londonzero_agents/{agents,tools,data_models}/` | Agents, tools, Pydantic contracts |
| `nim-compose.yml` | Local NIM microservices (embed/vlm/orch/feas) on shared 128GB |
| `ui/apps/londonzero-ui/pages/index.tsx` | Frontend entry: location select + streaming analysis |
| `ui/apps/londonzero-ui/components/Dashboard.tsx` | 3-column dashboard, tabs (Overview/Risk/Evidence/Recommendations) |
| `ui/apps/londonzero-ui/components/JunctionMap.tsx` | Leaflet map (Bank Junction) |
| `ui/apps/londonzero-ui/lib/api.ts` | SSE client (`analyseStream`) |
| `config/locations.yaml`, `config/models.yaml` | Bank Junction coords; model IDs |

## Run

```bash
# Backend (from agent/)
set -a && . ../.env && set +a
uv run --no-sync nat run --config_file configs/londonzero.yml --input "Why is this junction risky?"
# or the API server: uvicorn londonzero_agents.api.server:app --port 8000

# Frontend (from ui/apps/londonzero-ui/)
npm run dev   # localhost:3000 → talks to API at host:8000
```

Env: `NVIDIA_API_KEY`, `MAPILLARY_ACCESS_TOKEN`, optional `*_BASE_URL` overrides for local NIMs.

## Notes / gotchas

- Several catalog models 404 on this key (`cosmos-reason2-8b`, `vila`, nemotron-ultra-253b);
  working substitutes are wired into `londonzero.yml` (not `config/models.yaml`).
- FLUX runs locally on GB10 (aarch64, CUDA 13, torch pinned to cu130 index); ~32GB cached,
  30 steps ≈ 45s.
- Map click is cosmetic for now — orchestrator builds `LocationQuery` from config (Bank locked).
- Team: Jas/Balmee (data retrieval), Nishit (perception + redesign), He Xiao (feasibility),
  Tim (orchestrator).
- Known open items: CLIP 77-token prompt overflow in inpaint; saved image is JPEG bytes with `.png` name.
</content>
</invoke>
