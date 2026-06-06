"""
Jas skill 1 — Load STATS19 collision data from DfT download.

Reads cached CSV extracts (DfT "last-5-years" open data) from:
  https://www.gov.uk/government/statistical-data-sets/road-safety-open-data

The DfT publishes three linked tables joined on ``collision_index``:
  - collision-*.csv : one row per collision (location, severity, junction detail)
  - casualty-*.csv  : one row per casualty  (casualty_type → cyclist / pedestrian)
  - vehicle-*.csv   : one row per vehicle   (vehicle_type, vehicle_manoeuvre)

This tool filters collisions to a radius around the requested location, then
enriches each collision with cyclist / pedestrian / manoeuvre flags derived from
the casualty + vehicle tables, so ``aggregate_context`` can tally without
re-reading the raw files.
"""

import glob
import logging
import math
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

# Repo root = .../agent/src/londonzero_agents/tools/load_collision_data.py → parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _resolve_data_dir(data_dir: str) -> Path:
    """Resolve data_dir against CWD, then the repo root, so the tool works
    regardless of where the process was launched."""
    p = Path(data_dir)
    if p.is_absolute() and p.exists():
        return p
    for base in (Path.cwd(), _REPO_ROOT):
        cand = base / data_dir
        if cand.exists():
            return cand
    return _REPO_ROOT / data_dir  # best-effort; caller will get a clear FileNotFound


