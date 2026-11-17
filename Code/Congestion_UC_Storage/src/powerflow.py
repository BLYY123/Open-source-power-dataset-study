"""
Step 3 — Solve a simplified SAVNW-like network under mapped injections.

We do **not** use PSS/E or OpenDSS (no license / Windows COM on this path).
Instead we build a **teaching-scale** pandapower network that keeps the same
named load / gen / wind buses as the PSML SAVNW case, with a reduced set of
lines. Distribution feeders are ignored (loads at 3005/3008 stay on T).

Versions
--------
- **v1** (``include_shunts: false``): topology only — no fixed capacitors/reactors.
- **v2** (``include_shunts: true``): same topology + RAW fixed-shunt BL on
  buses 151 / 154 / 201 / 203 / 205, to show voltage support from capacitors.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Base PQ loads (MW / Mvar) — Re_wind_v3/savnw_wind_scale_down.raw load section.
LOAD_BASE: Dict[int, Tuple[float, float]] = {
    153: (148.0, 74.0),
    154: (740.0, 592.0),
    203: (222.0, 518.0),
    205: (768.0, 111.0),
    3005: (74.0, 37.0),
    3007: (148.0, 55.5),
    3008: (128.0, 75.0),
}

# Fixed shunts from RAW (GL, BL). BL>0 capacitor, BL<0 reactor at |V|=1 pu.
# pandapower create_shunt: q_mvar >0 = inductive (consumes Q); <0 = capacitive.
SHUNT_BL_MVAR: Dict[int, float] = {
    151: -600.0,  # reactor
    154: 300.0,  # capacitor
    201: 300.0,
    203: 50.0,
    205: 300.0,
}

# Focus buses for v1 vs v2 voltage comparison (were under-voltage without caps).
COMPARE_VM_BUSES = [154, 203, 205, 3008]

TRAD_GEN_BUS = [101, 102, 206, 211, 3011]
TRAD_GEN_PCT = [9.0, 9.0, 10.0, 7.25, 10.0]
WIND_BUS = 3018


def build_simplified_savnw_net(include_shunts: bool = False):
    """
    Teaching network with SAVNW bus names and a sparse 230 kV mesh.

    Includes plant / hydro intermediate buses **151** and **201** (still no
    full 500 kV layer). Optional fixed shunts match RAW BL on buses that exist
    in this net.
    """
    import pandapower as pp

    net = pp.create_empty_network(name="savnw_simplified_T", sn_mva=100.0)
    vn = 230.0

    buses = {}
    names = {
        101: "NUC-A",
        102: "NUC-B",
        151: "NUCPANT",  # was dropped; gens 101/102 attach here in RAW
        153: "MID230",
        154: "DOWNTN",
        201: "HYDRO",  # was dropped; gen 211 attaches via this corridor
        203: "EAST230",
        205: "SUB230",
        206: "URBGEN",
        211: "HYDRO_G",
        3005: "WEST",
        3007: "RURAL",
        3008: "CATDOG",
        3011: "MINE_G",
        3018: "CATDOG_G",
    }
    for num, name in names.items():
        buses[num] = pp.create_bus(net, vn_kv=vn, name=str(num), zone=name)

    def line(f: int, t: int, r_pu: float, x_pu: float, rate_mva: float, name: str):
        z_base = vn**2 / 100.0
        pp.create_line_from_parameters(
            net,
            from_bus=buses[f],
            to_bus=buses[t],
            length_km=1.0,
            r_ohm_per_km=r_pu * z_base,
            x_ohm_per_km=x_pu * z_base,
            c_nf_per_km=0.0,
            max_i_ka=rate_mva / (vn * 1.732),
            name=name,
        )

    # Gen → intermediate → load corridors (closer to RAW than direct 101-153).
    line(101, 151, 0.0003, 0.014, 1250, "101-151")
    line(102, 151, 0.0003, 0.014, 1250, "102-151")
    line(151, 153, 0.002, 0.02, 800, "151-153")
    line(153, 154, 0.005, 0.045, 300, "153-154")
    line(154, 203, 0.004, 0.04, 200, "154-203")
    line(154, 205, 0.00033, 0.00333, 600, "154-205")
    line(154, 3008, 0.0027, 0.022, 400, "154-3008")
    line(203, 205, 0.005, 0.045, 200, "203-205")
    line(205, 206, 0.001, 0.015, 900, "205-206")
    line(211, 201, 0.0007, 0.021, 800, "211-201")
    line(201, 203, 0.002, 0.025, 800, "201-203")
    line(3005, 153, 0.004, 0.035, 400, "3005-153")
    line(3005, 3007, 0.003, 0.025, 300, "3005-3007")
    line(3005, 3008, 0.006, 0.05, 300, "3005-3008")
    line(3007, 3008, 0.003, 0.025, 300, "3007-3008")
    line(3011, 3005, 0.002, 0.02, 800, "3011-3005")
    line(3018, 3008, 0.00021, 0.085, 150, "3018-3008")

    for bus_num, (p, q) in LOAD_BASE.items():
        pp.create_load(
            net,
            bus=buses[bus_num],
            p_mw=p,
            q_mvar=q,
            name=f"load_{bus_num}",
        )

    if include_shunts:
        for bus_num, bl in SHUNT_BL_MVAR.items():
            # RAW BL>0 capacitor → pandapower q_mvar < 0 (generates Q)
            pp.create_shunt(
                net,
                bus=buses[bus_num],
                p_mw=0.0,
                q_mvar=-bl,
                name=f"shunt_{bus_num}",
            )

    for bus_num in (101, 102, 206, 211):
        pp.create_gen(
            net,
            bus=buses[bus_num],
            p_mw=0.0,
            vm_pu=1.02 if bus_num in (101, 102) else 1.0,
            name=f"gen_{bus_num}",
        )

    pp.create_gen(
        net,
        bus=buses[WIND_BUS],
        p_mw=0.0,
        vm_pu=1.02,
        name="wind_3018",
        max_p_mw=130.0,
        min_p_mw=0.0,
    )

    pp.create_ext_grid(net, bus=buses[3011], vm_pu=1.04, name="slack_3011")

    net.bus["savnw_id"] = [int(n) for n in net.bus.name]
    return net


def apply_recipe_and_dispatch(net, recipe: Dict[str, Any], config: Dict[str, Any]):
    """Scale loads, set wind, solar placeholder, cosim-style gen split."""
    profiles = recipe["profiles"]
    scale = float(profiles["load_power_mean"])
    solar = float(profiles.get("solar_power_mean", 0.0))
    wind_bus = int(config.get("mapping", {}).get("wind_bus", WIND_BUS))
    wind_mw = float(recipe["machines"][str(wind_bus)]["p_mw"])

    dec_cfg = config.get("decisions", {})
    trad_bus = [int(b) for b in dec_cfg.get("trad_gen_bus", TRAD_GEN_BUS)]
    trad_pct = [float(p) for p in dec_cfg.get("trad_gen_pct", TRAD_GEN_PCT)]

    for i, load in net.load.iterrows():
        bus_savnw = int(str(load["name"]).split("_")[-1])
        p0, q0 = LOAD_BASE[bus_savnw]
        p, q = p0 * scale, q0 * scale
        if bus_savnw in (3005, 3008):
            p *= max(0.0, 1.0 - solar)
            q *= max(0.0, 1.0 - solar)
        net.load.at[i, "p_mw"] = p
        net.load.at[i, "q_mvar"] = q

    wind_rows = net.gen.index[net.gen.name == "wind_3018"]
    net.gen.at[wind_rows[0], "p_mw"] = max(0.0, wind_mw)

    p_load = float(net.load.p_mw.sum())
    pg_total = p_load - max(0.0, wind_mw)
    pct_all = sum(trad_pct)
    targets = {
        bus: pg_total * pct / pct_all for bus, pct in zip(trad_bus, trad_pct)
    }
    for bus_num in (101, 102, 206, 211):
        rows = net.gen.index[net.gen.name == f"gen_{bus_num}"]
        net.gen.at[rows[0], "p_mw"] = targets.get(bus_num, 0.0)
    return {
        "scale": scale,
        "solar_mean": solar,
        "wind_mw": wind_mw,
        "p_load_mw": p_load,
        "pg_total_conventional_mw": pg_total,
        "gen_targets_mw": {str(k): v for k, v in targets.items()},
        "trad_gen_pct": trad_pct,
    }


def _pkg_paths(config: Dict[str, Any]):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_cfg = config["outputs"]

    def _resolve(path: str) -> str:
        return (
            os.path.normpath(path)
            if os.path.isabs(path)
            else os.path.normpath(os.path.join(base, path))
        )

    def _rel(path: str) -> str:
        return os.path.relpath(_resolve(path), base)

    return base, out_cfg, _resolve, _rel


def _solve_one(
    config: Dict[str, Any],
    recipe: Dict[str, Any],
    recipe_rel: str,
    include_shunts: bool,
    version_tag: str,
    _resolve,
    _rel,
) -> Dict[str, Any]:
    import pandapower as pp

    out_cfg = config["outputs"]
    pf_root = _resolve(
        os.path.join(out_cfg["dir"], out_cfg.get("powerflow_subdir", "powerflow"))
    )
    pf_dir = os.path.join(pf_root, version_tag)
    os.makedirs(pf_dir, exist_ok=True)

    net = build_simplified_savnw_net(include_shunts=include_shunts)
    dispatch_info = apply_recipe_and_dispatch(net, recipe, config)

    converged = True
    err = None
    try:
        pp.runpp(net, calculate_voltage_angles=True, numba=False)
    except Exception as exc:  # noqa: BLE001
        converged = False
        err = str(exc)

    bus_out = net.bus[["name", "vn_kv"]].copy()
    bus_out["savnw_id"] = net.bus["savnw_id"]
    if converged:
        bus_out["vm_pu"] = net.res_bus.vm_pu
        bus_out["va_degree"] = net.res_bus.va_degree
        bus_out["p_mw"] = net.res_bus.p_mw
        bus_out["q_mvar"] = net.res_bus.q_mvar
        line_out = net.line[["name", "from_bus", "to_bus", "max_i_ka"]].copy()
        line_out["p_from_mw"] = net.res_line.p_from_mw
        line_out["q_from_mvar"] = net.res_line.q_from_mvar
        line_out["loading_percent"] = net.res_line.loading_percent
        line_out["i_ka"] = net.res_line.i_from_ka
    else:
        line_out = net.line[["name", "from_bus", "to_bus", "max_i_ka"]].copy()

    bus_csv = os.path.join(pf_dir, "res_bus.csv")
    line_csv = os.path.join(pf_dir, "res_line.csv")
    bus_out.to_csv(bus_csv, index=True)
    line_out.to_csv(line_csv, index=True)

    focus_vm = {}
    if converged:
        for bid in COMPARE_VM_BUSES:
            row = bus_out.loc[bus_out["savnw_id"] == bid]
            if len(row):
                focus_vm[str(bid)] = float(row["vm_pu"].iloc[0])

    summary = {
        "status": "ok" if converged else "failed",
        "version": version_tag,
        "include_shunts": include_shunts,
        "shunts_modeled": (
            {str(k): v for k, v in SHUNT_BL_MVAR.items()} if include_shunts else {}
        ),
        "solver": "pandapower.runpp",
        "network": (
            "simplified SAVNW-like (+151,+201; "
            + ("with fixed shunts" if include_shunts else "no shunts")
            + "; T only)"
        ),
        "recipe_path": recipe_rel,
        "origin_time": recipe.get("origin_time"),
        "horizon_hours": recipe.get("horizon_hours"),
        "converged": converged,
        "error": err,
        "dispatch": dispatch_info,
        "res_bus_csv": _rel(bus_csv),
        "res_line_csv": _rel(line_csv),
        "n_buses": int(len(net.bus)),
        "n_lines": int(len(net.line)),
        "n_shunts": int(len(net.shunt)) if hasattr(net, "shunt") else 0,
        "vm_pu_min": float(bus_out["vm_pu"].min()) if converged else None,
        "vm_pu_max": float(bus_out["vm_pu"].max()) if converged else None,
        "loading_percent_max": (
            float(line_out["loading_percent"].max()) if converged else None
        ),
        "focus_vm_pu": focus_vm,
    }
    out_path = os.path.join(pf_dir, "powerflow_summary.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def run_powerflow_step(
    config: Dict[str, Any],
    recipe_json: Optional[str] = None,
) -> str:
    """
    Step-3: build simplified net, apply recipe, runpp, write results.

    Writes only under ``outputs/powerflow/<version>/`` (no root res_*.csv).
    If ``run_both_shunt_versions`` is true, runs v1+v2 and a voltage compare.

    Returns package-relative path to the powerflow root directory.
    """
    base, out_cfg, _resolve, _rel = _pkg_paths(config)
    pf_root = _resolve(
        os.path.join(out_cfg["dir"], out_cfg.get("powerflow_subdir", "powerflow"))
    )
    os.makedirs(pf_root, exist_ok=True)

    if recipe_json is None:
        map_dir = _resolve(
            os.path.join(out_cfg["dir"], out_cfg.get("mapping_subdir", "mapping"))
        )
        recipes = sorted(
            f for f in os.listdir(map_dir) if f.startswith("injection_recipe_")
        )
        if not recipes:
            raise FileNotFoundError(
                f"No injection_recipe_*.json in {_rel(map_dir)}. Run Step 2 first."
            )
        recipe_json = os.path.join(map_dir, recipes[-1])
    else:
        recipe_json = _resolve(recipe_json)

    with open(recipe_json) as f:
        recipe = json.load(f)
    recipe_rel = _rel(recipe_json)

    pf_cfg = config.get("powerflow", {})
    run_both = bool(pf_cfg.get("run_both_shunt_versions", True))
    include_default = bool(pf_cfg.get("include_shunts", False))

    versions: List[Tuple[str, bool]] = []
    if run_both:
        versions = [("v1", False), ("v2", True)]
    else:
        versions = [("v2", True) if include_default else ("v1", False)]

    summaries = []
    for tag, with_shunt in versions:
        s = _solve_one(
            config, recipe, recipe_rel, with_shunt, tag, _resolve, _rel
        )
        summaries.append(s)
        if config.get("logging", {}).get("verbose", True):
            print(
                f"[powerflow] {tag} shunts={with_shunt} converged={s['converged']} "
                f"vm∈[{s['vm_pu_min']},{s['vm_pu_max']}] "
                f"→ {s['res_bus_csv']}"
            )

    # Index of version folders only (no root res_*.csv / powerflow_summary.json).
    index = {
        "versions": [s["version"] for s in summaries],
        "summaries": {
            s["version"]: {
                "include_shunts": s.get("include_shunts"),
                "powerflow_summary": s["res_bus_csv"].replace(
                    "res_bus.csv", "powerflow_summary.json"
                ),
                "res_bus_csv": s["res_bus_csv"],
                "res_line_csv": s["res_line_csv"],
                "focus_vm_pu": s.get("focus_vm_pu", {}),
            }
            for s in summaries
        },
    }
    if len(summaries) >= 2:
        by_tag = {s["version"]: s for s in summaries}
        if "v1" in by_tag and "v2" in by_tag:
            v1, v2 = by_tag["v1"], by_tag["v2"]
            compare = {
                "question": (
                    "Do RAW fixed capacitors/reactors raise vm_pu on stressed buses?"
                ),
                "focus_buses": COMPARE_VM_BUSES,
                "v1_no_shunts": v1.get("focus_vm_pu", {}),
                "v2_with_shunts": v2.get("focus_vm_pu", {}),
                "delta_vm_pu_v2_minus_v1": {},
                "shunts_added": {
                    str(k): {
                        "BL_mvar_raw": v,
                        "type": "capacitor" if v > 0 else "reactor",
                    }
                    for k, v in SHUNT_BL_MVAR.items()
                },
                "note": (
                    "3008 has no shunt in RAW; any vm change there is indirect "
                    "from caps on 154/203/205 (and reactor on 151)."
                ),
            }
            for bid in COMPARE_VM_BUSES:
                a = v1.get("focus_vm_pu", {}).get(str(bid))
                b = v2.get("focus_vm_pu", {}).get(str(bid))
                if a is not None and b is not None:
                    compare["delta_vm_pu_v2_minus_v1"][str(bid)] = b - a
            index["shunt_voltage_comparison"] = compare
            cmp_path = os.path.join(pf_root, "shunt_voltage_comparison.json")
            with open(cmp_path, "w") as f:
                json.dump(compare, f, indent=2)
            if config.get("logging", {}).get("verbose", True):
                print(f"[powerflow] wrote {_rel(cmp_path)}")
                for bid, d in compare["delta_vm_pu_v2_minus_v1"].items():
                    print(f"[powerflow]   bus {bid}: Δvm_pu = {d:+.4f}")

    index_path = os.path.join(pf_root, "versions_index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    # Drop obsolete root-level result files if they still exist.
    for obsolete in (
        "powerflow_summary.json",
        "powerflow_manifest.json",
        "res_bus.csv",
        "res_line.csv",
    ):
        p = os.path.join(pf_root, obsolete)
        if os.path.isfile(p):
            os.remove(p)

    if config.get("logging", {}).get("verbose", True):
        print(f"[powerflow] versions index → {_rel(index_path)}")

    return _rel(pf_root)
