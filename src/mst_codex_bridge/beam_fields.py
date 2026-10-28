"""EB Hermite recovery from verified source-coordinate nodal 6DOFs."""
from __future__ import annotations
import csv
import io
import numpy as np
from .files import digest, dumps, load_json
from .model import canonical
from .results import csv_text


def hermite_field(xi, length, axis, ui, uj, ri, rj):
    chord = (1-xi)*ui+xi*uj
    transverse_i = ui-axis*np.dot(ui, axis)
    transverse_j = uj-axis*np.dot(uj, axis)
    h1, h2 = 1-3*xi**2+2*xi**3, xi-2*xi**2+xi**3
    h3, h4 = 3*xi**2-2*xi**3, -xi**2+xi**3
    value = axis*np.dot(chord, axis)+h1*transverse_i+h2*length*np.cross(ri, axis)+h3*transverse_j+h4*length*np.cross(rj, axis)
    return value, value-chord


def recover(bridge, model_path: str, displacement_csv: str, out: str, stations: int = 11):
    if not isinstance(stations, int) or not 2 <= stations <= 1001:
        raise ValueError("stations must be 2..1001")
    model = bridge.model(model_path)
    p = bridge.ws.path(displacement_csv)
    raw = p.read_bytes()
    ingestion_path = bridge.ws.path(bridge.ws.relative(p.parent/"ingestion.json"))
    ingestion = load_json(ingestion_path)
    binding = ingestion.get("verified_model_binding") or {}
    if ingestion.get("status") != "PASS" or not ingestion.get("quantitative_beam_recovery_eligible") or ingestion.get("provenance") != "LOG_constrained_GRD_float32":
        raise ValueError("Beam recovery requires high-precision native results bound to a passing model comparison")
    if ingestion.get("outputs", {}).get(p.name) != digest(raw):
        raise ValueError("Displacement CSV hash differs from its ingestion receipt")
    if binding.get("canonical_model_sha256") != digest(dumps(canonical(model)).encode("utf-8")):
        raise ValueError("Displacement results belong to a different model")
    records = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    cols = ["source_ux_m", "source_uy_m", "source_uz_m", "source_rx_rad", "source_ry_rad", "source_rz_rad"]
    nodes = {n.id: np.array(n.xyz_m) for n in model.nodes}
    values = {}
    cases = set()
    for row in records:
        nid = row["source_node_id"]
        if nid in values or nid not in nodes:
            raise ValueError("Displacement CSV requires unique source IDs from the model")
        values[nid] = np.array([float(row[c]) for c in cols])
        if not np.isfinite(values[nid]).all():
            raise ValueError("Nonfinite displacement component")
        cases.add(row["case"])
    if set(values) != set(nodes) or len(cases) != 1:
        raise ValueError("CSV must cover every model node once in exactly one case")
    field_rows, maxima_rows = [], []
    for member in sorted(model.members, key=lambda m: m.id):
        delta = nodes[member.node_j]-nodes[member.node_i]
        length = np.linalg.norm(delta); axis = delta/length
        vi, vj = values[member.node_i], values[member.node_j]
        bows = []
        for xi in np.linspace(0, 1, stations):
            u, bow = hermite_field(xi, length, axis, vi[:3], vj[:3], vi[3:], vj[3:])
            magnitude = float(np.linalg.norm(bow))
            bows.append((magnitude, float(xi)))
            field_rows.append([member.id, next(iter(cases)), float(xi), *u, *bow, magnitude])
        largest = max(bows)
        maxima_rows.append([member.id, largest[0], largest[1]])
    if digest(p.read_bytes()) != digest(raw):
        raise ValueError("Source CSV changed during recovery")
    receipt = {"operation": "recover_beam_fields", "status": "RECOVERED", "members": len(model.members), "stations_per_member": stations,
        "rows": len(field_rows), "source_csv_sha256": digest(raw), "model_sha256": digest(bridge.ws.path(model_path).read_bytes()),
        "method": "Euler-Bernoulli cubic Hermite interpolation; rotations are axial vectors in source Z-up frame",
        "maxima_scope": "sampled station maxima only; not exact continuous extrema",
        "provenance": "input_nodal_6DOF_then_EB_shape_recovery", "direct_MST_station_export": False,
        "upstream_provenance": ingestion["provenance"], "verified_model_binding": binding,
        "ingestion_receipt_sha256": digest(ingestion_path.read_bytes()),
        "distributed_member_load_particular_solution_included": False,
        "training_label_release": False, "solver_correctness_claim": False}
    return bridge.ws.bundle(out, {
        "beam_fields.csv": csv_text(["source_member_id", "case", "xi", "ux_m", "uy_m", "uz_m", "chord_relative_x_m", "chord_relative_y_m", "chord_relative_z_m", "chord_relative_norm_m"], field_rows),
        "sampled_deflection_maxima.csv": csv_text(["source_member_id", "max_sampled_chord_relative_norm_m", "xi"], maxima_rows),
        "recovery.json": dumps(receipt)})
