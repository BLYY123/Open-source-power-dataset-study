#!/usr/bin/env python3
"""
Orchestrator for the Congestion_UC_Storage study pipeline.

Examples
--------
python run_pipeline.py --steps forecast
python run_pipeline.py --steps forecast,map,powerflow,decisions
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

import yaml

from src.forecast import run_forecast_step
from src.map_injections import run_mapping_step
from src.powerflow import run_powerflow_step
from src.decisions import run_decisions_step


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def parse_steps(steps_csv: str) -> List[str]:
    allowed = {"forecast", "map", "powerflow", "decisions"}
    steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
    bad = [s for s in steps if s not in allowed]
    if bad:
        raise ValueError(f"Unknown steps {bad}; allowed={sorted(allowed)}")
    return steps


def main(argv: List[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    # Resolve relative paths against this package so cwd does not matter.
    os.chdir(here)
    parser = argparse.ArgumentParser(
        description="PSML zone forecast → SAVNW decisions demo pipeline"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (relative to Congestion_UC_Storage/)",
    )
    parser.add_argument(
        "--steps",
        default="forecast",
        help="Comma-separated: forecast,map,powerflow,decisions",
    )
    parser.add_argument(
        "--map-row",
        type=int,
        default=0,
        help="Forecast row index to map (Step 2)",
    )
    parser.add_argument(
        "--horizon-hours",
        type=int,
        default=None,
        help="Horizon to map (default: first in config)",
    )
    parser.add_argument(
        "--include-shunts",
        action="store_true",
        help="v2: enable RAW fixed shunts (overrides config include_shunts)",
    )
    parser.add_argument(
        "--compare-shunts",
        action="store_true",
        help="Run powerflow v1 (no shunt) and v2 (with shunt) and compare vm_pu",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.include_shunts:
        config.setdefault("powerflow", {})["include_shunts"] = True
    if args.compare_shunts:
        config.setdefault("powerflow", {})["run_both_shunt_versions"] = True
    steps = parse_steps(args.steps)

    forecast_path = None
    recipe_path = None
    pf_path = None

    if "forecast" in steps:
        forecast_path = run_forecast_step(config)

    if "map" in steps:
        recipe_path = run_mapping_step(
            config,
            forecast_csv=forecast_path,
            row_index=args.map_row,
            horizon_hours=args.horizon_hours,
        )

    if "powerflow" in steps:
        pf_path = run_powerflow_step(config, recipe_json=recipe_path)

    if "decisions" in steps:
        run_decisions_step(config, powerflow_manifest=pf_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
