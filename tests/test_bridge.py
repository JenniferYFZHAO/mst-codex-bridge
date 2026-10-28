import copy
import json
import math
from pathlib import Path
import numpy as np
import pytest
from mst_codex_bridge.model import Model
from mst_codex_bridge.files import load_json
from mst_codex_bridge.service import Bridge
from mst_codex_bridge.mst_format import generate, geometry
from mst_codex_bridge.native_precision import Case, match_case, parse_cases
from mst_codex_bridge.results import compare, ingest
from mst_codex_bridge.beam_fields import hermite_field, recover


def fixture_result(model_data, tmp_path):
    bridge = Bridge(tmp_path)
    bridge.create(model_data, "input")
    bridge.prepare("input/model.json", "job")
    raw = (tmp_path/"job/model.log").read_text(encoding="ascii")
    inertia = math.pi*(114**4-106**4)/64
    displacement = 1000*3000**3/(3*206000*inertia)
    rotation = -1000*3000**2/(2*206000*inertia)
    values = np.array([[0., 0., 0., 0., 0., 0.], [0., 0., displacement, 0., rotation, 0.]], dtype="<f4")
    props = f"/AREAINFO/\n1 1 0 1382.30 {inertia:.2f} {inertia:.2f} {2*inertia:.2f} 206000.00 79000.00 0.30 0.00007698\n##\n"
    rows = [str(i+1)+" "+" ".join(f"{x:.2f}" for x in row) for i, row in enumerate(values)]
    text = raw.replace("/END/", props+"/RESDEADLOAD/\n//(U)/\n"+"\n".join(rows)+"\n##\n/END/")
    (tmp_path/"native.log").write_text(text, encoding="ascii")
    # This is intentionally synthetic binary data, not a claimed complete GRD format.
    (tmp_path/"native.grd").write_bytes(b"Synthetic-prefix\x7f"+values.tobytes()+b"Synthetic-tail")
    return bridge, values


def test_create_edit_preserves_source_and_changes_geometry(model_data, tmp_path):
    b = Bridge(tmp_path); b.create(model_data, "a")
    original = (tmp_path/"a/model.json").read_bytes()
    patch = {"operations": [{"collection":"nodes", "id":"tip", "operation":"upsert", "item":{"id":"tip","xyz_m":[4,0,0]}}]}
    b.edit("a/model.json", patch, "b")
    assert (tmp_path/"a/model.json").read_bytes() == original
    assert b.model("b/model.json").nodes[1].xyz_m[0] == 4
    with pytest.raises(FileExistsError): b.create(model_data, "a")


def test_deterministic_source_ids(model_data):
    a, amap = generate(Model.model_validate(model_data))
    model_data["nodes"].reverse()
    b, bmap = generate(Model.model_validate(model_data))
    assert a == b and amap == bmap and amap["nodes"] == {"base":1,"tip":2}


def test_live_group_order_and_material_edit_instructions(model_data,tmp_path):
    case = copy.deepcopy(model_data["load_cases"][0]); case.update(id="Q",kind="live")
    model_data["load_cases"].append(case)
    b=Bridge(tmp_path); b.create(model_data,"model")
    material = copy.deepcopy(model_data["materials"][0]); material["E_MPa"]=200000
    b.edit("model/model.json",{"operations":[{"collection":"materials","id":"steel","operation":"upsert","item":material}]},"edited")
    b.prepare("edited/model.json","job")
    text=(tmp_path/"job/model.log").read_text()
    _,_,rows=geometry(text)
    assert [float(x) for x in rows["LIVELOAD"][0]] == [1,2,0,0,1]
    assert "AREAINFO" not in text
    assert load_json(tmp_path/"job/job.json")["material_gui_required"]["E_MPa"]==200000


def test_canonical_csv_import_script(model_data,tmp_path):
    import subprocess, sys
    definitions=copy.deepcopy(model_data); definitions.pop("nodes"); definitions.pop("members")
    (tmp_path/"definitions.json").write_text(json.dumps(definitions))
    (tmp_path/"nodes.csv").write_text("id,x_m,y_m,z_m\nbase,0,0,0\ntip,3,0,0\n")
    (tmp_path/"members.csv").write_text("id,node_i,node_j,section_id\nbeam-001,base,tip,CHS114x4\n")
    script=Path(__file__).resolve().parents[1]/"scripts/import_tables.py"
    result=subprocess.run([sys.executable,str(script),"--workspace",str(tmp_path),"--definitions","definitions.json","--nodes","nodes.csv","--members","members.csv","--out","created"],capture_output=True,text=True)
    assert result.returncode==0, result.stderr
    assert Bridge(tmp_path).model("created/model.json").nodes[1].xyz_m==(3,0,0)


