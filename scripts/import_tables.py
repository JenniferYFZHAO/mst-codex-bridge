"""Import canonical CSV tables into an immutable JSON model.

definitions.json contains all model fields except nodes and members.
nodes.csv columns: id,x_m,y_m,z_m
members.csv columns: id,node_i,node_j,section_id
No column guessing or orientation/release deletion is performed.
"""
import argparse
import csv
import json
from mst_codex_bridge.service import Bridge
from mst_codex_bridge.files import load_json


def read_exact(path, columns):
    with path.open(encoding="utf-8-sig",newline="") as stream:
        reader = csv.DictReader(stream)
        if len(reader.fieldnames or []) != len(columns) or set(reader.fieldnames or []) != set(columns):
            raise ValueError(f"Expected exact CSV columns: {columns}")
        return list(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=".")
    for name in ("definitions", "nodes", "members", "out"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    bridge = Bridge(args.workspace)
    model = load_json(bridge.ws.path(args.definitions))
    if "nodes" in model or "members" in model:
        raise ValueError("Definitions must omit nodes and members to avoid silent replacement")
    model["nodes"] = [{"id":r["id"],"xyz_m":[float(r[c]) for c in ["x_m","y_m","z_m"]]}
        for r in read_exact(bridge.ws.path(args.nodes),["id","x_m","y_m","z_m"])]
    model["members"] = read_exact(bridge.ws.path(args.members),["id","node_i","node_j","section_id"])
    print(json.dumps(bridge.create(model,args.out),ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
