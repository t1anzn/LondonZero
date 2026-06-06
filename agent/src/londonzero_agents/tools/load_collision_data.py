"""
Jas skill 1 — Load STATS19 collision data from DfT download.

Ported from the manual pipeline (utils/collision_fetch.py). The batch pipeline
downloaded the whole of England & Wales and gridded it into national hotspots.
Here we reuse the same download + STATS19 decode + vehicle-join logic, but
filter collisions to a *radius around a single location* instead of gridding.

Each returned record is one decoded collision row with its joined vehicle rows
attached under "vehicles", ready for aggregate_context to summarise.

Source data:
  https://data.dft.gov.uk/road-accidents-safety-data/
  (per-year collision + vehicle CSVs; provisional 2025 also available)

Heavy pandas/requests work runs in a worker thread so the async signature
stays non-blocking. pandas/requests are imported lazily for the same reason.
"""

import asyncio
import logging
import math
from pathlib import Path
from typing import Any

from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from londonzero_agents.data_models.location import LocationQuery

logger = logging.getLogger(__name__)

# ── DfT URLs ──────────────────────────────────────────────────────────────────
DFT_BASE = "https://data.dft.gov.uk/road-accidents-safety-data"
DFT_COLLISION_URL = f"{DFT_BASE}/dft-road-casualty-statistics-collision-{{year}}.csv"
DFT_VEHICLE_URL = f"{DFT_BASE}/dft-road-casualty-statistics-vehicle-{{year}}.csv"
DFT_COLLISION_PROV = f"{DFT_BASE}/dft-road-casualty-statistics-collision-provisional-2025.csv"
DFT_VEHICLE_PROV = f"{DFT_BASE}/dft-road-casualty-statistics-vehicle-provisional-2025.csv"

# ── STATS19 collision decode tables ───────────────────────────────────────────
SEVERITY_MAP = {1: "fatal", 2: "serious", 3: "slight"}
ROAD_TYPE_MAP = {
    1: "roundabout",
    2: "one way street",
    3: "dual carriageway",
    6: "single carriageway",
    7: "slip road",
    9: "unknown",
}
LIGHT_MAP = {
    1: "daylight",
    4: "dark - no street lighting",
    5: "dark - street lights present",
    6: "dark - street lights lit",
    7: "dark - street lights unlit",
}
WEATHER_MAP = {
    1: "fine",
    2: "raining",
    3: "snowing",
    4: "fog or mist",
    5: "high winds",
    6: "snow or hail",
    7: "other",
    8: "unknown",
    9: "unknown",
}
SURFACE_MAP = {
    1: "dry",
    2: "wet or damp",
    3: "snow",
    4: "frost or ice",
    5: "flood",
    9: "unknown",
}
URBAN_MAP = {1: "urban", 2: "rural", 3: "unallocated"}
JUNCTION_MAP = {
    0: "not at junction",
    1: "roundabout",
    2: "mini roundabout",
    3: "T or staggered junction",
    5: "slip road",
    6: "crossroads",
    7: "more than 4 arms",
    8: "private drive",
    9: "other junction",
    13: "T junction",
    16: "signal controlled crossroads",
    17: "mini roundabout",
    18: "roundabout",
    19: "other",
    99: "unknown",
}

