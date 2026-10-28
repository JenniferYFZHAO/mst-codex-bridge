"""Official MCP Python SDK v1.x stdio adapter; stdout is protocol only."""
from __future__ import annotations
import argparse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .service import Bridge, CAPABILITIES
from .results import compare, ingest
from .beam_fields import recover


def make_server(workspace: str):
    bridge = Bridge(workspace)
    server = FastMCP("mst-codex-bridge", instructions="MST2008 file bridge. No direct GUI or solver API. All paths are confined to the configured workspace. All writes create new directories.")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    def mst_capabilities() -> dict:
        """Describe verified file operations and explicit native/GUI limitations."""
        return CAPABILITIES

    @server.tool(annotations=read)
    def mst_inspect(path: str) -> dict:
        """Inspect canonical JSON or MST LOG: counts, result blocks, encoding and hash."""
        return bridge.inspect(path)

    @server.tool(annotations=read)
    def mst_validate(model_path: str) -> dict:
        """Validate units, IDs, references, section dimensions and supported features."""
        return bridge.validate(model_path)

    @server.tool(annotations=write)
    def mst_create(model: dict, out: str) -> dict:
        """Create validated model.json in a new directory, preserving stable source IDs."""
        return bridge.create(model, out)

    @server.tool(annotations=write)
    def mst_edit(model_path: str, patch: dict, out: str) -> dict:
        """Apply ID-based upsert/remove edits, validate the whole model and save a new copy."""
        return bridge.edit(model_path, patch, out)

    @server.tool(annotations=write)
    def mst_prepare(model_path: str, out: str) -> dict:
        """Export LOG, stable ID maps and explicit GUI job instructions. Does not solve."""
        return bridge.prepare(model_path, out)

    @server.tool(annotations=write)
    def mst_compare(job: str, log: str, out: str) -> dict:
        """Compare native LOG to prepared model and record material/geometry/load discrepancies."""
        return compare(bridge, job, log, out)

    @server.tool(annotations=write)
    def mst_ingest(log: str, out: str, grd: str | None = None, mapping_path: str | None = None, selected_cases: list[str] | None = None, job: str | None = None, comparison_path: str | None = None) -> dict:
        """Export nodal 6DOFs. Optional GRD requires complete paired-LOG interval matches; ambiguity fails."""
        return ingest(bridge, log, out, grd, mapping_path, selected_cases, job, comparison_path)

    @server.tool(annotations=write)
    def mst_recover_beams(model_path: str, displacement_csv: str, out: str, stations: int = 11) -> dict:
        """Recover EB beam fields from source-ID nodal 6DOFs; marks results as interpolation, not direct MST station output."""
        return recover(bridge, model_path, displacement_csv, out, stations)

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, help="Existing directory bounding all reads and writes")
    args = parser.parse_args()
    make_server(args.workspace).run(transport="stdio")


if __name__ == "__main__":
    main()
