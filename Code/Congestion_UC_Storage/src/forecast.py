"""
Step 1 — Zone-level load / wind / solar forecasting.

Why this exists
---------------
The downstream demo needs *forecast trajectories* to push into a network model.
We intentionally use a persistence (naive) forecaster — the same idea as
``Code/BenchmarkModel/LoadForecasting/models/naive.py``:

    y_hat(t + h) = y(t)

Forecast skill is not the research goal here; coupling forecasts to congestion /
storage / dispatch metrics is.

Inputs
------
Raw PSML minute-level zone CSV, e.g. ``CAISO_zone_1_.csv`` with columns
``time, load_power, wind_power, solar_power, ...``.

Outputs
-------
A forecast table under ``outputs/forecasts/`` with, for each target and horizon:
mean / upper / lower (simple residual-std intervals from the train window).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def load_zone_series(
    zone_csv: str,
    year: Optional[int] = 2018,
    resample_rule: str = "1h",
) -> pd.DataFrame:
    """
    Load a PSML zone CSV and optionally crop + resample.

    Parameters
    ----------
    zone_csv:
        Path to ``*_zone_*.csv`` under ``PSML/Minute-level Load and Renewable/``.
    year:
        If set, keep only that calendar year (speeds up the demo a lot).
    resample_rule:
        Pandas offset alias, e.g. ``\"1h\"``. Minute data is mean-aggregated.

    Returns
    -------
    DataFrame indexed by timestamp with at least load/wind/solar columns.
    """
    df = pd.read_csv(zone_csv)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()

    if year is not None:
        df = df.loc[str(year)]

    # Weather columns are kept if present (useful later as features); targets are power.
    if resample_rule:
        df = df.resample(resample_rule).mean()

    # Drop residual NaNs from resample edges.
    df = df.dropna(how="any")
    return df


def _train_test_split(
    df: pd.DataFrame,
    year: Optional[int],
    test_fraction: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split into train / test.

    If ``year`` is set: train = Jan–Nov, test = December (PSML forecasting style).
    Otherwise: last ``test_fraction`` of rows are test.
    """
    if year is not None:
        train = df.loc[f"{year}-01-01":f"{year}-11-30 23:59:59"]
        test = df.loc[f"{year}-12-01":f"{year}-12-31 23:59:59"]
        if len(train) == 0 or len(test) == 0:
            raise ValueError(
                f"Train/test empty for year={year}. Check zone CSV coverage."
            )
        return train, test

    n = len(df)
    n_test = max(1, int(n * test_fraction))
    return df.iloc[:-n_test], df.iloc[-n_test:]


