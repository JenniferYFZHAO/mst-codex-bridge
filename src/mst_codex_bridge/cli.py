from __future__ import annotations
import argparse
import json
import sys
from .files import load_json
from .service import Bridge, CAPABILITIES


def main():
    parser = argparse.ArgumentParser(description="Structural MST2008 file bridge; does not execute MST GUI")
    parser.add_argument("--workspace", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    for name in ("inspect", "validate"):
        p = sub.add_parser(name); p.add_argument("path")
    p = sub.add_parser("create"); p.add_argument("model_json"); p.add_argument("out")
    p = sub.add_parser("edit"); p.add_argument("model_json"); p.add_argument("patch_json"); p.add_argument("out")
    p = sub.add_parser("prepare"); p.add_argument("model_json"); p.add_argument("out")
    p = sub.add_parser("compare"); p.add_argument("job"); p.add_argument("log"); p.add_argument("out")
    p = sub.add_parser("ingest"); p.add_argument("log"); p.add_argument("out"); p.add_argument("--grd"); p.add_argument("--mapping"); p.add_argument("--case", action="append")
    p.add_argument("--job"); p.add_argument("--comparison")
    p = sub.add_parser("recover"); p.add_argument("model_json"); p.add_argument("displacement_csv"); p.add_argument("out"); p.add_argument("--stations", type=int, default=11)
    args = parser.parse_args()
    try:
        bridge = Bridge(args.workspace)
        if args.command == "capabilities":
            result = CAPABILITIES
        elif args.command in ("inspect", "validate"):
            result = getattr(bridge, args.command)(args.path)
        elif args.command == "create":
            result = bridge.create(load_json(bridge.ws.path(args.model_json)), args.out)
        elif args.command == "edit":
            result = bridge.edit(args.model_json, load_json(bridge.ws.path(args.patch_json)), args.out)
        elif args.command == "prepare":
            result = bridge.prepare(args.model_json, args.out)
        elif args.command == "compare":
            from .results import compare
            result = compare(bridge, args.job, args.log, args.out)
        elif args.command == "ingest":
            from .results import ingest
            result = ingest(bridge, args.log, args.out, args.grd, args.mapping, args.case, args.job, args.comparison)
        else:
            from .beam_fields import recover
            result = recover(bridge, args.model_json, args.displacement_csv, args.out, args.stations)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if result.get("status") == "FAILED" else 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
