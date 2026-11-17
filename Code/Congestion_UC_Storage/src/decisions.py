"""
Step 4 — Turn power-flow results into decision / risk metrics.

Runs **per powerflow version** (``outputs/powerflow/v1``, ``v2``, …) so you can
compare congestion / storage / voltage-band outcomes across model variants.
Future versions only need a new ``powerflow/<tag>/`` folder with a summary.

Three learning lanes (not real market/UC software):

1. Congestion — lines with loading_percent above threshold; voltage band.
2. Storage heuristic — charge if net load low and quiet lines; else discharge.
3. Toy dispatch report — the cosim-style shares already applied in Step 3.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd


def _discover_pf_versions(pf_root: str) -> List[str]:
    """Sorted version tags that contain powerflow_summary.json."""
    if not os.path.isdir(pf_root):
        return []
    tags = []
    for name in sorted(os.listdir(pf_root)):
        summary = os.path.join(pf_root, name, "powerflow_summary.json")
        if os.path.isdir(os.path.join(pf_root, name)) and os.path.isfile(summary):
            tags.append(name)
    return tags


def _decide_one(
    summary: Dict[str, Any],
    summary_path: str,
    config: Dict[str, Any],
    _resolve,
    _rel,
) -> Dict[str, Any]:
    pf_cfg = config.get("powerflow", {})
    alert = float(pf_cfg.get("loading_alert_percent", 100.0))
    vmin = float(pf_cfg.get("voltage_pu_min", 0.95))
    vmax = float(pf_cfg.get("voltage_pu_max", 1.05))

    congestion: Dict[str, Any] = {
        "loading_alert_percent": alert,
        "n_lines_over_alert": None,
        "top_stressed_lines": [],
        "n_buses_outside_voltage_band": None,
        "buses_outside_band": [],
    }
    storage: Dict[str, Any] = {
        "storage_action_mw": None,
        "reason": "powerflow did not converge",
    }

    if summary.get("converged"):
        lines = pd.read_csv(_resolve(summary["res_line_csv"]))
        buses = pd.read_csv(_resolve(summary["res_bus_csv"]))

        overloaded = lines[lines["loading_percent"] >= alert].sort_values(
            "loading_percent", ascending=False
        )
        top = lines.sort_values("loading_percent", ascending=False).head(5)
        congestion["n_lines_over_alert"] = int(len(overloaded))
        congestion["top_stressed_lines"] = [
            {
                "name": str(r["name"]),
                "loading_percent": float(r["loading_percent"]),
                "p_from_mw": float(r["p_from_mw"]),
            }
            for _, r in top.iterrows()
        ]

        bad = buses[(buses["vm_pu"] < vmin) | (buses["vm_pu"] > vmax)]
        congestion["n_buses_outside_voltage_band"] = int(len(bad))
        congestion["buses_outside_band"] = [
            {
                "savnw_id": int(r["savnw_id"]),
                "vm_pu": float(r["vm_pu"]),
            }
            for _, r in bad.iterrows()
        ]

        disp = summary.get("dispatch", {})
        p_load = float(disp.get("p_load_mw", 0.0))
        wind = float(disp.get("wind_mw", 0.0))
        net_load = p_load - wind
        max_loading = float(lines["loading_percent"].max())
        charge_if_below = float(
            config.get("decisions", {}).get("storage_net_load_charge_below_mw", 1500.0)
        )
        storage_mw = float(config.get("decisions", {}).get("storage_mw", 50.0))

        if net_load < charge_if_below and max_loading < alert:
            storage = {
                "storage_action_mw": storage_mw,
                "reason": (
                    f"net_load={net_load:.1f} MW < {charge_if_below} and "
                    f"max_loading={max_loading:.1f}% < {alert}% → charge"
                ),
                "net_load_mw": net_load,
                "max_loading_percent": max_loading,
            }
        else:
            storage = {
                "storage_action_mw": -storage_mw,
                "reason": (
                    f"net_load={net_load:.1f} MW or congestion "
                    f"(max_loading={max_loading:.1f}%) → discharge"
                ),
                "net_load_mw": net_load,
                "max_loading_percent": max_loading,
            }

    return {
        "status": "ok" if summary.get("converged") else "degraded",
        "version": summary.get("version"),
        "include_shunts": summary.get("include_shunts"),
        "powerflow_summary": _rel(summary_path),
        "origin_time": summary.get("origin_time"),
        "horizon_hours": summary.get("horizon_hours"),
        "congestion": congestion,
        "storage_heuristic": storage,
        "toy_dispatch": {
            "question": "How did conventional gens share residual load − wind?",
            "rule": "cosim.get_dispatch shares [9, 9, 10, 7.25, 10] on buses "
            "[101, 102, 206, 211, 3011]; 3011 is slack in pandapower",
            "gen_targets_mw": summary.get("dispatch", {}).get("gen_targets_mw"),
            "note": (
                "Targets were applied in Step 3 before runpp "
                "(PV gens 101/102/206/211; slack absorbs balance)."
            ),
        },
        "focus_vm_pu": summary.get("focus_vm_pu", {}),
    }


def run_decisions_step(
    config: Dict[str, Any],
    powerflow_manifest: Optional[str] = None,
) -> str:
    """
    Step-4: for each ``outputs/powerflow/<version>/``, write
    ``outputs/decisions/<version>/decisions.json``, plus a cross-version
    ``decisions_comparison.json``.

    ``powerflow_manifest`` may be the powerflow root dir or any version
    summary; versions are always discovered under the powerflow subdir.

    Returns package-relative path to ``decisions_comparison.json`` (or the
    single version file if only one exists).
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

    dec_root = _resolve(
        os.path.join(out_cfg["dir"], out_cfg.get("decisions_subdir", "decisions"))
    )
    os.makedirs(dec_root, exist_ok=True)

    pf_root = _resolve(
        os.path.join(out_cfg["dir"], out_cfg.get("powerflow_subdir", "powerflow"))
    )
    # If caller passed a version summary path, still scan the whole pf_root.
    if powerflow_manifest:
        cand = _resolve(powerflow_manifest)
        if os.path.isdir(cand) and os.path.basename(cand).startswith("v"):
            # single-version hint ignored; discovery is authoritative
            pass
        elif cand.endswith("powerflow_summary.json"):
            # parent of version folder → still use pf_root
            pass

    tags = _discover_pf_versions(pf_root)
    if not tags:
        raise FileNotFoundError(
            f"No outputs/powerflow/<version>/powerflow_summary.json under "
            f"{_rel(pf_root)}. Run Step 3 first."
        )

    per_version: Dict[str, Any] = {}
    for tag in tags:
        summary_path = os.path.join(pf_root, tag, "powerflow_summary.json")
        with open(summary_path) as f:
            summary = json.load(f)
        result = _decide_one(summary, summary_path, config, _resolve, _rel)
        ver_dir = os.path.join(dec_root, tag)
        os.makedirs(ver_dir, exist_ok=True)
        out_path = os.path.join(ver_dir, "decisions.json")
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        per_version[tag] = result
        if config.get("logging", {}).get("verbose", True):
            c = result["congestion"]
            print(
                f"[decisions] {tag}: lines≥alert="
                f"{c.get('n_lines_over_alert')}, "
                f"buses_out_of_band={c.get('n_buses_outside_voltage_band')}, "
                f"storage_mw={result['storage_heuristic'].get('storage_action_mw')} "
                f"→ {_rel(out_path)}"
            )

    comparison = {
        "versions": tags,
        "by_version": {
            tag: {
                "include_shunts": per_version[tag].get("include_shunts"),
                "n_lines_over_alert": per_version[tag]["congestion"].get(
                    "n_lines_over_alert"
                ),
                "n_buses_outside_voltage_band": per_version[tag]["congestion"].get(
                    "n_buses_outside_voltage_band"
                ),
                "buses_outside_band": per_version[tag]["congestion"].get(
                    "buses_outside_band"
                ),
                "storage_action_mw": per_version[tag]["storage_heuristic"].get(
                    "storage_action_mw"
                ),
                "max_loading_percent": per_version[tag]["storage_heuristic"].get(
                    "max_loading_percent"
                ),
                "focus_vm_pu": per_version[tag].get("focus_vm_pu", {}),
                "decisions_json": _rel(os.path.join(dec_root, tag, "decisions.json")),
            }
            for tag in tags
        },
    }
    if len(tags) >= 2:
        # pairwise deltas vs first version (usually v1)
        base_tag = tags[0]
        deltas = {}
        for tag in tags[1:]:
            d_vm = {}
            for bid, vm in per_version[tag].get("focus_vm_pu", {}).items():
                vm0 = per_version[base_tag].get("focus_vm_pu", {}).get(bid)
                if vm0 is not None:
                    d_vm[bid] = vm - vm0
            deltas[f"{tag}_minus_{base_tag}"] = {
                "delta_focus_vm_pu": d_vm,
                "delta_n_buses_outside_band": (
                    (per_version[tag]["congestion"].get("n_buses_outside_voltage_band") or 0)
                    - (
                        per_version[base_tag]["congestion"].get(
                            "n_buses_outside_voltage_band"
                        )
                        or 0
                    )
                ),
                "delta_n_lines_over_alert": (
                    (per_version[tag]["congestion"].get("n_lines_over_alert") or 0)
                    - (per_version[base_tag]["congestion"].get("n_lines_over_alert") or 0)
                ),
            }
        comparison["deltas"] = deltas

    cmp_path = os.path.join(dec_root, "decisions_comparison.json")
    with open(cmp_path, "w") as f:
        json.dump(comparison, f, indent=2)

    # Remove obsolete single-file artifacts if present
    for obsolete in ("decisions.json", "decisions_template.json"):
        p = os.path.join(dec_root, obsolete)
        if os.path.isfile(p):
            os.remove(p)

    out_rel = _rel(cmp_path)
    if config.get("logging", {}).get("verbose", True):
        print(f"[decisions] comparison → {out_rel}")

    return out_rel
