"""Shared CLI/MCP operations, all bounded by an explicit workspace."""
from __future__ import annotations
import copy
from pathlib import Path
from .files import Workspace, digest, dumps, load_json
from .model import Model, canonical
from .mst_format import decode_log, generate, summarize

CAPABILITIES = {
    "software": "Structural MST2008 / MstCAD.exe, observed local LOG dialect",
    "native_scripting_api": False, "gui_automation_in_server": False, "automatic_solver_run": False,
    "native_grd_writer": False, "model_format": "mst-bridge/1",
    "supported": ["rigid_EB_beam", "CHS", "RHS_with_explicit_torsion_acceptance", "I_alpha0",
                  "fully_fixed_six_dof_support", "one_material_class0", "nodal_dead_forces_and_moments", "nodal_live_forces"],
    "material_update": "GUI required, AREAINFO readback verifies actual values",
    "unsupported": ["partial_supports", "truss_releases", "nonzero_section_rotation", "custom_J_override", "live_nodal_moments"],
    "solver_settings_required": ["disable automatic selfweight", "verification without section resizing", "explicit load combination"],
    "validation_scope": "input consistency and observed file behavior; no design approval or calibrated truth",
    "training_label_release": False,
}


class Bridge:
    def __init__(self, workspace):
        self.ws = Workspace(workspace)

    def model(self, path):
        return Model.model_validate(load_json(self.ws.path(path)))

    def inspect(self, path: str):
        p = self.ws.path(path)
        raw = p.read_bytes()
        if p.suffix.lower() == ".json":
            model = self.model(path)
            info = {key: len(getattr(model, key)) for key in ("nodes", "members", "materials", "sections", "supports", "load_cases")}
            info.update(format="canonical_model", name=model.name)
        elif p.suffix.lower() == ".log":
            txt, encoding = decode_log(raw)
            info = summarize(txt) | {"format": "MST_LOG", "encoding": encoding}
        else:
            raise ValueError("inspect accepts canonical .json or MST .log; GRD needs paired LOG in ingest")
        return info | {"path": self.ws.relative(p), "sha256": digest(raw)}

    def validate(self, path: str):
        model = self.model(path)
        return {"status": "INPUT_VALID", "counts": self.inspect(path), "warnings": [
            "Stiffness rank, physical assumptions and code checks still require solver validation",
            "Material fields are GUI instructions until compared with native AREAINFO",
            *(["RHS torsion follows observed open-plate MST behavior"] if any(s.shape == "RHS" for s in model.sections) else [])]}

    def create(self, model: dict, out: str):
        validated = Model.model_validate(model)
        return self.ws.bundle(out, {"model.json": dumps(canonical(validated)), "receipt.json": dumps({"operation": "create", "status": "INPUT_VALID"})})

    def edit(self, model_path: str, patch: dict, out: str):
        source = self.ws.path(model_path)
        data = canonical(self.model(model_path))
        if set(patch) != {"operations"} or not isinstance(patch["operations"], list):
            raise ValueError("Patch must contain only operations list")
        allowed = {"nodes", "members", "materials", "sections", "supports", "load_cases"}
        for op in patch["operations"]:
            if set(op) - {"collection", "id", "operation", "item"}:
                raise ValueError("Unknown patch field")
            collection, oid, action = op["collection"], op["id"], op["operation"]
            if collection not in allowed or action not in ("upsert", "remove"):
                raise ValueError("Invalid patch collection or operation")
            matches = [i for i, item in enumerate(data[collection]) if item["id"] == oid]
            if action == "remove":
                if not matches or "item" in op:
                    raise ValueError("remove requires existing ID and no item")
                del data[collection][matches[0]]
            else:
                item = copy.deepcopy(op.get("item"))
                if not isinstance(item, dict) or item.get("id") != oid:
                    raise ValueError("upsert item ID must match patch ID")
                if matches:
                    data[collection][matches[0]] = item
                else:
                    data[collection].append(item)
        validated = Model.model_validate(data)
        receipt = {"operation": "edit", "source_model_sha256": digest(source.read_bytes()), "patch": patch,
                   "status": "INPUT_VALID", "source_modified": False}
        return self.ws.bundle(out, {"model.json": dumps(canonical(validated)), "receipt.json": dumps(receipt)})

    def prepare(self, model_path: str, out: str):
        model = self.model(model_path)
        log, mapping = generate(model)
        material = model.materials[0].model_dump()
        material["weight_density_N_mm3"] = material["density_kg_m3"]*9.80665/1e9
        receipt = {"operation": "prepare", "state": "AWAITING_GUI_IMPORT_AND_SOLVE",
            "model_sha256": digest(self.ws.path(model_path).read_bytes()), "input_log_sha256": digest(log),
            "native_api_used": False, "solver_executed": False, "material_gui_required": material,
            "capabilities": CAPABILITIES}
        instructions = f"""# 本作业的 MST 界面步骤

1. 在 MST 新建空白模型后，再导入同目录 model.log；核对节点 {len(model.nodes)}、杆件 {len(model.members)}、截面 {len(model.sections)}、支座 {len(model.supports)}。完整 LOG 导入已加载模型会累加荷载，不能用来覆盖旧模型；重复导入后应新建空白模型重做。
2. 材料 class0 设置 E={material['E_MPa']} MPa、G={material['G_MPa']} MPa、泊松比={material['poisson']}、重量密度={material['weight_density_N_mm3']:.12g} N/mm³。随后用 AREAINFO 回读确认。
3. 关闭软件自动自重；荷载均为显式节点力/力矩。源模型 Z 向上，LOG 已转换为 Z 向下。力矩遵循轴矢量反射。
4. 先另存为新的 GRD。选择验算不调整，明确本次组合系数，再求解。不要让软件自动优化截面。
5. 保存 GRD，导出同一已保存状态的 LOG。先 compare，再 ingest。来源不一致或结果不唯一时停止发布数据。

本包不包含 GUI 驱动；Codex 使用当前环境提供的 computer-use 技能操作可见窗口。此步骤可以由 Codex 执行，也可人工完成。
本作业生成时间不构成求解证据；receipt 的状态保持等待 GUI，直到产生单独的读回证据。
"""
        return self.ws.bundle(out, {"model.log": log, "model.json": dumps(canonical(model)), "mapping.json": dumps(mapping),
                                  "job.json": dumps(receipt), "GUI_STEPS.md": instructions})
