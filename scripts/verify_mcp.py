"""Actual subprocess stdio initialize/list/call/error test using the official client."""
from __future__ import annotations
import argparse
import asyncio
import importlib.metadata
import json
import math
import struct
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify():
    package = Path(__file__).resolve().parents[1]
    model = json.loads((package/"examples/cantilever.json").read_text(encoding="utf-8"))
    calls = []
    with tempfile.TemporaryDirectory(prefix="mst-mcp-test-") as temporary:
        params = StdioServerParameters(command=sys.executable, args=["-m","mst_codex_bridge.server","--workspace",temporary])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                initialization = await client.initialize()
                tools = await client.list_tools()
                names = sorted(t.name for t in tools.tools)
                assert len(names) == 9, names
                async def invoke(name, args, *, expect_error=False):
                    response = await client.call_tool(name, args)
                    assert bool(response.isError) == expect_error, response
                    calls.append({"tool":name,"expected_error":expect_error,"error_observed":bool(response.isError)})
                    if response.structuredContent is not None:
                        return response.structuredContent
                    return json.loads(response.content[0].text) if not expect_error else None
                await invoke("mst_capabilities",{})
                await invoke("mst_create",{"model":model,"out":"created"})
                answer = await invoke("mst_inspect",{"path":"created/model.json"})
                assert answer["nodes"] == 2
                await invoke("mst_validate",{"model_path":"created/model.json"})
                await invoke("mst_prepare",{"model_path":"created/model.json","out":"job"})
                temp = Path(temporary)
                log = (temp/"job/model.log").read_text(encoding="ascii")
                inertia = math.pi*(114**4-106**4)/64
                six = [0.,0.,1000*3000**3/(3*206000*inertia),0.,-1000*3000**2/(2*206000*inertia),0.]
                payload = struct.pack("<12f", *([0.]*6+six))
                native = log.replace("/END/",f"/AREAINFO/\n1 1 0 1382.30 {inertia:.2f} {inertia:.2f} {2*inertia:.2f} 206000.00 79000.00 0.30 0.00007698\n##\n/RESDEADLOAD/\n//(U)/\n1 0.00 0.00 0.00 0.00 0.00 0.00\n2 "+" ".join(f"{v:.2f}" for v in six)+"\n##\n/END/")
                (temp/"native.log").write_text(native,encoding="ascii")
                (temp/"native.grd").write_bytes(b"Synthetic prefix"+payload+b"tail")
                result = await invoke("mst_compare",{"job":"job","log":"native.log","out":"comparison"})
                assert result["status"] == "PASS"
                result = await invoke("mst_ingest",{"log":"native.log","grd":"native.grd","mapping_path":"job/mapping.json","selected_cases":["RESDEADLOAD"],"out":"extracted","job":"job","comparison_path":"comparison/comparison.json"})
                assert result["status"] == "PASS"
                await invoke("mst_recover_beams",{"model_path":"job/model.json","displacement_csv":"extracted/RESDEADLOAD_displacements.csv","out":"beam_fields"})
                await invoke("mst_edit",{"model_path":"created/model.json","patch":{"operations":[{"collection":"nodes","id":"tip","operation":"upsert","item":{"id":"tip","xyz_m":[4,0,0]}}]},"out":"edited"})
                await invoke("mst_create",{"model":model,"out":"created"},expect_error=True)
                await invoke("mst_inspect",{"path":"../escape.json"},expect_error=True)
                bad = json.loads(json.dumps(model)); bad["members"][0]["alpha_deg"] = 15
                await invoke("mst_create",{"model":bad,"out":"invalid"},expect_error=True)
                assert not (Path(temporary)/"invalid").exists()
                return {"status":"PASS","transport":"stdio subprocess", "protocol":initialization.protocolVersion,
                        "mcp_sdk":importlib.metadata.version("mcp"),"tools":names,"calls":calls,
                        "scope":"actual SDK initialize/list_tools/call_tool with filesystem outputs and failure checks; no MST GUI",
                        "registered_in_current_codex_session":False}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--out",type=Path); args=parser.parse_args()
    result = asyncio.run(asyncio.wait_for(verify(),timeout=45))
    text = json.dumps(result,ensure_ascii=False,indent=2)+"\n"
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open("x",encoding="utf-8") as stream:stream.write(text)
    print(text)


if __name__ == "__main__": main()