# ── STATS19 vehicle decode tables ─────────────────────────────────────────────
VEHICLE_TYPE_MAP = {
    1: "pedal cycle",
    2: "motorcycle 50cc and under",
    3: "motorcycle 125cc and under",
    4: "motorcycle over 125cc and up to 500cc",
    5: "motorcycle over 500cc",
    8: "taxi or private hire car",
    9: "car",
    10: "minibus (8-16 passenger seats)",
    11: "bus or coach (17+ passenger seats)",
    16: "ridden horse",
    17: "agricultural vehicle",
    18: "tram",
    19: "van or goods vehicle (3.5t mgw or under)",
    20: "goods vehicle (over 3.5t and up to 7.5t mgw)",
    21: "goods vehicle (7.5t mgw and over)",
    22: "mobility scooter",
    23: "electric motorcycle",
    90: "other vehicle",
    97: "motorcycle - unknown cc",
    98: "goods vehicle - unknown weight",
}
MANOEUVRE_MAP = {
    1: "reversing",
    2: "parked",
    3: "waiting to go ahead but held up",
    4: "slowing or stopping",
    5: "moving off",
    6: "u-turn",
    7: "turning left",
    8: "waiting to turn left",
    9: "turning right",
    10: "waiting to turn right",
    11: "changing lane to left",
    12: "changing lane to right",
    13: "overtaking moving vehicle on offside",
    14: "overtaking stationary vehicle on offside",
    15: "overtaking on nearside",
    16: "going ahead - left-hand bend",
    17: "going ahead - right-hand bend",
    18: "going ahead - other",
    19: "going ahead",
}
IMPACT_MAP = {0: "did not impact", 1: "front", 2: "back", 3: "offside", 4: "nearside"}
SKIDDING_MAP = {
    0: "none",
    1: "skidded",
    2: "skidded and overturned",
    3: "jackknifed",
    4: "jackknifed and overturned",
    5: "overturned",
}
PROPULSION_MAP = {
    1: "petrol",
    2: "heavy oil (diesel)",
    3: "electric",
    4: "steam",
    5: "gas",
    6: "petrol/gas",
    7: "new fuel technology",
    8: "hybrid electric",
    9: "gas/bi-fuel",
    10: "hydrogen",
    11: "unknown",
}


class LoadCollisionDataConfig(FunctionBaseConfig, name="load_collision_data"):
    data_dir: str = Field(
        default="data/stats19",
        description="Local cache dir for downloaded STATS19 CSV files",
    )
    include_provisional: bool = Field(
        default=True,
        description="Also load the provisional 2025 collision/vehicle extract",
    )
    combined_csv: str | None = Field(
        default=None,
        description=(
            "Optional path to a single pre-combined collision CSV (e.g. the DfT "
            "'last-5-years' file). If set, skips per-year download. Vehicle context "
            "is unavailable in this mode unless combined_vehicle_csv is also given."
        ),
    )
    combined_vehicle_csv: str | None = Field(
        default=None,
        description="Optional path to a pre-combined vehicle CSV paired with combined_csv",
    )
    download_timeout: int = Field(default=120)
    max_retries: int = Field(default=3)


class LoadCollisionDataInput(BaseModel):
    location: LocationQuery
    year_from: int = Field(default=2020)
    year_to: int = Field(default=2024)


class LoadCollisionDataOutput(BaseModel):
    raw_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Decoded STATS19 collisions within radius, each with joined 'vehicles'",
    )
    record_count: int = 0


# ── Download (sync, runs in worker thread) ────────────────────────────────────


def _download_file(url: str, dest: Path, timeout: int, max_retries: int) -> None:
    import time

    import requests

    if dest.exists():
        logger.info("  Already cached: %s", dest.name)
        return
    logger.info("  Downloading %s ...", url)
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            logger.info("  Saved %.1f MB -> %s", dest.stat().st_size / 1e6, dest.name)
            return
        except requests.HTTPError as exc:
            code = exc.response.status_code
            logger.warning("  HTTP %d for %s (attempt %d/%d)", code, url, attempt, max_retries)
            if code == 404:
                logger.warning("  Not found on DfT — skipping")
                return
        except requests.RequestException as exc:
            logger.warning("  Request error: %s (attempt %d/%d)", exc, attempt, max_retries)
        time.sleep(2**attempt)
    logger.error("  Failed after %d attempts: %s", max_retries, url)


