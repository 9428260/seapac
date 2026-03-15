from __future__ import annotations

"""
ELIA raw-schema aware converter for paper-style reproduction dataset.

Important note
--------------
The attached ELIA schema image appears to describe balancing activation / reserve
volume fields such as:

- datetime
- resolutioncode
- afrrbeup
- mfrrbesaup
- mfrrbedaup
- afrrbedown
- mfrrbesadown
- mfrrbedadown

This is NOT the same schema as the paper's directly described renewable generation
and load-demand curves. The paper says renewable generation outputs and load demand
curves were extracted from an ELIA dataset, but the screenshot schema corresponds
to balancing activation volumes.

Accordingly, this file does two things:

1) It directly supports the exact raw column names from the screenshot.
2) It converts them into a normalized internal dataset structure that can still be
   used in the reproduction pipeline.

If you later obtain ELIA solar / wind / load columns, replace the feature-building
logic in `build_internal_timeseries_from_elia_raw()` with the direct mapping.
"""

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

import numpy as np
import pandas as pd


PROSUMER_TABLE = [
    (48,  "Commercial",  1, 0, 1, 1, 1),
    (78,  "Commercial",  1, 0, 1, 1, 1),
    (102, "Commercial",  1, 0, 1, 1, 1),
    (127, "Commercial",  1, 0, 1, 1, 1),
    (59,  "Rural",       0, 1, 1, 1, 1),
    (109, "Rural",       0, 1, 1, 1, 1),
    (130, "Rural",       0, 1, 1, 1, 1),
    (140, "Rural",       0, 1, 1, 1, 1),
    (67,  "Industrial",  1, 1, 0, 0, 1),
    (95,  "Industrial",  1, 1, 0, 0, 1),
    (133, "Industrial",  1, 1, 0, 0, 1),
    (136, "Industrial",  1, 1, 0, 0, 1),
    (62,  "Residential", 0, 0, 1, 1, 1),
    (86,  "Residential", 0, 0, 1, 1, 1),
    (106, "Residential", 0, 0, 1, 1, 1),
    (138, "Residential", 0, 0, 1, 1, 1),
    (74,  "EnergyHub",   1, 1, 1, 1, 1),
    (100, "EnergyHub",   1, 1, 1, 1, 1),
    (116, "EnergyHub",   1, 1, 1, 1, 1),
    (134, "EnergyHub",   1, 1, 1, 1, 1),
]

TYPE_SPECS = {
    "Residential": {"pv_kw_cap": 6.0, "wt_kw_cap": 0.0, "bess_kwh_cap": 13.5, "bess_kw_cap": 5.0, "cl_kw_cap": 2.0, "cdg_kw_cap": 0.0, "load_scale": 0.6},
    "Commercial": {"pv_kw_cap": 60.0, "wt_kw_cap": 0.0, "bess_kwh_cap": 80.0, "bess_kw_cap": 30.0, "cl_kw_cap": 20.0, "cdg_kw_cap": 40.0, "load_scale": 1.3},
    "Rural": {"pv_kw_cap": 25.0, "wt_kw_cap": 35.0, "bess_kwh_cap": 50.0, "bess_kw_cap": 20.0, "cl_kw_cap": 8.0, "cdg_kw_cap": 0.0, "load_scale": 0.8},
    "Industrial": {"pv_kw_cap": 0.0, "wt_kw_cap": 50.0, "bess_kwh_cap": 0.0, "bess_kw_cap": 0.0, "cl_kw_cap": 30.0, "cdg_kw_cap": 80.0, "load_scale": 1.8},
    "EnergyHub": {"pv_kw_cap": 120.0, "wt_kw_cap": 60.0, "bess_kwh_cap": 200.0, "bess_kw_cap": 80.0, "cl_kw_cap": 40.0, "cdg_kw_cap": 60.0, "load_scale": 2.0},
}


@dataclass
class EliaScreenshotSchemaMapping:
    datetime: str = "datetime"
    resolutioncode: str = "resolutioncode"
    afrrbeup: str = "afrrbeup"
    mfrrbesaup: str = "mfrrbesaup"
    mfrrbedaup: str = "mfrrbedaup"
    afrrbedown: str = "afrrbedown"
    mfrrbesadown: str = "mfrrbesadown"
    mfrrbedadown: str = "mfrrbedadown"