def _find_one(data_dir: Path, prefix: str) -> Path:
    matches = sorted(glob.glob(str(data_dir / f"{prefix}*.csv")))
    if not matches:
        raise FileNotFoundError(
            f"No STATS19 '{prefix}*.csv' found in {data_dir}. "
            "Download DfT open data into this directory (see module docstring)."
        )
    return Path(matches[0])


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular approximation — accurate to well within a metre at junction scale."""
    dlat = (lat2 - lat1) * 111_000.0
    dlon = (lon2 - lon1) * 111_000.0 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlon)


def _safe_int(val) -> int | None:
    import pandas as pd

    try:
        if val is None or pd.isna(val):
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


class LoadCollisionDataConfig(FunctionBaseConfig, name="load_collision_data"):
    data_dir: str = Field(
        default="data/stats19",
        description="Local path to downloaded STATS19 CSV files (collision/casualty/vehicle).",
    )
    local_authority_code: str = Field(
        default="E09000001",
        description="ONS local authority code — default is City of London. Used as a coarse pre-filter.",
    )


class LoadCollisionDataInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2019)
    year_to: int = Field(default=2024)


class LoadCollisionDataOutput(BaseModel):
    raw_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Enriched STATS19 collision rows within radius of the requested location",
    )
    record_count: int = 0


def _load_filtered(
    config: LoadCollisionDataConfig, input: LoadCollisionDataInput
) -> list[dict[str, Any]]:
    import pandas as pd

    data_dir = _resolve_data_dir(config.data_dir)
    loc = input.location

    collision_csv = _find_one(data_dir, "collision")
    casualty_csv = _find_one(data_dir, "casualty")
    vehicle_csv = _find_one(data_dir, "vehicle")

    # ── 1. Collisions: filter by year + spatial radius ────────────────────────
    coll = pd.read_csv(collision_csv, low_memory=False)
    coll = coll.dropna(subset=["latitude", "longitude"])
    coll = coll[
        (coll["collision_year"] >= input.year_from) & (coll["collision_year"] <= input.year_to)
    ]
    # Coarse pre-filter by LA code when it actually contains the junction; a junction
    # on a boundary may fall outside, so only narrow to the LA if we find rows in range.
    if config.local_authority_code and "local_authority_ons_district" in coll.columns:
        la = coll[coll["local_authority_ons_district"] == config.local_authority_code]
        if not la.empty:
            in_range = la.apply(
                lambda r: _haversine_m(loc.lat, loc.lon, r["latitude"], r["longitude"])
                <= loc.radius_m,
                axis=1,
            )
            if in_range.any():
                coll = la

    coll = coll.copy()
    coll["_dist_m"] = coll.apply(
        lambda r: _haversine_m(loc.lat, loc.lon, r["latitude"], r["longitude"]), axis=1
    )
    coll = coll[coll["_dist_m"] <= loc.radius_m]

    if coll.empty:
        logger.warning(
            "load_collision_data: no collisions within %dm of %s (%.4f, %.4f) for %d–%d",
            loc.radius_m, loc.name, loc.lat, loc.lon, input.year_from, input.year_to,
        )
        return []

    idx = set(coll["collision_index"].astype(str))

    # ── 2. Casualties for these collisions → cyclist / pedestrian flags ───────
    cas = pd.read_csv(
        casualty_csv,
        usecols=["collision_index", "casualty_class", "casualty_type"],
        low_memory=False,
    )
    cas = cas[cas["collision_index"].astype(str).isin(idx)]
    cas_by_coll: dict[str, dict[str, Any]] = {}
    for cidx, grp in cas.groupby(cas["collision_index"].astype(str)):
        types = grp["casualty_type"].tolist()
        classes = grp["casualty_class"].tolist()
        cas_by_coll[cidx] = {
            "casualty_count": int(len(grp)),
            # casualty_type 1 = Cyclist, 0 = Pedestrian; casualty_class 3 = Pedestrian
            "cyclist_casualty": any(int(t) == 1 for t in types if pd.notna(t)),
            "pedestrian_casualty": (
                any(int(t) == 0 for t in types if pd.notna(t))
                or any(int(c) == 3 for c in classes if pd.notna(c))
            ),
        }

    # ── 3. Vehicles for these collisions → pedal-cycle flag + manoeuvres ──────
    veh = pd.read_csv(
        vehicle_csv,
        usecols=["collision_index", "vehicle_type", "vehicle_manoeuvre"],
        low_memory=False,
    )
    veh = veh[veh["collision_index"].astype(str).isin(idx)]
    veh_by_coll: dict[str, dict[str, Any]] = {}
    for cidx, grp in veh.groupby(veh["collision_index"].astype(str)):
        vtypes = grp["vehicle_type"].tolist()
        man = [int(m) for m in grp["vehicle_manoeuvre"].tolist() if pd.notna(m) and int(m) > 0]
        veh_by_coll[cidx] = {
            "pedal_cycle_involved": any(int(t) == 1 for t in vtypes if pd.notna(t)),
            "manoeuvres": man,
        }

    # ── 4. Assemble enriched records ──────────────────────────────────────────
    records: list[dict[str, Any]] = []
    for _, row in coll.iterrows():
        cidx = str(row["collision_index"])
        c = cas_by_coll.get(cidx, {})
        v = veh_by_coll.get(cidx, {})
        records.append(
            {
                "collision_index": cidx,
                "collision_year": int(row["collision_year"]),
                "collision_severity": int(row["collision_severity"]),  # 1=fatal 2=serious 3=slight
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "distance_m": round(float(row["_dist_m"]), 1),
                "speed_limit": _safe_int(row.get("speed_limit")),
                "junction_detail": _safe_int(row.get("junction_detail")),
                "junction_control": _safe_int(row.get("junction_control")),
                "number_of_vehicles": _safe_int(row.get("number_of_vehicles")),
                "number_of_casualties": _safe_int(row.get("number_of_casualties")),
                "casualty_count": c.get("casualty_count", 0),
                "cyclist_involved": bool(c.get("cyclist_casualty") or v.get("pedal_cycle_involved")),
                "pedestrian_involved": bool(c.get("pedestrian_casualty")),
                "manoeuvres": v.get("manoeuvres", []),
            }
        )
    return records


@register_function(config_type=LoadCollisionDataConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def load_collision_data(
    config: LoadCollisionDataConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo]:
    async def _run(input: LoadCollisionDataInput) -> LoadCollisionDataOutput:
        import asyncio

        # pandas + CSV reads are blocking and CPU-bound — keep the event loop free.
        records = await asyncio.to_thread(_load_filtered, config, input)
        logger.info(
            "load_collision_data: %d collisions within %dm of %s",
            len(records), input.location.radius_m, input.location.name,
        )
        return LoadCollisionDataOutput(raw_records=records, record_count=len(records))

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Load raw STATS19 collision records from DfT CSV extracts "
            "filtered to a radius around a given location, enriched with "
            "cyclist / pedestrian / manoeuvre flags from the casualty + vehicle tables."
        ),
        input_schema=LoadCollisionDataInput,
        single_output_schema=LoadCollisionDataOutput,
    )
