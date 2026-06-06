"""
Jas skill 3 — Aggregate collision records into a structured CollisionProfile.

Ported from the manual pipeline (utils/collision_fetch.py aggregation stage).
The batch pipeline emitted one HotspotRecord per grid cell; here we collapse the
radius-filtered records for a *single* location into one CollisionProfile that all
downstream agents consume.

First-class CollisionProfile fields are populated where they map cleanly; the
richer STATS19/vehicle context (dominant road type, manoeuvres, propulsion,
year trend, HGV/cyclist counts, …) is stashed in `raw` per the agent contract.

OSM road-layout features are not produced by the source pipeline — osm_context
is left empty (see TODO) until an OSM extractor is wired in.
"""

from collections import Counter
import logging
from typing import Any

from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from londonzero_agents.data_models.collision_profile import CollisionProfile
from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

# Vehicle-type buckets reused from the manual pipeline.
HGV_TYPES = {
    "goods vehicle (over 3.5t and up to 7.5t mgw)",
    "goods vehicle (7.5t mgw and over)",
    "goods vehicle - unknown weight",
}
MOTORCYCLE_KEYWORD = "motorcycle"
CYCLE_TYPE = "pedal cycle"


class AggregateContextConfig(FunctionBaseConfig, name="aggregate_context"):
    osm_pbf_path: str = Field(
        default="data/osm/greater-london-latest.osm.pbf",
        description="Path to Geofabrik Greater London OSM extract (not yet used)",
    )


class AggregateContextInput(BaseModel):
    location: LocationQuery
    raw_records: list[dict[str, Any]] = Field(
        description="STATS19 rows from load_collision_data (each with joined 'vehicles')"
    )


def _dominant(values: list) -> str | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def _top_counts(values: list, n: int = 3) -> list[dict]:
    counts = Counter(v for v in values if v is not None)
    return [{"value": k, "count": c} for k, c in counts.most_common(n)]


