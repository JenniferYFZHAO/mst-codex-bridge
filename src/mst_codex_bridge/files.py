"""Workspace-bounded files and immutable output bundles."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def load_json(path: Path):
    def no_constant(value):
        raise ValueError(f"Invalid JSON constant {value}")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=no_constant, object_pairs_hook=no_duplicates)


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be an existing directory")

    def path(self, value: str, *, exists=True) -> Path:
        raw = Path(value)
        path = (raw if raw.is_absolute() else self.root/raw).resolve(strict=exists)
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path must remain beneath the configured workspace")
        return path

    def relative(self, path: Path):
        return path.relative_to(self.root).as_posix()

    def bundle(self, directory: str, files: dict[str, bytes | str]) -> dict:
        out = self.path(directory, exists=False)
        if any(Path(name).name != name for name in files):
            raise ValueError("Bundle filenames must be simple basenames")
        # Exclusive directory creation prevents overwrite of earlier work.
        out.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for name, value in files.items():
            payload = value.encode("utf-8") if isinstance(value, str) else value
            with (out/name).open("xb") as stream:
                stream.write(payload)
            hashes[name] = digest(payload)
        return {"directory": self.relative(out), "sha256": hashes}

