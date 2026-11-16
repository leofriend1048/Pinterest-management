"""Index over Pinterest's published OpenAPI description.

The spec is what lets a handful of generic tools cover every endpoint: it is
searched to find an operation, rendered to show its parameters and body, and
consulted on each call to decide whether that call only reads data.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# POST operations that need only read scopes but still do something a person
# would not call "reading", so they stay behind the write tool.
NOT_READ_ONLY = {"notification/post"}

PLACEHOLDER = re.compile(r"\{[^/{}]+\}")

# What people say versus what Pinterest names its operations.
SYNONYMS = {
    "pause": ("update",),
    "resume": ("update",),
    "unpause": ("update",),
    "archive": ("update",),
    "edit": ("update",),
    "change": ("update",),
    "rename": ("update",),
    "budget": ("update", "campaigns", "ad_groups"),
    "launch": ("create",),
    "new": ("create",),
    "build": ("create",),
    "make": ("create",),
    "remove": ("delete",),
    "performance": ("analytics",),
    "metrics": ("analytics",),
    "stats": ("analytics",),
    "insights": ("analytics",),
    "spend": ("analytics",),
    "roas": ("analytics",),
    "report": ("reports", "analytics"),
    "creative": ("ads", "pins"),
    "creatives": ("ads", "pins"),
    "adset": ("ad_groups",),
    "adsets": ("ad_groups",),
    "ad": ("ads",),
    "campaign": ("campaigns",),
    "audience": ("audiences",),
    "keyword": ("keywords",),
    "pin": ("pins",),
    "board": ("boards",),
    "video": ("media",),
    "upload": ("media",),
}


@dataclass
class Operation:
    id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    scopes: list[str]
    read_only: bool
    raw: dict = field(repr=False)
    _regex: re.Pattern = field(repr=False, default=None)

    def brief(self) -> dict:
        return {
            "operation_id": self.id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "access": "read" if self.read_only else "write",
        }


class Spec:
    def __init__(self, doc: dict):
        self.doc = doc
        self.version = doc.get("info", {}).get("version", "?")
        self.operations: list[Operation] = []
        for path, item in doc.get("paths", {}).items():
            for method in HTTP_METHODS:
                raw = item.get(method)
                if not raw:
                    continue
                scopes = sorted(
                    {
                        scope
                        for alternative in raw.get("security", [])
                        for scope_list in alternative.values()
                        for scope in scope_list
                    }
                )
                op_id = raw.get("operationId") or f"{method}:{path}"
                read_only = method == "get" or (
                    bool(scopes)
                    and all(s.split(":")[-1].startswith("read") for s in scopes)
                    and op_id not in NOT_READ_ONLY
                )
                pattern = "^" + "".join(
                    "[^/]+" if PLACEHOLDER.fullmatch(part) else re.escape(part)
                    for part in re.split(r"(\{[^/{}]+\})", path)
                ) + "/?$"
                self.operations.append(
                    Operation(
                        id=op_id,
                        method=method.upper(),
                        path=path,
                        summary=raw.get("summary", ""),
                        description=raw.get("description", ""),
                        tags=raw.get("tags", []),
                        scopes=scopes,
                        read_only=read_only,
                        raw=raw,
                        _regex=re.compile(pattern),
                    )
                )
        self._by_id = {op.id.lower(): op for op in self.operations}

    # ── lookup ────────────────────────────────────────────────────────────────

    def get(self, operation_id: str) -> Operation | None:
        return self._by_id.get(operation_id.strip().lower())

    def match(self, method: str, path: str) -> Operation | None:
        """Find the operation a concrete request path belongs to.

        Templates with more literal segments win, so `/ad_groups/audience_sizing`
        is not mistaken for `/ad_groups/{ad_group_id}`.
        """
        method = method.upper()
        path = "/" + path.split("?", 1)[0].strip("/")
        candidates = [
            op for op in self.operations if op.method == method and op._regex.match(path)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda op: len(PLACEHOLDER.sub("", op.path)))

    def search(
        self,
        query: str = "",
        tag: str | None = None,
        access: str = "any",
        limit: int = 25,
    ) -> list[Operation]:
        words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
        scored: list[tuple[int, Operation]] = []
        for op in self.operations:
            if tag and tag.lower() not in (t.lower() for t in op.tags):
                continue
            if access == "read" and not op.read_only:
                continue
            if access == "write" and op.read_only:
                continue
            name = op.id.lower()
            head = f"{op.path} {op.summary} {' '.join(op.tags)}".lower()
            body = op.description.lower()
            score = 0
            matched = 0
            for word in words:
                best = 0
                for term in (word, *SYNONYMS.get(word, ())):
                    # A synonym is a weaker signal than the word that was typed.
                    weight = 1.0 if term == word else 0.6
                    if term in name:
                        best = max(best, 6 * weight)
                    elif term in head:
                        best = max(best, 3 * weight)
                    elif term in body:
                        best = max(best, 1 * weight)
                if best:
                    matched += 1
                    score += best
            if matched or not words:
                scored.append((matched, score, op))
        # Operations matching more of the query's words come first.
        scored.sort(key=lambda row: (-row[0], -row[1], row[2].path, row[2].method))
        return [op for _, _, op in scored[:limit]]

    def tags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for op in self.operations:
            for tag in op.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items()))

    # ── rendering ─────────────────────────────────────────────────────────────

    def _ref(self, ref: str) -> dict:
        node = self.doc
        for part in ref.lstrip("#/").split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def resolve(self, node, depth: int = 6, _seen: tuple = ()):
        """Inline $refs and trim noise so a schema is readable in one go."""
        if isinstance(node, list):
            return [self.resolve(item, depth, _seen) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            name = ref.rsplit("/", 1)[-1]
            if ref in _seen:
                return {"$recursive": name}
            if depth <= 0:
                return {"$schema": name, "note": "not expanded; raise depth to see it"}
            return self.resolve(self._ref(ref), depth - 1, _seen + (ref,))
        out = {}
        for key, value in node.items():
            if key in ("example", "examples", "x-codeSamples", "title", "xml"):
                continue
            if key == "allOf" and isinstance(value, list) and len(value) == 1:
                merged = self.resolve(value[0], depth, _seen)
                if isinstance(merged, dict):
                    for mk, mv in merged.items():
                        out.setdefault(mk, mv)
                    continue
            if key == "description" and isinstance(value, str):
                out[key] = value if len(value) <= 600 else value[:600] + " …"
                continue
            out[key] = self.resolve(value, depth, _seen)
        return out

    def describe(self, op: Operation, depth: int = 6) -> dict:
        raw = op.raw
        params = []
        for param in raw.get("parameters", []):
            param = self.resolve(param, depth)
            entry = {
                "name": param.get("name"),
                "in": param.get("in"),
                "required": bool(param.get("required")),
            }
            if param.get("description"):
                entry["description"] = param["description"]
            if param.get("schema"):
                entry["schema"] = param["schema"]
            params.append(entry)

        body = None
        content = raw.get("requestBody", {}).get("content", {})
        for content_type, media in content.items():
            body = {
                "content_type": content_type,
                "required": bool(raw.get("requestBody", {}).get("required")),
                "schema": self.resolve(media.get("schema", {}), depth),
            }
            break

        response = None
        for status, resp in raw.get("responses", {}).items():
            if status.startswith("2"):
                for media in resp.get("content", {}).values():
                    response = self.resolve(media.get("schema", {}), min(depth, 3))
                    break
                break

        return {
            "operation_id": op.id,
            "method": op.method,
            "path": op.path,
            "access": "read" if op.read_only else "write",
            "use_tool": "pinterest_api_read" if op.read_only else "pinterest_api_write",
            "summary": op.summary,
            "description": op.description,
            "scopes": op.scopes,
            "rate_limit_category": raw.get("x-ratelimit-category"),
            "parameters": params,
            "request_body": body,
            "success_response": response,
        }

@lru_cache(maxsize=1)
def load() -> Spec:
    data = resources.files("pinterest_ads_mcp").joinpath("data/openapi.json.gz").read_bytes()
    return Spec(json.loads(gzip.decompress(data)))