def _year_trend(year_counts: dict[int, int]) -> str | None:
    years = sorted(year_counts)
    if len(years) < 3:
        return None
    counts = [year_counts[y] for y in years]
    mid = max(len(counts) // 2, 1)
    first = sum(counts[:mid]) / mid
    last = sum(counts[mid:]) / max(len(counts) - mid, 1)
    delta = last - first
    if delta > first * 0.10:
        return "worsening"
    if delta < -first * 0.10:
        return "improving"
    return "stable"


def _build_vehicle_context(all_vehicles: list[dict]) -> dict:
    """Summarise the joined vehicle rows — port of collision_fetch._build_vehicle_context."""
    if not all_vehicles:
        return {}

    types = [v.get("vehicle_type_label") for v in all_vehicles]
    return {
        "total_vehicles": len(all_vehicles),
        "vehicle_types": _top_counts(types),
        "manoeuvres": _top_counts([v.get("vehicle_manoeuvre_label") for v in all_vehicles]),
        "impact_points": _top_counts([v.get("first_point_of_impact_label") for v in all_vehicles]),
        "propulsion_types": _top_counts([v.get("propulsion_code_label") for v in all_vehicles]),
        "hgv_involved_count": sum(1 for t in types if t in HGV_TYPES),
        "cyclist_involved_count": sum(1 for t in types if t == CYCLE_TYPE),
        "motorcycle_involved_count": sum(1 for t in types if t and MOTORCYCLE_KEYWORD in t),
        "skidding_overturning_count": sum(
            1 for v in all_vehicles if v.get("skidding_and_overturning_label") not in (None, "none")
        ),
    }


async def aggregate_context(
    config: AggregateContextConfig,  # noqa: ARG001 — reserved for OSM enrichment via config.osm_pbf_path
    input: AggregateContextInput,
) -> CollisionProfile:
    """Aggregate raw STATS19 collision records (and OSM road context, when wired)
    into a structured CollisionProfile. Called directly by data_retrieval_agent."""
    records = input.raw_records
    total = len(records)

    if total == 0:
        logger.warning(
            "aggregate_context: no records for %s — returning empty profile",
            input.location.name,
        )
        return CollisionProfile(location=input.location.name, total_collisions=0)

    # ── Severity counts ───────────────────────────────────────────────────────
    sev = [r.get("severity_label") for r in records]
    fatal = sum(1 for s in sev if s == "fatal")
    serious = sum(1 for s in sev if s == "serious")
    slight = sum(1 for s in sev if s == "slight")

    # ── Casualties + year span/trend ──────────────────────────────────────────
    total_casualties = sum(int(r.get("number_of_casualties") or 0) for r in records)
    years = [int(r["collision_year"]) for r in records if r.get("collision_year") is not None]
    year_counts = Counter(years)
    earliest = min(years) if years else None
    latest = max(years) if years else None

    # ── Dominant environmental context ────────────────────────────────────────
    dominant_road_type = _dominant([r.get("road_type_label") for r in records])
    dominant_light = _dominant([r.get("light_conditions_label") for r in records])
    dominant_weather = _dominant([r.get("weather_conditions_label") for r in records])
    dominant_surface = _dominant([r.get("road_surface_conditions_label") for r in records])
    dominant_junction = _dominant([r.get("junction_detail_label") for r in records])
    urban_or_rural = _dominant([r.get("urban_or_rural_area_label") for r in records])

    speed_raw = _dominant([r.get("speed_limit") for r in records])
    try:
        dominant_speed_limit = int(speed_raw) if speed_raw is not None else None
    except (TypeError, ValueError):
        dominant_speed_limit = None

    # ── Vehicle context (flatten joined vehicle rows) ─────────────────────────
    all_vehicles = [v for r in records for v in (r.get("vehicles") or [])]
    vehicle_context = _build_vehicle_context(all_vehicles)

    # Share of collisions (not vehicles) that involved a cyclist.
    collisions_with_cyclist = sum(
        1 for r in records if any((v.get("vehicle_type_label") == CYCLE_TYPE) for v in (r.get("vehicles") or []))
    )
    cyclist_involved_pct = collisions_with_cyclist / total if total else 0.0

    dominant_manoeuvre = vehicle_context["manoeuvres"][0]["value"] if vehicle_context.get("manoeuvres") else None

    profile = CollisionProfile(
        location=input.location.name,
        total_collisions=total,
        fatal=fatal,
        serious=serious,
        slight=slight,
        cyclist_involved_pct=cyclist_involved_pct,
        # Pedestrian breakdown needs the STATS19 casualty table, which the source
        # pipeline does not load — left at 0.0.  # TODO (Jas/Balmee): load casualty CSV.
        pedestrian_involved_pct=0.0,
        dominant_manoeuvre=dominant_manoeuvre,
        year_range=(earliest, latest) if earliest is not None and latest is not None else None,
        # TODO (Jas/Balmee): populate from an OSM extract over osm_pbf_path.
        osm_context={},
        raw={
            "total_casualties": total_casualties,
            "year_trend": _year_trend(dict(year_counts)),
            "year_counts": dict(year_counts),
            "dominant_speed_limit": dominant_speed_limit,
            "dominant_road_type": dominant_road_type,
            "dominant_light": dominant_light,
            "dominant_weather": dominant_weather,
            "dominant_surface": dominant_surface,
            "dominant_junction": dominant_junction,
            "urban_or_rural": urban_or_rural,
            "vehicle_context": vehicle_context,
            "collisions_with_cyclist": collisions_with_cyclist,
            "search_radius_m": input.location.radius_m,
            "center": {"lat": input.location.lat, "lon": input.location.lon},
        },
    )
    logger.info(
        "aggregate_context: %s — %d collisions (%d fatal, %d serious, %d slight)",
        input.location.name,
        total,
        fatal,
        serious,
        slight,
    )
    return profile
