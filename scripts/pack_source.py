"""Create an MIT source archive using an explicit allowlist; never publishes."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TOP={".gitignore","pyproject.toml","README.md","LICENSE","LICENSE_STATUS.md","requirements-tested.txt","model.schema.json"}
DIRS={"src","scripts","tests","examples","skills","docs"}
VERIFICATION={"mcp_stdio_final.json","gui_3m_analytic.json","gui_4m_analytic.json","summary.json","README.md"}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out",type=Path,required=True);args=parser.parse_args()
    files=[]
    for p in ROOT.rglob("*"):
        if not p.is_file():continue
        rel=p.relative_to(ROOT)
        if "__pycache__" in rel.parts or any(part.endswith(".egg-info") for part in rel.parts) or p.suffix in {".pyc",".pyo"}:continue
        allowed=(len(rel.parts)==1 and p.name in TOP) or rel.parts[0] in DIRS or (rel.parts[0]=="verification" and p.name in VERIFICATION)
        if not allowed:continue
        if p.suffix.lower() in {".exe",".dll",".3dm",".pdf",".grd",".docx",".png"}:
            raise ValueError(f"Unpublishable artifact in source allowlist: {rel}")
        data=p.read_bytes()
        text=data.decode("utf-8-sig")
        # Private absolute paths should never enter the portable source bundle.
        if any(token in text for token in ["C:"+"\\Users\\", "D:"+"\\Softwares\\", "D:"+"\\1-"]):
            raise ValueError(f"Local absolute path found in {rel}")
        files.append((rel.as_posix(),data))
    files.sort()
    if not (ROOT/"LICENSE").read_text(encoding="utf-8").startswith("MIT License"):
        raise ValueError("Expected MIT license before source packaging")
    manifest={"license":"MIT","license_selected":True,"publication_status":"archive only; check GitHub for publication","commercial_binaries_included":False,
              "project_models_included":False,"files":[{"path":n,"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()} for n,b in files]}
    out=args.out.resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,"x",compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files:
            info=zipfile.ZipInfo("mst-codex-bridge/"+name,date_time=(2026,10,28,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
        archive.writestr("mst-codex-bridge/SOURCE_MANIFEST.json",json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"archive":out.name,"source_files":len(files),"bytes":out.stat().st_size,"sha256":hashlib.sha256(out.read_bytes()).hexdigest()},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