def load_elia_raw_from_screenshot_schema(
    path: str | Path,
    mapping: EliaScreenshotSchemaMapping = EliaScreenshotSchemaMapping(),
    target_tz: str | None = None,
) -> pd.DataFrame:
    """Load ELIA raw CSV and optionally convert timestamps to target timezone (e.g. 'Asia/Seoul')."""
    path = Path(path)

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    elif path.suffix.lower() in [".parquet", ".pq"]:
        df = pd.read_parquet(path)
    elif path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        raise ValueError(f"Unsupported ELIA file format: {path.suffix}")

    required = [
        mapping.datetime,
        mapping.resolutioncode,
        mapping.afrrbeup,
        mapping.mfrrbesaup,
        mapping.mfrrbedaup,
        mapping.afrrbedown,
        mapping.mfrrbesadown,
        mapping.mfrrbedadown,
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Missing raw ELIA columns from screenshot schema: {missing}")

    out = pd.DataFrame()
    out["timestamp"] = pd.to_datetime(df[mapping.datetime], errors="coerce", utc=True)
    if target_tz:
        out["timestamp"] = out["timestamp"].dt.tz_convert(target_tz)
    out["resolutioncode"] = df[mapping.resolutioncode].astype(str)
    out["afrr_up_mw"] = pd.to_numeric(df[mapping.afrrbeup], errors="coerce").fillna(0.0)
    out["mfrr_sa_up_mw"] = pd.to_numeric(df[mapping.mfrrbesaup], errors="coerce").fillna(0.0)
    out["mfrr_da_up_mw"] = pd.to_numeric(df[mapping.mfrrbedaup], errors="coerce").fillna(0.0)
    out["afrr_down_mw"] = pd.to_numeric(df[mapping.afrrbedown], errors="coerce").fillna(0.0)
    out["mfrr_sa_down_mw"] = pd.to_numeric(df[mapping.mfrrbesadown], errors="coerce").fillna(0.0)
    out["mfrr_da_down_mw"] = pd.to_numeric(df[mapping.mfrrbedadown], errors="coerce").fillna(0.0)

    out = out.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp")
    # resample은 숫자 컬럼만 허용하므로 resolutioncode 제외
    out = out.drop(columns=["resolutioncode"], errors="ignore")
    out = out.set_index("timestamp").resample("15min").mean().interpolate("time").reset_index()
    return out


def _safe_norm(series: pd.Series) -> pd.Series:
    mx = float(series.max()) if len(series) else 0.0
    if mx <= 1e-9:
        return pd.Series(np.zeros(len(series)), index=series.index)
    return series / mx


def build_internal_timeseries_from_elia_raw(elia_raw: pd.DataFrame) -> pd.DataFrame:
    df = elia_raw.copy()

    up_total = df["afrr_up_mw"] + df["mfrr_sa_up_mw"] + df["mfrr_da_up_mw"]
    down_total = df["afrr_down_mw"] + df["mfrr_sa_down_mw"] + df["mfrr_da_down_mw"]

    surplus_proxy = _safe_norm(down_total)
    scarcity_proxy = _safe_norm(up_total)

    hour = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60.0
    daylight = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None)

    out = pd.DataFrame()
    out["timestamp"] = df["timestamp"]
    out["solar_proxy"] = np.clip(daylight * (0.5 + 0.5 * surplus_proxy.to_numpy()), 0, 1)
    out["wind_proxy"] = np.clip(0.35 + 0.35 * surplus_proxy.to_numpy() + 0.20 * scarcity_proxy.to_numpy(), 0, 1)

    evening_shape = 0.65 + 0.35 * np.sin((hour - 18) / 24 * 2 * np.pi) ** 2
    out["load_proxy"] = np.clip(0.4 * evening_shape + 0.6 * scarcity_proxy.to_numpy(), 0, None)

    out["price_buy"] = 95.0 + 90.0 * scarcity_proxy.to_numpy() + 20.0 * ((hour >= 17) & (hour < 21)).astype(float)
    out["price_sell"] = out["price_buy"] * 0.62
    return out


def load_ieee141_grid(grid_pkl: str | Path = "IEEE141_grid.pkl") -> Dict[str, pd.DataFrame]:
    with open(grid_pkl, "rb") as f:
        obj = pickle.load(f)

    if isinstance(obj, dict) and "grid" in obj:
        return obj["grid"]
    if isinstance(obj, dict) and {"buses", "branches", "generators"} <= set(obj.keys()):
        return obj
    raise ValueError("Unsupported IEEE141 grid pickle structure.")


