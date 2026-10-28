"""Bounded, observed MST2008 LOG dialect. No native GRD writes."""
from __future__ import annotations
import re
from collections import defaultdict
import math

from .model import Model, canonical

HEADER = re.compile(r"^/([^/]+)/$")


def decode_log(data: bytes):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("LOG needs lossless UTF-8 or GB18030 decoding")


def blocks(text: str):
    result = []
    section = None
    for line in text.splitlines():
        line = line.strip()
        header = HEADER.fullmatch(line)
        if header:
            section = {"name": header.group(1).upper(), "rows": []}
            result.append(section)
        elif section is not None and line:
            section["rows"].append(line)
    return result


def rows_by_name(text: str):
    result = defaultdict(list)
    for b in blocks(text):
        result[b["name"]].extend(row.split() for row in b["rows"] if not row.startswith(("#", "/")))
    return dict(result)


def number(value):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("Nonfinite LOG numeric field")
    return x


def geometry(text: str):
    rows = rows_by_name(text)
    node_rows = rows.get("NODECORD2", rows.get("NODECORD", []))
    nodes, beams = {}, {}
    for row in node_rows:
        n = int(row[0])
        if n <= 0 or n in nodes or len(row) < 4:
            raise ValueError("Invalid or repeated LOG node")
        nodes[n] = [number(v) for v in row[1:4]]
    for row in rows.get("BEAM_ELEMS_7", []):
        n = int(row[0])
        if n <= 0 or n in beams or len(row) != 7:
            raise ValueError("Invalid or repeated LOG beam")
        if int(row[2]) not in nodes or int(row[3]) not in nodes:
            raise ValueError("LOG beam refers to unknown node")
        beams[n] = [int(v) for v in row[2:6]] + [number(row[6])]
    if not nodes:
        raise ValueError("No NODECORD/NODECORD2 geometry")
    return nodes, beams, rows


def section_args(section):
    d = section["dimensions_mm"]
    names = {"CHS": ["D", "t"], "RHS": ["b", "h", "tf", "tw"], "I": ["h", "b", "tw", "tf"]}[section["shape"]]
    values = [d[key] for key in names]
    return values + [0.0]*(10-len(values))


def generate(model: Model):
    data = canonical(model)
    node_map = {v["id"]: i+1 for i, v in enumerate(data["nodes"])}
    member_map = {v["id"]: i+1 for i, v in enumerate(data["members"])}
    counter, section_map = defaultdict(int), {}
    section_rows = []
    for section in data["sections"]:
        shape = {"CHS": 1, "RHS": 5, "I": 6}[section["shape"]]
        counter[shape] += 1
        section_map[section["id"]] = [shape, counter[shape]]
        section_rows.append(f"{shape} {counter[shape]} " + " ".join(f"{v:.12g}" for v in section_args(section)) + " 0 1")
    lines = ["/BEGIN_MST_SPECIAL_DATA/", "/NODECORD2/"]
    for node in data["nodes"]:
        x, y, z = node["xyz_m"]
        lines.append(f"{node_map[node['id']]} {x:.12f} {y:.12f} {-z:.12f} 1 1")
    lines += ["##", "/BEAM_ELEMS_7/"]
    for member in data["members"]:
        mid = member_map[member["id"]]
        shape, snum = section_map[member["section_id"]]
        lines.append(f"{mid} {mid} {node_map[member['node_i']]} {node_map[member['node_j']]} {shape} {snum} 0")
    lines += ["##", "/SECTION PROPERTIES/", *section_rows, "##", "/SUPPORT INFORMATION/"]
    lines += [f"{node_map[s['node_id']]} 1 1 1 1 1 1" for s in data["supports"]]
    lines += ["##"]
    case_map = {}
    live_group = 0
    for case in data["load_cases"]:
        moments = any(any(load["moment_kNm"]) for load in case["loads"])
        if case["kind"] == "dead":
            block = "DEADLOAD1" if moments else "DEADLOAD"
            prefix = ""
            case_map[case["id"]] = {"slot": "RESDEADLOAD", "group": None}
        else:
            live_group += 1
            block = "LIVELOAD"
            prefix = f"{live_group} "
            case_map[case["id"]] = {"slot": "RESLIVELOAD", "group": live_group}
        lines.append(f"/{block}/")
        for load in case["loads"]:
            fx, fy, fz = load["force_kN"]
            mx, my, mz = load["moment_kNm"]
            values = [fx, fy, -fz] + ([-mx, -my, mz] if moments else [])
            lines.append(prefix+str(node_map[load["node_id"]])+" "+" ".join(f"{v:.12f}" for v in values))
        lines.append("##")
    lines.append("/END/")
    mapping = {"schema_version": "mst-map/1", "nodes": node_map, "members": member_map,
               "sections": section_map, "load_cases": case_map,
               "polar_transform": [1, 1, -1], "axial_transform": [-1, -1, 1]}
    return ("\r\n".join(lines)+"\r\n").encode("ascii"), mapping


def summarize(text: str):
    nodes, members, rows = geometry(text)
    return {"nodes": len(nodes), "members": len(members), "sections": len(rows.get("SECTION PROPERTIES", [])),
            "supports": len(rows.get("SUPPORT INFORMATION", [])),
            "result_blocks": [b["name"] for b in blocks(text) if b["name"].startswith("RES")],
            "unknown_blocks": sorted(set(rows)-{"BEGIN_MST_SPECIAL_DATA", "NODECORD", "NODECORD2", "BEAM_ELEMS_7",
               "SECTION PROPERTIES", "SUPPORT INFORMATION", "AREAINFO", "DEADLOAD", "DEADLOAD1", "LIVELOAD", "END"})}
