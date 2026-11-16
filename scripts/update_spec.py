"""Refresh the bundled Pinterest OpenAPI description.

    uv run python scripts/update_spec.py

Downloads Pinterest's published v5 spec and stores it as compressed JSON inside
the package. Run it when Pinterest ships new endpoints.
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

import yaml

SOURCE = "https://raw.githubusercontent.com/pinterest/api-description/main/v5/openapi.yaml"
TARGET = Path(__file__).resolve().parent.parent / "src/pinterest_ads_mcp/data/openapi.json.gz"


def strip(node):
    """Drop code samples and examples, which the server never shows."""
    if isinstance(node, dict):
        return {
            k: strip(v)
            for k, v in node.items()
            if k not in ("x-codeSamples", "example", "examples")
        }
    if isinstance(node, list):
        return [strip(v) for v in node]
    return node


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=60) as resp:
        doc = yaml.safe_load(resp.read())
    doc = strip(doc)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, separators=(",", ":"), default=str).encode()
    with gzip.GzipFile(TARGET, "wb", mtime=0) as fh:
        fh.write(payload)
    operations = sum(
        1
        for item in doc["paths"].values()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    )
    print(
        f"Pinterest API {doc['info']['version']}: {operations} operations, "
        f"{TARGET.stat().st_size // 1024} KB -> {TARGET}"
    )


if __name__ == "__main__":
    main()