def build_prosumer_table() -> pd.DataFrame:
    cols = ["bus", "prosumer_type", "has_cdg", "has_wt", "has_pv", "has_bess", "has_cl"]
    df = pd.DataFrame(PROSUMER_TABLE, columns=cols)
    df["prosumer_id"] = df.apply(lambda r: f"bus_{int(r['bus'])}_{r['prosumer_type']}", axis=1)

    for field in ["pv_kw_cap", "wt_kw_cap", "bess_kwh_cap", "bess_kw_cap", "cl_kw_cap", "cdg_kw_cap", "load_scale"]:
        df[field] = 0.0

    for idx, row in df.iterrows():
        spec = TYPE_SPECS[row["prosumer_type"]]
        df.loc[idx, "pv_kw_cap"] = spec["pv_kw_cap"] if row["has_pv"] else 0.0
        df.loc[idx, "wt_kw_cap"] = spec["wt_kw_cap"] if row["has_wt"] else 0.0
        df.loc[idx, "bess_kwh_cap"] = spec["bess_kwh_cap"] if row["has_bess"] else 0.0
        df.loc[idx, "bess_kw_cap"] = spec["bess_kw_cap"] if row["has_bess"] else 0.0
        df.loc[idx, "cl_kw_cap"] = spec["cl_kw_cap"] if row["has_cl"] else 0.0
        df.loc[idx, "cdg_kw_cap"] = spec["cdg_kw_cap"] if row["has_cdg"] else 0.0
        df.loc[idx, "load_scale"] = spec["load_scale"]
    return df


