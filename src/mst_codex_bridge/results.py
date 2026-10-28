"""Roundtrip checks and evidence-constrained result ingestion."""
from __future__ import annotations
import csv
import io
import math
import numpy as np

from .files import digest, dumps, load_json
from .model import canonical
from .mst_format import decode_log, generate, geometry, number, section_args
from .native_precision import match_case, parse_cases


def csv_text(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns); writer.writerows(rows)
    return "\ufeff"+stream.getvalue()


def unique_records(rows, key_count=1):
    result = {}
    for row in rows:
        key = tuple(int(x) for x in row[:key_count])
        if key in result:
            raise ValueError(f"Duplicate native record {key}")
        result[key] = row[key_count:]
    return result


def nodal_loads(rows):
    data = {}
    for block in ("DEADLOAD", "DEADLOAD1", "LIVELOAD"):
        seen = set()
        for row in rows.get(block, []):
            live = block == "LIVELOAD"
            key = ("live", int(row[0]), int(row[1])) if live else ("dead", 0, int(row[0]))
            if key in seen:
                raise ValueError("Repeated load row in native LOG")
            seen.add(key)
            values = [number(x) for x in row[2 if live else 1:]]
            if len(values) not in (3, 6) or (live and len(values) != 3):
                raise ValueError("Unexpected nodal load format")
            values += [0.0]*(6-len(values))
            if key in data:
                raise ValueError("Same node occurs in DEADLOAD and DEADLOAD1; contribution meaning ambiguous")
            data[key] = np.array(values)
    return data


