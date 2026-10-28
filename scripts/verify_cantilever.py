"""Check actual synthetic-cantilever native readback against closed-form EB response."""
import argparse
import csv
import json
import math
from pathlib import Path
from mst_codex_bridge.service import Bridge
from mst_codex_bridge.files import load_json, digest, dumps


def verify(bridge, job, ingested, fields):
    model=bridge.model(job+"/model.json")
    if len(model.nodes)!=2 or len(model.members)!=1 or model.sections[0].shape!="CHS":
        raise ValueError("Verifier only covers the supplied single CHS cantilever")
    n={v.id:v for v in model.nodes}; member=model.members[0]
    a,b=n[member.node_i].xyz_m,n[member.node_j].xyz_m
    if a!=(0,0,0) or b[1:]!=(0,0) or b[0]<=0 or model.supports[0].node_id!=member.node_i:
        raise ValueError("Verifier requires fixed origin and beam on positive source X")
    case=model.load_cases[0]; load=case.loads[0]
    if case.kind!="dead" or len(case.loads)!=1 or load.node_id!=member.node_j or load.force_kN[:2]!=(0,0) or any(load.moment_kNm):
        raise ValueError("Verifier requires one vertical tip force")
    length=b[0]; force=load.force_kN[2]*1000; E=model.materials[0].E_MPa*1e6
    d=model.sections[0].dimensions_mm
    I=math.pi*((d["D"]*.001)**4-((d["D"]-2*d["t"])*.001)**4)/64
    expected=force*length**3/(3*E*I); rotation=-force*length**2/(2*E*I)
    ip=bridge.ws.path(ingested+"/RESDEADLOAD_displacements.csv")
    with ip.open(encoding="utf-8-sig",newline="") as stream:
        rows={r["source_node_id"]:r for r in csv.DictReader(stream)}
    tip=rows[member.node_j]
    ue=abs(float(tip["source_uz_m"])-expected)
    re=abs(float(tip["source_ry_rad"])-rotation)
    fp=bridge.ws.path(fields+"/beam_fields.csv")
    field_errors=[]
    with fp.open(encoding="utf-8-sig",newline="") as stream:
        for row in csv.DictReader(stream):
            x=float(row["xi"])*length
            exact=force*x*x*(3*length-x)/(6*E*I)
            field_errors.append(abs(float(row["uz_m"])-exact))
    ingestion=load_json(bridge.ws.path(ingested+"/ingestion.json"))
    return {"status":"PASS" if ue<1e-7 and re<1e-8 and max(field_errors)<1e-7 else "FAILED",
        "length_m":length,"vertical_tip_force_kN":force/1000,"expected_tip_uz_mm":expected*1000,
        "native_tip_uz_mm":float(tip["source_uz_m"])*1000,"tip_displacement_error_mm":ue*1000,
        "expected_tip_ry_rad":rotation,"native_tip_ry_rad":float(tip["source_ry_rad"]),"rotation_error_rad":re,
        "max_sampled_curve_error_mm":max(field_errors)*1000,"field_stations":len(field_errors),
        "tolerance_displacement_mm":.0001,"tolerance_rotation_rad":1e-8,
        "native_hashes":ingestion["hashes"],"verified_model_binding":ingestion["verified_model_binding"],
        "displacement_csv_sha256":digest(ip.read_bytes()),"beam_fields_csv_sha256":digest(fp.read_bytes()),
        "scope":"synthetic linear CHS cantilever analytic validation, not validation of an engineering roof"}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--workspace",default=".")
    for name in ("job","ingested","fields","out"):parser.add_argument("--"+name,required=True)
    args=parser.parse_args(); bridge=Bridge(args.workspace)
    report=verify(bridge,args.job,args.ingested,args.fields)
    path=bridge.ws.path(args.out,exists=False); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as stream:stream.write(dumps(report))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"]=="PASS" else 2


if __name__=="__main__": raise SystemExit(main())