def test_polar_and_axial_reflection(model_data):
    model_data["nodes"][1]["xyz_m"] = [3,0,2]
    load = model_data["load_cases"][0]["loads"][0]
    load["force_kN"] = [1,2,3]; load["moment_kNm"] = [4,5,6]
    txt, _ = generate(Model.model_validate(model_data))
    nodes, _, rows = geometry(txt.decode())
    assert nodes[2] == [3,0,-2]
    assert [float(x) for x in rows["DEADLOAD1"][0]] == [2,1,2,-3,-4,-5,6]


@pytest.mark.parametrize("mutation", [
    lambda d:d["nodes"].append(copy.deepcopy(d["nodes"][0])),
    lambda d:d["members"][0].update(node_j="unknown"),
    lambda d:d["nodes"][1].update(xyz_m=[0,0,0]),
    lambda d:d["nodes"][1].update(xyz_m=[float("nan"),0,0]),
    lambda d:d["sections"][0]["dimensions_mm"].update(t=60),
    lambda d:d["members"][0].update(alpha_deg=15),
    lambda d:d["supports"][0].update(fixed=[True,True,True,False,False,False]),
    lambda d:d.update(automatic_self_weight=True),
    lambda d:d.update(unrecognized="silently ignored?"),
    lambda d:d["load_cases"].append(copy.deepcopy(d["load_cases"][0])),
])
def test_reject_invalid_models(model_data, mutation):
    mutation(model_data)
    with pytest.raises(ValueError): Model.model_validate(model_data)


def test_rhs_requires_explicit_behavior_acceptance(model_data):
    model_data["sections"][0].update(shape="RHS", dimensions_mm={"b":250,"h":400,"tw":10,"tf":10})
    with pytest.raises(ValueError): Model.model_validate(model_data)
    model_data["accept_mst_rhs_open_plate_torsion"] = True
    log, _ = generate(Model.model_validate(model_data))
    assert "5 1 250 400 10 10" in log.decode()


def test_workspace_escape_and_duplicate_json(tmp_path):
    b = Bridge(tmp_path)
    with pytest.raises(ValueError): b.ws.path("../escape", exists=False)
    (tmp_path/"duplicate.json").write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError): load_json(tmp_path/"duplicate.json")


def test_compare_detects_material_load_and_geometry_drift(model_data, tmp_path):
    b, _ = fixture_result(model_data, tmp_path)
    assert compare(b,"job","native.log","ok")["status"] == "PASS"
    txt = (tmp_path/"native.log").read_text()
    txt = txt.replace("206000.00", "200000.00").replace("2 3.000000000000", "2 3.100000000000")
    (tmp_path/"changed.log").write_text(txt)
    answer = compare(b,"job","changed.log","bad")
    assert answer["status"] == "FAILED"
    assert "COORDINATE_ERROR_EXCEEDS_0.02_MM" in answer["problems"]
    assert any("MATERIAL_E" in p for p in answer["problems"])


def test_compare_rejects_accumulated_imported_load(model_data,tmp_path):
    b,_=fixture_result(model_data,tmp_path)
    text=(tmp_path/"native.log").read_text()
    text=text.replace("2 0.000000000000 0.000000000000 1.000000000000", "2 0.000000000000 0.000000000000 3.000000000000")
    (tmp_path/"accumulated.log").write_text(text)
    result=compare(b,"job","accumulated.log","rejected_load")
    assert result["status"]=="FAILED" and any("NODAL_LOAD_MISMATCH" in p for p in result["problems"])


def test_ingest_source_ids_and_recover(model_data, tmp_path):
    b, values = fixture_result(model_data, tmp_path)
    compare(b,"job","native.log","comparison")
    answer = ingest(b,"native.log","readback","native.grd","job/mapping.json",["RESDEADLOAD"],"job","comparison/comparison.json")
    assert answer["status"] == "PASS"
    import csv
    rows = list(csv.DictReader((tmp_path/"readback/RESDEADLOAD_displacements.csv").open(encoding="utf-8-sig")))
    assert rows[1]["source_node_id"] == "tip"
    assert float(rows[1]["source_uz_m"]) == -float(values[1,2])/1000
    assert float(rows[1]["source_ry_rad"]) == -float(values[1,4])
    recover(b,"job/model.json","readback/RESDEADLOAD_displacements.csv","beam",11)
    field = list(csv.DictReader((tmp_path/"beam/beam_fields.csv").open(encoding="utf-8-sig")))
    expected = -1000*1500**2*(3*3000-1500)/(6*206000*(math.pi*(114**4-106**4)/64))/1000
    assert abs(float(field[5]["uz_m"])-expected) < 1e-9
    model_data["nodes"][1]["xyz_m"][0] = 4
    b.create(model_data,"different_model")
    with pytest.raises(ValueError, match="different model"):
        recover(b,"different_model/model.json","readback/RESDEADLOAD_displacements.csv","wrong_model",11)


