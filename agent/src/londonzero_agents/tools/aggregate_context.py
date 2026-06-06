"""
Jas skill 3 — Aggregate collision records + OSM context into a structured summary JSON.

Takes the enriched STATS19 records from ``load_collision_data`` and the local
Geofabrik OSM extract, and produces a ``CollisionProfile`` that every downstream
agent consumes:
  - severity tallies (fatal / serious / slight)
  - cyclist & pedestrian involvement percentages
  - dominant vehicle manoeuvre
  - real road-layout context near the junction from OpenStreetMap
    (traffic signals, pedestrian crossings, cycle infrastructure, road names/lanes)
"""

import logging
import math
from collections import Counter
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]

# STATS19 vehicle_manoeuvre code → human label
_MANOEUVRE_LABELS = {
    1: "Reversing", 2: "Parked", 3: "Waiting to go – held up", 4: "Slowing or stopping",
    5: "Moving off", 6: "U-turn", 7: "Turning left", 8: "Waiting to turn left",
    9: "Turning right", 10: "Waiting to turn right", 11: "Changing lane to left",
    12: "Changing lane to right", 13: "Overtaking moving vehicle (offside)",
    14: "Overtaking static vehicle (offside)", 15: "Overtaking (nearside)",
    16: "Going ahead left-hand bend", 17: "Going ahead right-hand bend",
    18: "Going ahead other",
}


def _resolve_path(p: str) -> Path:
    pp = Path(p)
    if pp.is_absolute() and pp.exists():
        return pp
    for base in (Path.cwd(), _REPO_ROOT):
        cand = base / p
        if cand.exists():
            return cand
    return _REPO_ROOT / p


class AggregateContextConfig(FunctionBaseConfig, name="aggregate_context"):
    osm_pbf_path: str = Field(
        default="data/osm/greater-london-latest.osm.pbf",
        description="Path to Geofabrik Greater London OSM extract",
    )
    osm_radius_m: int = Field(
        default=120,
        description="Radius for OSM feature extraction around the junction (m).",
    )


class AggregateContextInput(BaseModel):
    location: LocationQuery
    raw_records: list[dict[str, Any]] = Field(
        description="Enriched STATS19 rows from load_collision_data"
    )


# ── OSM extraction ────────────────────────────────────────────────────────────
def _extract_osm_context(pbf_path: Path, lat: float, lon: float, radius_m: int) -> dict[str, Any]:
    """Single streaming pass over the local OSM extract, collecting road-layout
    features within ``radius_m`` of the junction."""
    import osmium

    dlat = radius_m / 111_000.0
    dlon = radius_m / (111_000.0 * math.cos(math.radians(lat)))
    min_lat, max_lat = lat - dlat, lat + dlat
    min_lon, max_lon = lon - dlon, lon + dlon

    def in_bbox(la: float, lo: float) -> bool:
        return min_lat <= la <= max_lat and min_lon <= lo <= max_lon

    class _Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.traffic_signals = 0
            self.crossings = 0
            self.bus_stops = 0
            self.cycle_features: set[str] = set()
            self.roads: dict[str, dict[str, Any]] = {}
            self.lanes: list[int] = []
            self.speed_limits: set[str] = set()

        def node(self, n):
            if not n.location.valid() or not in_bbox(n.location.lat, n.location.lon):
                return
            tags = n.tags
            hw = tags.get("highway")
            if hw == "traffic_signals":
                self.traffic_signals += 1
            if hw == "crossing" or "crossing" in tags or tags.get("footway") == "crossing":
                self.crossings += 1
            if hw == "bus_stop":
                self.bus_stops += 1
            if "cycleway" in tags or tags.get("bicycle") == "designated":
                self.cycle_features.add(tags.get("cycleway", "cycle node"))

        def way(self, w):
            tags = w.tags
            hw = tags.get("highway")
            if hw is None:
                return
            # keep only ways with a node inside the bbox
            near = False
            try:
                for nd in w.nodes:
                    if nd.location.valid() and in_bbox(nd.location.lat, nd.location.lon):
                        near = True
                        break
            except Exception:
                return
            if not near:
                return

            if hw in ("cycleway",) or "cycleway" in tags or "cycleway:left" in tags or "cycleway:right" in tags:
                self.cycle_features.add(tags.get("cycleway", hw))

            if hw in (
                "motorway", "trunk", "primary", "secondary", "tertiary",
                "unclassified", "residential", "living_street", "service",
            ):
                name = tags.get("name") or tags.get("ref") or f"<unnamed {hw}>"
                lanes = tags.get("lanes")
                maxspeed = tags.get("maxspeed")
                self.roads.setdefault(name, {"name": name, "highway": hw,
                                             "lanes": lanes, "maxspeed": maxspeed})
                if lanes and lanes.isdigit():
                    self.lanes.append(int(lanes))
                if maxspeed:
                    self.speed_limits.add(maxspeed)

    h = _Handler()
    h.apply_file(str(pbf_path), locations=True)

    return {
        "num_traffic_signals": h.traffic_signals,
        "num_pedestrian_crossings": h.crossings,
        "num_bus_stops": h.bus_stops,
        "has_cycle_infrastructure": bool(h.cycle_features),
        "cycle_infrastructure": sorted(h.cycle_features),
        "nearby_roads": list(h.roads.values()),
        "road_names": sorted(r["name"] for r in h.roads.values() if not r["name"].startswith("<")),
        "max_lanes": max(h.lanes) if h.lanes else None,
        "speed_limits": sorted(h.speed_limits),
        "osm_radius_m": radius_m,
    }