def _resolve_csv_paths(cfg: LoadCollisionDataConfig, year_from: int, year_to: int) -> tuple[list[Path], list[Path]]:
    """Ensure per-year collision+vehicle CSVs are cached; download any missing ones."""
    raw_dir = Path(cfg.data_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    years = list(range(year_from, year_to + 1))
    logger.info("Target years: %s (provisional=%s)", years, cfg.include_provisional)

    col_paths, veh_paths = [], []
    for year in years:
        c = raw_dir / f"dft-collision-{year}.csv"
        v = raw_dir / f"dft-vehicle-{year}.csv"
        _download_file(DFT_COLLISION_URL.format(year=year), c, cfg.download_timeout, cfg.max_retries)
        _download_file(DFT_VEHICLE_URL.format(year=year), v, cfg.download_timeout, cfg.max_retries)
        col_paths.append(c)
        veh_paths.append(v)

    if cfg.include_provisional:
        c = raw_dir / "dft-collision-provisional-2025.csv"
        v = raw_dir / "dft-vehicle-provisional-2025.csv"
        _download_file(DFT_COLLISION_PROV, c, cfg.download_timeout, cfg.max_retries)
        _download_file(DFT_VEHICLE_PROV, v, cfg.download_timeout, cfg.max_retries)
        col_paths.append(c)
        veh_paths.append(v)

    return [p for p in col_paths if p.exists()], [p for p in veh_paths if p.exists()]


# ── Load + decode (sync) ──────────────────────────────────────────────────────


def _load_collisions(paths: list[Path]):
    import pandas as pd

    frames = []
    for p in paths:
        try:
            df = pd.read_csv(
                p,
                dtype={
                    "collision_index": str,
                    "lsoa_of_accident_location": str,
                    "local_authority_ons_district": str,
                },
                low_memory=False,
            )
            frames.append(df)
            logger.info("  Collision: %d rows from %s", len(df), p.name)
        except Exception as exc:
            logger.error("  Failed to read %s: %s", p.name, exc)

    combined = pd.concat(frames, ignore_index=True)
    num_cols = combined.select_dtypes(include="number").columns
    combined[num_cols] = combined[num_cols].replace(-1, pd.NA)
    combined = combined.drop_duplicates(subset=["collision_index"], keep="first")

    def _map(col, lookup):
        if col in combined.columns:
            combined[col + "_label"] = combined[col].map(lookup)

    _map("collision_severity", SEVERITY_MAP)
    _map("road_type", ROAD_TYPE_MAP)
    _map("light_conditions", LIGHT_MAP)
    _map("weather_conditions", WEATHER_MAP)
    _map("road_surface_conditions", SURFACE_MAP)
    _map("urban_or_rural_area", URBAN_MAP)
    _map("junction_detail", JUNCTION_MAP)
    combined["severity_label"] = combined.apply(_resolve_severity, axis=1)
    return combined


def _load_vehicles(paths: list[Path]):
    import pandas as pd

    if not paths:
        return pd.DataFrame()
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, dtype={"collision_index": str}, low_memory=False)
            frames.append(df)
            logger.info("  Vehicle: %d rows from %s", len(df), p.name)
        except Exception as exc:
            logger.error("  Failed to read %s: %s", p.name, exc)

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    num_cols = combined.select_dtypes(include="number").columns
    combined[num_cols] = combined[num_cols].replace(-1, pd.NA)

    def _map(col, lookup):
        if col in combined.columns:
            combined[col + "_label"] = combined[col].map(lookup)

    _map("vehicle_type", VEHICLE_TYPE_MAP)
    _map("vehicle_manoeuvre", MANOEUVRE_MAP)
    _map("first_point_of_impact", IMPACT_MAP)
    _map("skidding_and_overturning", SKIDDING_MAP)
    _map("propulsion_code", PROPULSION_MAP)
    return combined