def test_recover_rejects_printed_and_unbound_data(model_data, tmp_path):
    b, _ = fixture_result(model_data, tmp_path)
    ingest(b,"native.log","rounded",None,"job/mapping.json",["RESDEADLOAD"])
    with pytest.raises(ValueError, match="high-precision"):
        recover(b,"job/model.json","rounded/RESDEADLOAD_displacements.csv","rounded_beams")
    ingest(b,"native.log","unbound","native.grd","job/mapping.json",["RESDEADLOAD"])
    with pytest.raises(ValueError, match="high-precision"):
        recover(b,"job/model.json","unbound/RESDEADLOAD_displacements.csv","unbound_beams")


def test_ingest_rejects_wrong_comparison_and_csv_mutation(model_data, tmp_path):
    b, _ = fixture_result(model_data,tmp_path)
    compare(b,"job","native.log","comparison")
    ingest(b,"native.log","bound","native.grd","job/mapping.json",["RESDEADLOAD"],"job","comparison/comparison.json")
    p = tmp_path/"bound/RESDEADLOAD_displacements.csv"
    p.write_bytes(p.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="hash differs"):
        recover(b,"job/model.json","bound/RESDEADLOAD_displacements.csv","tampered")
    check = load_json(tmp_path/"comparison/comparison.json"); check["native_log_sha256"]="wrong"
    (tmp_path/"badcomparison.json").write_text(json.dumps(check))
    with pytest.raises(ValueError,match="does not bind"):
        ingest(b,"native.log","bad","native.grd","job/mapping.json",["RESDEADLOAD"],"job","badcomparison.json")


def test_precision_rejects_ambiguous_native_arrays():
    c = Case("x","RESDEADLOAD",[1],["1.00","0.00","0.00","0.00","0.00","0.00"],1,2)
    a = np.array([1.001,0,0,0,0,0], dtype="<f4").tobytes()
    b = np.array([1.002,0,0,0,0,0], dtype="<f4").tobytes()
    report, values = match_case(a+b,c)
    assert values is None and report["reason"] == "MULTIPLE_DIFFERENT_MATCHING_ARRAYS_NO_GUESS"
    report, values = match_case(a+a,c)
    assert values is not None and report["reason"] == "MULTIPLE_BYTE_IDENTICAL_MATCHES"


def test_failed_ingest_receipt_does_not_claim_csv_export(model_data,tmp_path):
    b, values=fixture_result(model_data,tmp_path)
    p=tmp_path/"native.grd"
    altered=values.copy();altered[1,2]+=0.0001
    p.write_bytes(p.read_bytes()+altered.tobytes())
    result=ingest(b,"native.log","ambiguous","native.grd","job/mapping.json",["RESDEADLOAD"])
    assert result["status"]=="FAILED"
    receipt=load_json(tmp_path/"ambiguous/ingestion.json")
    assert receipt["node_result_export"] is False and receipt["outputs"]=={}
    assert not list((tmp_path/"ambiguous").glob("*.csv"))


def test_precision_rejects_allzero_and_wrong_signed_zero():
    c = Case("x","RESDEADLOAD",[1],["0.00"]*6,1,2)
    report, _ = match_case(bytes(100),c)
    assert report["reason"] == "UNINFORMATIVE_ALL_ROUNDED_ZERO_CASE"
    c.tokens[0] = "1.00"; c.tokens[1] = "-0.00"
    raw = np.array([1,0,0,0,0,0],dtype="<f4").tobytes()
    report, _ = match_case(raw,c)
    assert report["reason"] == "NO_FULL_ARRAY_MATCH"


def test_log_coverage_mismatch_fails():
    with pytest.raises(ValueError): parse_cases("/NODECORD2/\n1 0 0 0 1 1\n2 1 0 0 1 1\n##\n/RESDEADLOAD/\n//(U)/\n1 0 0 0 0 0 0\n##\n")


def test_rigid_body_rotation_recovers_zero_chord_bow():
    axis = np.array([1.,0,0]); ri = np.array([0.,.001,0]); ui = np.zeros(3); uj = np.cross(ri,axis)*3
    for xi in np.linspace(0,1,21):
        _, relative = hermite_field(xi,3,axis,ui,uj,ri,ri)
        assert np.linalg.norm(relative) < 1e-17