def compare(bridge, job: str, log: str, out: str):
    jobpath = bridge.ws.path(job)
    model = bridge.model(bridge.ws.relative(jobpath/"model.json"))
    expected, mapping = generate(model)
    if load_json(jobpath/"mapping.json") != mapping:
        raise ValueError("Mapping differs from deterministic model IDs")
    inputraw = (jobpath/"model.log").read_bytes()
    if inputraw != expected:
        raise ValueError("Prepared LOG changed relative to model.json")
    source = bridge.ws.path(log)
    raw = source.read_bytes()
    native, encoding = decode_log(raw)
    en, eb, er = geometry(expected.decode("ascii"))
    an, ab, ar = geometry(native)
    problems = []
    coordinate_errors = []
    if set(en) != set(an):
        problems.append("NODE_ID_SET_MISMATCH")
    for n in set(en)&set(an):
        coordinate_errors.append(max(abs(x-y) for x, y in zip(en[n], an[n])))
    max_coord = max(coordinate_errors, default=0)
    if max_coord > 2e-5:
        problems.append("COORDINATE_ERROR_EXCEEDS_0.02_MM")
    expected_attrs = unique_records(er["NODECORD2"])
    actual_attrs = unique_records(ar.get("NODECORD2", []))
    if any(k not in actual_attrs or len(actual_attrs[k]) < 5 or actual_attrs[k][4] != "1" for k in expected_attrs):
        problems.append("NODE_SIX_DOF_ATTRIBUTE_MISMATCH")
    reversed_ends = []
    if set(eb) != set(ab):
        problems.append("MEMBER_ID_SET_MISMATCH")
    for mid in set(eb)&set(ab):
        a, b = eb[mid], ab[mid]
        if a[:2] != b[:2]:
            if a[:2] == b[:2][::-1]:
                reversed_ends.append(mid)
            else:
                problems.append(f"MEMBER_CONNECTIVITY_MISMATCH:{mid}")
        if a[2:4] != b[2:4] or abs(a[4]-b[4]) > 1e-6:
            problems.append(f"MEMBER_SECTION_OR_ALPHA_MISMATCH:{mid}")
    es = unique_records(er.get("SUPPORT INFORMATION", []))
    acs = unique_records(ar.get("SUPPORT INFORMATION", []))
    if es != acs:
        problems.append("SUPPORT_MISMATCH")
    se = unique_records(er.get("SECTION PROPERTIES", []), 2)
    sa = unique_records(ar.get("SECTION PROPERTIES", []), 2)
    for key, expected_values in se.items():
        actual_values = sa.get(key, [])
        if len(expected_values) != len(actual_values) or any(abs(number(x)-number(y)) > .0051 for x, y in zip(expected_values, actual_values)):
            problems.append(f"SECTION_GEOMETRY_MISMATCH:{key}")
    le, la = nodal_loads(er), nodal_loads(ar)
    load_error = 0.0
    for key in set(le)|set(la):
        error = float(np.max(np.abs(le.get(key, np.zeros(6))-la.get(key, np.zeros(6)))))
        load_error = max(load_error, error)
        if error > .000501:
            problems.append(f"NODAL_LOAD_MISMATCH:{key}")
    material = model.materials[0]
    areas = unique_records(ar.get("AREAINFO", []), 3)
    observed = []
    for key in se:
        values = areas.get((*key, 0), [])
        if len(values) != 8:
            problems.append(f"MISSING_AREAINFO:{key}")
            continue
        v = [number(x) for x in values]
        observed.append({"shape": key[0], "section": key[1], "A_mm2": v[0], "I1_mm4": v[1], "I2_mm4": v[2], "J_mm4": v[3],
                         "E_MPa": v[4], "G_MPa": v[5], "poisson": v[6], "weight_density_N_mm3": v[7]})
        if min(v[:6]) <= 0:
            problems.append(f"NONPOSITIVE_SECTION_OR_MATERIAL_PROPERTY:{key}")
        for label, actual, target, tol in [("E", v[4], material.E_MPa, .0051), ("G", v[5], material.G_MPa, .0051),
                ("nu", v[6], material.poisson, .0051), ("weight_density", v[7], material.density_kg_m3*9.80665/1e9, .5001e-8)]:
            if abs(actual-target) > tol:
                problems.append(f"MATERIAL_{label}_MISMATCH:{key}")
    unchanged = digest(source.read_bytes()) == digest(raw)
    if not unchanged:
        problems.append("SOURCE_CHANGED_DURING_READ")
    report = {"operation": "compare", "status": "PASS" if not problems else "FAILED",
        "status_meaning": "model parameters match within declared tolerances" if not problems else "parameter comparison failed; inspect problems before using results",
        "solver_result_validated": False, "training_label_release": False,
        "native_log_sha256": digest(raw), "prepared_log_sha256": digest(expected), "encoding": encoding,
        "canonical_model_sha256": digest(dumps(canonical(model)).encode("utf-8")),
        "mapping_sha256": digest(dumps(mapping).encode("utf-8")),
        "coordinate_tolerance_m": 2e-5, "max_coordinate_component_error_m": max_coord,
        "load_component_tolerance_kN_or_kNm": .000501, "max_load_component_error": load_error,
        "reversed_member_ids": reversed_ends, "reversed_end_note": "geometry accepted; member-local force signs require separate handling",
        "observed_section_properties": observed, "problems": problems,
        "unverified_settings": ["automatic selfweight state", "design check mode", "combination coefficients"],
        "source_hash_unchanged": unchanged}
    bundle = bridge.ws.bundle(out, {"comparison.json": dumps(report)})
    return {"status": report["status"], "problems": problems, **bundle}