def persistence_forecast_frame(
    train: pd.DataFrame,
    test: pd.DataFrame,
    targets: Sequence[str],
    horizons_hours: Sequence[int],
    interval_z: float = 1.96,
) -> pd.DataFrame:
    """
    Build persistence forecasts on the test index.

    For each target ``y`` and horizon ``h`` (in steps of the resampled series,
    assumed 1 hour per step when ``resample_rule='1h'``):

    - mean: ``y[t]`` used as forecast of ``y[t+h]`` (aligned on forecast origin ``t``)
    - U/L: mean ± z * std(train residual), residual = y[t+h] - y[t] on train

    Notes
    -----
    We emit one row per *forecast origin* ``t`` in the test set where ``t+h``
    still exists inside the full (train+test) timeline when evaluating, but for
    the demo we only require ``t`` in test and store the persistence value
    ``y[t]`` as the prediction of the future. Downstream mapping can treat each
    row as \"the world at origin t with h-ahead outlook\".
    """
    # Combined series so that train residual uses contiguous history.
    full = pd.concat([train, test]).sort_index()
    # Deduplicate index if train/test abut.
    full = full[~full.index.duplicated(keep="last")]

    rows: List[Dict[str, Any]] = []
    test_index = test.index

    # Pre-compute residual std per target/horizon on train.
    std_map: Dict[Tuple[str, int], float] = {}
    for target in targets:
        if target not in full.columns:
            raise KeyError(f"Target column '{target}' missing from zone data.")
        series = full[target]
        for h in horizons_hours:
            # residual at time t: future(t+h) - present(t), for t in train with room for h
            present = train[target]
            future = series.reindex(present.index + pd.Timedelta(hours=h))
            # align by position via explicit timestamps
            aligned_future = []
            aligned_present = []
            for ts, val in present.items():
                ts_f = ts + pd.Timedelta(hours=h)
                if ts_f in series.index:
                    aligned_present.append(val)
                    aligned_future.append(series.loc[ts_f])
            if len(aligned_present) < 2:
                std_map[(target, h)] = 0.0
            else:
                resid = np.asarray(aligned_future) - np.asarray(aligned_present)
                std_map[(target, h)] = float(np.nanstd(resid))

    for ts in test_index:
        row: Dict[str, Any] = {"origin_time": ts}
        for target in targets:
            y_now = float(full.loc[ts, target])
            row[f"{target}_t"] = y_now
            for h in horizons_hours:
                mean = y_now  # persistence
                std = std_map[(target, h)]
                row[f"{target}_t+{h}h(mean)"] = mean
                row[f"{target}_t+{h}h(U)"] = mean + interval_z * std
                row[f"{target}_t+{h}h(L)"] = mean - interval_z * std
                # Actual future if available (for offline scoring / sanity checks)
                ts_f = ts + pd.Timedelta(hours=h)
                if ts_f in full.index:
                    row[f"{target}_t+{h}h(actual)"] = float(full.loc[ts_f, target])
                else:
                    row[f"{target}_t+{h}h(actual)"] = np.nan
        rows.append(row)

    out = pd.DataFrame(rows)
    return out


def run_forecast_step(config: Dict[str, Any]) -> str:
    """
    Execute Step 1 end-to-end and write a forecast CSV.

    Returns
    -------
    Path to the written forecast file.
    """
    data_cfg = config["data"]
    fc_cfg = config["forecast"]
    out_cfg = config["outputs"]

    # Paths relative to Congestion_UC_Storage/ (parent of src/).
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _resolve(path: str) -> str:
        return (
            os.path.normpath(path)
            if os.path.isabs(path)
            else os.path.normpath(os.path.join(base, path))
        )

    def _rel(path: str) -> str:
        return os.path.relpath(_resolve(path), base)

    zone_csv = _resolve(data_cfg["zone_csv"])

    df = load_zone_series(
        zone_csv=zone_csv,
        year=data_cfg.get("year"),
        resample_rule=data_cfg.get("resample_rule", "1h"),
    )
    train, test = _train_test_split(
        df,
        year=data_cfg.get("year"),
        test_fraction=float(fc_cfg.get("test_fraction", 0.1)),
    )

    forecasts = persistence_forecast_frame(
        train=train,
        test=test,
        targets=list(fc_cfg["targets"]),
        horizons_hours=list(fc_cfg["horizons_hours"]),
        interval_z=float(fc_cfg.get("interval_z", 1.96)),
    )

    out_dir = _resolve(out_cfg["dir"])
    fc_dir = os.path.join(out_dir, out_cfg.get("forecasts_subdir", "forecasts"))
    os.makedirs(fc_dir, exist_ok=True)

    zone_name = os.path.splitext(os.path.basename(zone_csv))[0]
    year_tag = data_cfg.get("year") or "allyears"
    out_path = os.path.join(fc_dir, f"forecast_{zone_name}_{year_tag}.csv")
    forecasts.to_csv(out_path, index=False)
    out_rel = _rel(out_path)

    if config.get("logging", {}).get("verbose", True):
        print(f"[forecast] zone={_rel(zone_csv)}")
        print(f"[forecast] rows train={len(train)} test={len(test)} forecasts={len(forecasts)}")
        print(f"[forecast] wrote {out_rel}")

    return out_rel
