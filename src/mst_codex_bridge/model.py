"""Unit-explicit canonical model; no silent defaults for engineering inputs."""
from __future__ import annotations

import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Vec3 = tuple[Finite, Finite, Finite]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Node(StrictModel):
    id: ID
    xyz_m: Vec3


class Material(StrictModel):
    id: ID
    name: str
    E_MPa: Positive
    G_MPa: Positive
    poisson: Annotated[float, Field(gt=-1, lt=0.5)]
    density_kg_m3: Positive
    mst_class: Literal[0] = 0
    provenance: str


class Section(StrictModel):
    id: ID
    material_id: ID
    shape: Literal["CHS", "RHS", "I"]
    dimensions_mm: dict[str, Positive]
    provenance: str

    @model_validator(mode="after")
    def dimensions(self):
        keys = {"CHS": {"D", "t"}, "RHS": {"b", "h", "tf", "tw"},
                "I": {"h", "b", "tw", "tf"}}[self.shape]
        d = self.dimensions_mm
        if set(d) != keys:
            raise ValueError(f"{self.shape} dimensions require exactly {sorted(keys)}")
        if self.shape == "CHS":
            if 2*d["t"] >= d["D"]:
                raise ValueError("CHS wall consumes its diameter")
        elif 2*d["tf"] >= d["h"] or (2*d["tw"] if self.shape == "RHS" else d["tw"]) >= d["b"]:
            raise ValueError("Section has nonpositive clear space")
        return self


class Member(StrictModel):
    id: ID
    node_i: ID
    node_j: ID
    section_id: ID
    formulation: Literal["rigid_EB_beam"] = "rigid_EB_beam"
    alpha_deg: Literal[0] = 0


class Support(StrictModel):
    id: ID
    node_id: ID
    # Only the fully fixed format has been tested for this adapter release.
    fixed: tuple[Literal[True], Literal[True], Literal[True], Literal[True], Literal[True], Literal[True]]
    provenance: str


class NodalLoad(StrictModel):
    node_id: ID
    force_kN: Vec3
    moment_kNm: Vec3


class LoadCase(StrictModel):
    id: ID
    kind: Literal["dead", "live"]
    loads: list[NodalLoad]
    provenance: str


class Model(StrictModel):
    schema_version: Literal["mst-bridge/1"]
    name: str
    coordinate_system: Literal["right_handed_Z_up"]
    units: Literal["geometry:m;sections:mm;force:kN;moment:kNm;stress:MPa;rotation:rad"]
    nodes: list[Node] = Field(min_length=2)
    members: list[Member] = Field(min_length=1)
    materials: list[Material] = Field(min_length=1, max_length=1)
    sections: list[Section] = Field(min_length=1)
    supports: list[Support] = Field(min_length=1)
    load_cases: list[LoadCase] = Field(min_length=1)
    assumptions: list[str]
    automatic_self_weight: Literal[False]
    accept_mst_rhs_open_plate_torsion: bool = False

    @model_validator(mode="after")
    def topology(self):
        collections = ["nodes", "members", "materials", "sections", "supports", "load_cases"]
        for key in collections:
            ids = [v.id for v in getattr(self, key)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate IDs in {key}")
        nodes = {v.id: v for v in self.nodes}
        sections = {v.id: v for v in self.sections}
        material_ids = {v.id for v in self.materials}
        edges, used = set(), set()
        for member in self.members:
            if member.node_i not in nodes or member.node_j not in nodes:
                raise ValueError(f"Unknown endpoint for {member.id}")
            if member.section_id not in sections:
                raise ValueError(f"Unknown section for {member.id}")
            edge = tuple(sorted([member.node_i, member.node_j]))
            if edge in edges:
                raise ValueError(f"Coincident endpoint pair {edge}; model these deliberately in a later adapter")
            if math.dist(nodes[member.node_i].xyz_m, nodes[member.node_j].xyz_m) <= 1e-9:
                raise ValueError(f"Zero-length member {member.id}")
            edges.add(edge)
            used.update(edge)
        if used != set(nodes):
            raise ValueError(f"Isolated nodes: {sorted(set(nodes)-used)}")
        for section in self.sections:
            if section.material_id not in material_ids:
                raise ValueError(f"Unknown material for {section.id}")
            if section.shape == "RHS" and not self.accept_mst_rhs_open_plate_torsion:
                raise ValueError("MST SH5 uses open-plate torsion. Explicit acceptance required for software-behavior studies.")
        support_nodes = [v.node_id for v in self.supports]
        if len(support_nodes) != len(set(support_nodes)) or any(n not in nodes for n in support_nodes):
            raise ValueError("Duplicate or unknown support nodes")
        if sum(c.kind == "dead" for c in self.load_cases) > 1:
            raise ValueError("At most one dead-load slot supported; combine deliberately before export")
        if sum(c.kind == "live" for c in self.load_cases) > 1:
            raise ValueError("This release supports one verified live-load group")
        for case in self.load_cases:
            ids = [l.node_id for l in case.loads]
            if len(ids) != len(set(ids)) or any(n not in nodes for n in ids):
                raise ValueError(f"Duplicate or unknown loaded nodes in {case.id}")
            if case.kind == "live" and any(any(l.moment_kNm) for l in case.loads):
                raise ValueError("Live-load nodal moment format is unverified")
        # Every component needs a tested fixed support. This is not a stiffness-rank test.
        unseen = set(nodes)
        adjacency = {n: set() for n in nodes}
        for a, b in edges:
            adjacency[a].add(b); adjacency[b].add(a)
        while unseen:
            todo = [next(iter(unseen))]; component = set()
            while todo:
                n = todo.pop()
                if n in component:
                    continue
                component.add(n); todo.extend(adjacency[n]-component)
            unseen -= component
            if not component.intersection(support_nodes):
                raise ValueError("A disconnected component has no fixed support")
        return self


def canonical(model: Model) -> dict:
    data = model.model_dump(mode="json")
    for key in ("nodes", "members", "materials", "sections", "supports", "load_cases"):
        data[key] = sorted(data[key], key=lambda row: row["id"])
    for case in data["load_cases"]:
        case["loads"].sort(key=lambda row: row["node_id"])
    return data