def _resolve_severity(row):
    import pandas as pd  # noqa: F401  (kept local for thread-safety symmetry)

    raw = row.get("collision_severity_label")
    if row.get("collision_injury_based") == 1:
        return raw
    try:
        ps = float(row.get("collision_adjusted_severity_serious") or 0)
        pl = float(row.get("collision_adjusted_severity_slight") or 0)
        if ps > 0 or pl > 0:
            pf = max(0.0, 1.0 - ps - pl)
            return max([("fatal", pf), ("serious", ps), ("slight", pl)], key=lambda x: x[1])[0]
    except (TypeError, ValueError):
        pass
    return raw


# ── Radius filter + vehicle join (sync) ───────────────────────────────────────


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _clean(value):
    """Make a pandas cell JSON-serialisable (NaN/NA -> None)."""
    import pandas as pd

    try:
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def _filter_to_radius(collision_df, vehicle_df, location: LocationQuery) -> list[dict]:

    geo = collision_df.dropna(subset=["latitude", "longitude"]).copy()
    if geo.empty:
        return []

    # Cheap bounding-box prefilter before the per-row haversine.
    radius_m = max(location.radius_m, 1)
    dlat = radius_m / 111_000.0
    dlon = radius_m / (111_000.0 * max(math.cos(math.radians(location.lat)), 1e-6))
    box = geo[
        (geo["latitude"].between(location.lat - dlat, location.lat + dlat))
        & (geo["longitude"].between(location.lon - dlon, location.lon + dlon))
    ].copy()
    if box.empty:
        logger.info("  No collisions in bounding box around %s", location.name)
        return []

    box["_dist_m"] = box.apply(
        lambda r: _haversine_m(location.lat, location.lon, r["latitude"], r["longitude"]),
        axis=1,
    )
    within = box[box["_dist_m"] <= radius_m].copy()
    logger.info(
        "  %d collisions within %dm of %s (%d in bbox)",
        len(within),
        radius_m,
        location.name,
        len(box),
    )
    if within.empty:
        return []

    # Join vehicle rows for just these collisions.
    indices = set(within["collision_index"])
    veh_by_collision: dict[str, list[dict]] = {}
    if not vehicle_df.empty and "collision_index" in vehicle_df.columns:
        subset = vehicle_df[vehicle_df["collision_index"].isin(indices)]
        for vrec in subset.to_dict(orient="records"):
            cleaned = {k: _clean(v) for k, v in vrec.items()}
            veh_by_collision.setdefault(str(cleaned.get("collision_index")), []).append(cleaned)

    records: list[dict] = []
    for crec in within.to_dict(orient="records"):
        cleaned = {k: _clean(v) for k, v in crec.items()}
        cid = str(cleaned.get("collision_index"))
        cleaned["vehicles"] = veh_by_collision.get(cid, [])
        records.append(cleaned)
    return records


def _load_and_filter(cfg: LoadCollisionDataConfig, inp: LoadCollisionDataInput) -> list[dict]:

    if cfg.combined_csv:
        col_paths = [Path(cfg.combined_csv)]
        veh_paths = [Path(cfg.combined_vehicle_csv)] if cfg.combined_vehicle_csv else []
        if not col_paths[0].exists():
            raise FileNotFoundError(f"combined_csv not found: {cfg.combined_csv}")
    else:
        col_paths, veh_paths = _resolve_csv_paths(cfg, inp.year_from, inp.year_to)
    if not col_paths:
        raise RuntimeError("No collision CSV files available (download failed?).")

    collision_df = _load_collisions(col_paths)
    vehicle_df = _load_vehicles(veh_paths)
    return _filter_to_radius(collision_df, vehicle_df, inp.location)


async def load_collision_data(
    config: LoadCollisionDataConfig,
    input: LoadCollisionDataInput,
) -> LoadCollisionDataOutput:
    """Load raw STATS19 collision records from DfT CSV extracts, filtered to a
    radius around a given location. Called directly by data_retrieval_agent."""
    records = await asyncio.to_thread(_load_and_filter, config, input)
    logger.info("load_collision_data: %d records near %s", len(records), input.location.name)
    return LoadCollisionDataOutput(raw_records=records, record_count=len(records))