def ingest(bridge, log: str, out: str, grd: str | None = None, mapping_path: str | None = None, selected_cases: list[str] | None = None,
           job: str | None = None, comparison_path: str | None = None):
    lp = bridge.ws.path(log)
    raw = lp.read_bytes()
    txt, encoding = decode_log(raw)
    cases, excluded = parse_cases(txt)
    available = [c.key for c in cases]
    if selected_cases:
        if set(selected_cases)-set(available):
            raise ValueError(f"Unknown cases; available {available}")
        cases = [c for c in cases if c.key in selected_cases]
    if not cases:
        raise ValueError("No selected result cases")
    source_map = None
    if mapping_path:
        mp = bridge.ws.path(mapping_path)
        if mp.suffix.lower() == ".csv":
            map_rows = list(csv.DictReader(io.StringIO(mp.read_text(encoding="utf-8-sig"))))
            active = [r for r in map_rows if r.get("mst_node_id", "").strip()]
            mapping = {"nodes": {r["source_node_id"]: int(r["mst_node_id"]) for r in active}}
            if len(mapping["nodes"]) != len(active):
                raise ValueError("Duplicate source IDs in mapping CSV")
        else:
            mapping = load_json(mp)
        vals = list(mapping["nodes"].values())
        if len(vals) != len(set(vals)) or set(vals) != set(cases[0].node_ids):
            raise ValueError("Mapping must cover all LOG nodes once")
        source_map = {v: k for k, v in mapping["nodes"].items()}
    binding = None
    if bool(job) != bool(comparison_path):
        raise ValueError("job and comparison_path must be supplied together")
    if job:
        if not mapping_path:
            raise ValueError("Bound ingestion requires the job's mapping.json")
        jp = bridge.ws.path(job)
        model = bridge.model(bridge.ws.relative(jp/"model.json"))
        expected_log, expected_map = generate(model)
        model_hash = digest(dumps(canonical(model)).encode("utf-8"))
        map_hash = digest(dumps(expected_map).encode("utf-8"))
        if (jp/"model.log").read_bytes() != expected_log or mapping != expected_map:
            raise ValueError("Prepared model, LOG or supplied mapping mismatch")
        cp = bridge.ws.path(comparison_path)
        check = load_json(cp)
        if check.get("status") != "PASS" or check.get("native_log_sha256") != digest(raw) or check.get("prepared_log_sha256") != digest(expected_log) or check.get("canonical_model_sha256") != model_hash or check.get("mapping_sha256") != map_hash:
            raise ValueError("Comparison receipt does not bind this model, mapping, input and native LOG")
        binding = {"canonical_model_sha256": model_hash, "mapping_sha256": map_hash,
                   "prepared_log_sha256": digest(expected_log), "comparison_receipt_sha256": digest(cp.read_bytes())}
    gp = bridge.ws.path(grd) if grd else None
    data = gp.read_bytes() if gp else None
    hashes = {"log": digest(raw), **({"grd": digest(data)} if data is not None else {})}
    reports, pending = [], {}
    for case in cases:
        if data is not None:
            report, values = match_case(data, case)
        else:
            report = {"case": case.key, "status": "PASS", "precision": "printed_LOG_only"}
            values = np.array([float(x) for x in case.tokens]).reshape(-1, 6)
        reports.append(report)
        if values is None:
            continue
        rows = []
        for node, row in zip(case.node_ids, values):
            source_values = row*np.array([.001, .001, -.001, -1., -1., 1.])
            rows.append([source_map[node] if source_map else "", node, case.key, *[format(float(x), ".17g") for x in row],
                         *[format(float(x), ".17g") for x in source_values]])
        columns = ["source_node_id", "mst_node_id", "case", "mst_ux_mm", "mst_uy_mm", "mst_uz_mm", "mst_rx_rad", "mst_ry_rad", "mst_rz_rad",
                   "source_ux_m", "source_uy_m", "source_uz_m", "source_rx_rad", "source_ry_rad", "source_rz_rad"]
        pending[case.key+"_displacements.csv"] = csv_text(columns, rows)
    stable = digest(lp.read_bytes()) == hashes["log"] and (gp is None or digest(gp.read_bytes()) == hashes["grd"])
    success = stable and all(r["status"] == "PASS" for r in reports)
    # One requested case failing prevents partial datasets from looking complete.
    files = pending if success else {}
    receipt = {"operation": "ingest", "status": "PASS" if success else "FAILED", "hashes": hashes, "encoding": encoding,
        "available_independent_cases": available, "selected_cases": [c.key for c in cases], "cases": reports,
        "excluded_combined_blocks": excluded, "source_hashes_unchanged": stable,
        "source_id_mapping_supplied": source_map is not None, "native_binary_written": False,
        "verified_model_binding": binding,
        "quantitative_beam_recovery_eligible": success and binding is not None and gp is not None,
        "provenance": "LOG_constrained_GRD_float32" if gp else "MST_printed_LOG_rounded",
        "node_result_export": success and bool(files), "direct_MST_station_export": False,
        "training_label_release": False, "solver_correctness_claim": False,
        "coordinate_transform": "u=(ux,uy,-uz)/1000 m; axial rotation=(-rx,-ry,rz) rad",
        "outputs": {name: digest(value.encode("utf-8")) for name, value in files.items()}}
    files["ingestion.json"] = dumps(receipt)
    bundle = bridge.ws.bundle(out, files)
    return {"status": receipt["status"], "cases": [{"case": r["case"], "status": r["status"], "reason": r.get("reason")} for r in reports], **bundle}