def make_paper_style_timeseries(
    internal_df: pd.DataFrame,
    prosumers: pd.DataFrame,
    p2p_kappa: float = 0.5,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = internal_df.copy()
    df["price_p2p"] = p2p_kappa * (df["price_buy"] - df["price_sell"]) + df["price_sell"]

    rows = []
    hour = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60.0

    for _, p in prosumers.iterrows():
        ptype = p["prosumer_type"]

        if ptype == "Residential":
            load_shape = 0.55 + 0.25 * np.sin((hour - 18) / 24 * 2 * np.pi) ** 2 + 0.10 * (hour < 7)
        elif ptype == "Commercial":
            load_shape = 0.45 + 0.55 * (((hour >= 8) & (hour < 18)).astype(float))
        elif ptype == "Industrial":
            load_shape = 0.70 + 0.20 * (((hour >= 6) & (hour < 22)).astype(float))
        elif ptype == "Rural":
            load_shape = 0.50 + 0.20 * np.sin((hour - 12) / 24 * 2 * np.pi) ** 2
        else:
            load_shape = 0.65 + 0.25 * (((hour >= 7) & (hour < 22)).astype(float))

        load_shape = np.clip(load_shape + rng.normal(0, 0.03, size=len(df)), 0.25, None)

        base_load_kw = 100.0 * float(p["load_scale"])
        load_kw = base_load_kw * df["load_proxy"].to_numpy() * load_shape

        pv_kw = p["pv_kw_cap"] * df["solar_proxy"].to_numpy() * (0.96 + rng.normal(0, 0.02, size=len(df)))
        pv_kw = np.clip(pv_kw, 0, None)

        wt_kw = p["wt_kw_cap"] * df["wind_proxy"].to_numpy() * (0.98 + rng.normal(0, 0.03, size=len(df)))
        wt_kw = np.clip(wt_kw, 0, None)

        bess_soc_kwh = np.full(len(df), p["bess_kwh_cap"] * 0.5)
        bess_ref_power_kw = np.zeros(len(df))

        if p["bess_kwh_cap"] > 0:
            charge_mask = (df["price_buy"] <= df["price_buy"].quantile(0.25)) & (df["solar_proxy"] > 0.2)
            discharge_mask = df["price_buy"] >= df["price_buy"].quantile(0.75)
            bess_ref_power_kw[charge_mask] = -0.5 * p["bess_kw_cap"]
            bess_ref_power_kw[discharge_mask] = 0.6 * p["bess_kw_cap"]

            eta = 0.92
            for t in range(1, len(df)):
                e = bess_soc_kwh[t - 1]
                if bess_ref_power_kw[t] < 0:
                    e = min(p["bess_kwh_cap"], e + (-bess_ref_power_kw[t]) * 0.25 * eta)
                else:
                    e = max(0, e - bess_ref_power_kw[t] * 0.25 / eta)
                bess_soc_kwh[t] = e

        controllable_load_kw = p["cl_kw_cap"] * (0.3 + 0.7 * (df["price_buy"] >= df["price_buy"].quantile(0.75)).astype(float))

        out = pd.DataFrame({
            "timestamp": df["timestamp"],
            "bus": int(p["bus"]),
            "prosumer_id": p["prosumer_id"],
            "prosumer_type": p["prosumer_type"],
            "load_kw": np.round(load_kw, 3),
            "pv_kw": np.round(pv_kw, 3),
            "wt_kw": np.round(wt_kw, 3),
            "bess_soc_kwh": np.round(bess_soc_kwh, 3),
            "bess_ref_power_kw": np.round(bess_ref_power_kw, 3),
            "controllable_load_kw": np.round(controllable_load_kw, 3),
            "cdg_kw_cap": float(p["cdg_kw_cap"]),
            "price_buy": np.round(df["price_buy"], 3),
            "price_sell": np.round(df["price_sell"], 3),
            "price_p2p": np.round(df["price_p2p"], 3),
        })
        rows.append(out)

    return pd.concat(rows, ignore_index=True)


def assign_paper_split(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts)
    day = ts.dt.day
    next_month = ts.dt.to_period("M").dt.to_timestamp() + pd.offsets.MonthEnd(1)
    days_in_month = next_month.dt.day

    is_val = day == 1
    is_test = day >= (days_in_month - 6)
    split = np.where(is_val, "val", np.where(is_test, "test", "train"))
    return pd.Series(split, index=ts.index, name="split")


def build_dataset_from_elia_df(
    elia_raw: pd.DataFrame,
    ieee141_grid_pkl: str | Path = "IEEE141_grid.pkl",
    split_label: str = "train",
    metadata_extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build dataset from an already-loaded (and optionally filtered) ELIA raw DataFrame.
    Use split_label to set timeseries.split to 'train' or 'test' for the whole period."""
    internal = build_internal_timeseries_from_elia_raw(elia_raw)
    grid = load_ieee141_grid(ieee141_grid_pkl)
    prosumers = build_prosumer_table()
    timeseries = make_paper_style_timeseries(internal, prosumers)
    timeseries["split"] = split_label
    meta = {
        "name": "Paper reproduction dataset from ELIA + IEEE141",
        "warning": (
            "The ELIA screenshot schema corresponds to balancing activation volumes, "
            "not direct solar/wind/load columns. Internal solar/wind/load were built as proxy."
        ),
        "time_resolution_minutes": 15,
        "timezone": (str(elia_raw["timestamp"].dt.tz) if elia_raw["timestamp"].dt.tz is not None else None),
        "elia_raw_columns": [
            "datetime", "resolutioncode",
            "afrrbeup", "mfrrbesaup", "mfrrbedaup",
            "afrrbedown", "mfrrbesadown", "mfrrbedadown",
        ],
    }
    if metadata_extra:
        meta.update(metadata_extra)
    return {
        "metadata": meta,
        "elia_raw": elia_raw,
        "elia_internal": internal,
        "grid": grid,
        "prosumers": prosumers,
        "timeseries": timeseries,
    }


def build_reproduction_dataset_from_screenshot_schema(
    elia_raw_path: str | Path,
    ieee141_grid_pkl: str | Path = "IEEE141_grid.pkl",
    output_pkl: str | Path = "paper_reproduction_dataset_from_screenshot_schema.pkl",
    target_tz: str | None = None,
) -> Dict[str, Any]:
    elia_raw = load_elia_raw_from_screenshot_schema(elia_raw_path, target_tz=target_tz)
    internal = build_internal_timeseries_from_elia_raw(elia_raw)
    grid = load_ieee141_grid(ieee141_grid_pkl)
    prosumers = build_prosumer_table()
    timeseries = make_paper_style_timeseries(internal, prosumers)
    timeseries["split"] = assign_paper_split(timeseries["timestamp"])

    meta = {
        "name": "Paper reproduction dataset from ELIA screenshot-schema raw data + IEEE141",
        "warning": (
            "The ELIA screenshot schema corresponds to balancing activation volumes, "
            "not direct solar/wind/load columns. Internal solar/wind/load were built as proxy "
            "signals to keep the reproduction pipeline executable."
        ),
        "time_resolution_minutes": 15,
        "timezone": str(elia_raw["timestamp"].dt.tz) if elia_raw["timestamp"].dt.tz else None,
        "elia_raw_columns": [
            "datetime", "resolutioncode",
            "afrrbeup", "mfrrbesaup", "mfrrbedaup",
            "afrrbedown", "mfrrbesadown", "mfrrbedadown",
        ],
    }
    dataset = {
        "metadata": meta,
        "elia_raw": elia_raw,
        "elia_internal": internal,
        "grid": grid,
        "prosumers": prosumers,
        "timeseries": timeseries,
    }

    with open(output_pkl, "wb") as f:
        pickle.dump(dataset, f)

    return dataset


if __name__ == "__main__":
    dataset = build_reproduction_dataset_from_screenshot_schema(
        elia_raw_path="elia_raw.csv",
        ieee141_grid_pkl="IEEE141_grid.pkl",
        output_pkl="paper_reproduction_dataset_from_screenshot_schema.pkl",
    )

    print("Saved: paper_reproduction_dataset_from_screenshot_schema.pkl")
    print("elia_raw rows:", len(dataset["elia_raw"]))
    print("prosumers:", len(dataset["prosumers"]))
    print("timeseries rows:", len(dataset["timeseries"]))
    print(dataset["timeseries"].head())