def _aggregate(config: AggregateContextConfig, input: AggregateContextInput) -> CollisionProfile:
    records = input.raw_records
    n = len(records)

    fatal = sum(1 for r in records if r.get("collision_severity") == 1)
    serious = sum(1 for r in records if r.get("collision_severity") == 2)
    slight = sum(1 for r in records if r.get("collision_severity") == 3)

    cyclist = sum(1 for r in records if r.get("cyclist_involved"))
    pedestrian = sum(1 for r in records if r.get("pedestrian_involved"))

    manoeuvre_counts: Counter[int] = Counter()
    for r in records:
        manoeuvre_counts.update(m for m in r.get("manoeuvres", []) if m in _MANOEUVRE_LABELS)
    dominant_manoeuvre = (
        _MANOEUVRE_LABELS[manoeuvre_counts.most_common(1)[0][0]] if manoeuvre_counts else None
    )

    years = [r.get("collision_year") for r in records if r.get("collision_year")]
    year_range = (min(years), max(years)) if years else None

    # ── OSM enrichment ────────────────────────────────────────────────────────
    osm_context: dict[str, Any] = {}
    pbf = _resolve_path(config.osm_pbf_path)
    if pbf.exists():
        try:
            osm_context = _extract_osm_context(
                pbf, input.location.lat, input.location.lon, config.osm_radius_m
            )
        except Exception as exc:  # noqa: BLE001 — OSM is enrichment, never fatal
            logger.warning("aggregate_context: OSM enrichment failed: %s", exc)
            osm_context = {"error": str(exc)}
    else:
        logger.warning("aggregate_context: OSM extract not found at %s — skipping enrichment", pbf)
        osm_context = {"error": f"OSM extract not found at {pbf}"}

    profile = CollisionProfile(
        location=input.location.name,
        total_collisions=n,
        fatal=fatal,
        serious=serious,
        slight=slight,
        cyclist_involved_pct=(cyclist / n) if n else 0.0,
        pedestrian_involved_pct=(pedestrian / n) if n else 0.0,
        dominant_manoeuvre=dominant_manoeuvre,
        year_range=year_range,
        osm_context=osm_context,
        raw={
            "manoeuvre_histogram": {
                _MANOEUVRE_LABELS[k]: v for k, v in manoeuvre_counts.most_common()
            },
            "cyclist_collisions": cyclist,
            "pedestrian_collisions": pedestrian,
            "radius_m": input.location.radius_m,
        },
    )
    return profile


@register_function(config_type=AggregateContextConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def aggregate_context(
    config: AggregateContextConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    async def _run(input: AggregateContextInput) -> CollisionProfile:
        import asyncio

        # OSM parse + tallying are blocking/CPU-bound — keep the event loop free.
        profile = await asyncio.to_thread(_aggregate, config, input)
        logger.info(
            "aggregate_context: %s — %d collisions (%d fatal, %d serious), %.0f%% cyclist",
            profile.location, profile.total_collisions, profile.fatal, profile.serious,
            profile.cyclist_involved_pct * 100,
        )
        return profile

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Aggregate enriched STATS19 collision records and local OSM road context "
            "into a structured CollisionProfile JSON for downstream agents."
        ),
        input_schema=AggregateContextInput,
        single_output_schema=CollisionProfile,
    )
