"""
Step 2 — Map zone forecasts → SAVNW bus / machine injections (no feeders).

What “map” means
----------------
Forecasts are *zone scalars* (normalized load / wind / solar at a time origin).
SAVNW is a *network* with named buses and generators. Mapping assigns those
scalars to concrete MW injections, inspired by
``Code/Joint Simulation/code/cosim.py`` but **without OpenDSS feeders**:

- Wind profile → machine at bus ``3018`` (``pg ≈ wind_ts * wind_mw_base``).
- Load profile → scale existing transmission load P/Q at load buses
  (including 3005/3008 treated as ordinary loads when ``ignore_feeders=true``).
- Solar profile (v1) → optional negative load or ignored until a PV bus is chosen;
  with feeders skipped, a simple approach is to reduce net load at 3005/3008
  proportional to solar (documented placeholder).

Status
------
Scaffold only: documents the intended interface and writes a *mapping recipe*
JSON/CSV for one forecast row. Full pandapower case mutation comes next.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import pandas as pd


# Buses that carry loads in the SAVNW family of cases used by PSML cosim
# (see savnw_wind_scale_down.raw load section). Used for documentation / later scaling.
SAVNW_LOAD_BUSES = [153, 154, 203, 205, 3005, 3007, 3008]
SAVNW_WIND_BUS = 3018


def build_injection_recipe(
    forecast_row: pd.Series,
    horizon_hours: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Translate one forecast origin + horizon into an injection recipe.

    Parameters
    ----------
    forecast_row:
        One row from Step-1 forecast CSV (must include ``*_t+{h}h(mean)`` fields).
    horizon_hours:
        Which outlook to map (e.g. 1 or 24).
    config:
        Pipeline config; uses ``mapping`` section.

    Returns
    -------
    dict describing intended MW set-points. This is *not* yet applied to a net.
    """
    map_cfg = config["mapping"]
    h = horizon_hours
    load_mean = float(forecast_row[f"load_power_t+{h}h(mean)"])
    wind_mean = float(forecast_row[f"wind_power_t+{h}h(mean)"])
    solar_mean = float(forecast_row[f"solar_power_t+{h}h(mean)"])
    load_u = float(forecast_row[f"load_power_t+{h}h(U)"])
    load_l = float(forecast_row[f"load_power_t+{h}h(L)"])

    wind_mw_base = float(map_cfg.get("wind_mw_base", 130.0))
    wind_bus = int(map_cfg.get("wind_bus", SAVNW_WIND_BUS))

    recipe = {
        "origin_time": str(forecast_row["origin_time"]),
        "horizon_hours": h,
        "ignore_feeders": bool(map_cfg.get("ignore_feeders", True)),
        # Normalized PSML powers (profiles), not yet absolute system MW for loads.
        "profiles": {
            "load_power_mean": load_mean,
            "load_power_U": load_u,
            "load_power_L": load_l,
            "wind_power_mean": wind_mean,
            "solar_power_mean": solar_mean,
        },
        # Absolute-ish wind MW following cosim: wind_ts * 130.
        "machines": {
            str(wind_bus): {
                "p_mw": wind_mean * wind_mw_base,
                "note": "cosim.change_wind style: profile * wind_mw_base",
            }
        },
        # Load scaling factors to apply to each SAVNW load bus base P/Q.
        "load_scale_by_bus": {
            str(b): {
                "scale_mean": load_mean,
                "scale_U": load_u,
                "scale_L": load_l,
                "note": "P_new = P_base * scale; same idea as cosim.change_load",
            }
            for b in SAVNW_LOAD_BUSES
        },
        "solar_placeholder": {
            "policy": "reduce_net_load_at_3005_3008_by_solar_mean_fraction",
            "solar_mean": solar_mean,
            "note": (
                "Without feeders/OpenDSS, solar is not injected at 671. "
                "v1 placeholder: treat solar as a zonal reduction of loads at "
                "3005/3008 when applying the map in pandapower."
            ),
        },
    }
    return recipe


def run_mapping_step(
    config: Dict[str, Any],
    forecast_csv: Optional[str] = None,
    row_index: int = 0,
    horizon_hours: Optional[int] = None,
) -> str:
    """
    Step-2 entry: read forecasts, emit injection recipe file(s).

    Returns path to the written recipe JSON.
    """
    out_cfg = config["outputs"]
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _resolve(path: str) -> str:
        return (
            os.path.normpath(path)
            if os.path.isabs(path)
            else os.path.normpath(os.path.join(base, path))
        )

    def _rel(path: str) -> str:
        return os.path.relpath(_resolve(path), base)

    if forecast_csv is None:
        fc_dir = _resolve(
            os.path.join(out_cfg["dir"], out_cfg.get("forecasts_subdir", "forecasts"))
        )
        candidates = sorted(
            f for f in os.listdir(fc_dir) if f.startswith("forecast_") and f.endswith(".csv")
        )
        if not candidates:
            raise FileNotFoundError(
                f"No forecast_*.csv in {_rel(fc_dir)}. Run Step 1 first."
            )
        forecast_csv = os.path.join(fc_dir, candidates[-1])
    else:
        forecast_csv = _resolve(forecast_csv)

    fc = pd.read_csv(forecast_csv)
    if row_index < 0 or row_index >= len(fc):
        raise IndexError(
            f"row_index={row_index} out of range for {_rel(forecast_csv)}"
        )

    if horizon_hours is None:
        horizon_hours = int(config["forecast"]["horizons_hours"][0])

    recipe = build_injection_recipe(fc.iloc[row_index], horizon_hours, config)

    map_dir = _resolve(
        os.path.join(out_cfg["dir"], out_cfg.get("mapping_subdir", "mapping"))
    )
    os.makedirs(map_dir, exist_ok=True)
    out_path = os.path.join(
        map_dir, f"injection_recipe_row{row_index}_h{horizon_hours}.json"
    )
    with open(out_path, "w") as f:
        json.dump(recipe, f, indent=2)
    out_rel = _rel(out_path)

    if config.get("logging", {}).get("verbose", True):
        print(
            f"[map] forecast={_rel(forecast_csv)} "
            f"row={row_index} h={horizon_hours}h"
        )
        print(f"[map] wrote {out_rel}")
        print(
            "[map] recipe ready — Step 3 applies it on the simplified pandapower net."
        )

    return out_rel
