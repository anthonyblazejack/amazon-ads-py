"""Build the operation catalog and the API coverage docs from the downloaded specs.

Reads ``specs/`` (see ``fetch_specs.py``) and writes:

* ``src/amazon_ads/_generated/catalog.json``: every operation in every spec, with the
  method, path, parameters and the vendor media types Amazon expects. The client's
  ``call()`` uses it to send any Ads API operation with the right headers.
* ``docs/reference/coverage.md``: the same catalog as a readable page.
* ``docs/guide/quirks.md``: the API behavior notes from ``amazon_ads/notes.py``.

``--docs-only`` rebuilds the two pages from the committed catalog without the specs,
which is what CI runs to check the docs match the code. Output is deterministic (sorted,
no timestamps), so a diff after regenerating means something is stale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs"
CATALOG = ROOT / "src" / "amazon_ads" / "_generated" / "catalog.json"
COVERAGE = ROOT / "docs" / "reference" / "coverage.md"
QUIRKS = ROOT / "docs" / "guide" / "quirks.md"

METHODS = ("get", "put", "post", "delete", "patch")
# Sent by the transport on every call, so they are not listed as operation parameters.
AUTH_HEADERS = {
    "authorization",
    "amazon-advertising-api-clientid",
    "amazon-advertising-api-scope",
}

# Operations the library wraps with typed, hand-written methods. Everything else is
# reachable through ProfileClient.call().
WRAPPED_PREFIXES = {
    "sponsored-products": (
        "/sp/campaigns",
        "/sp/adGroups",
        "/sp/keywords",
        "/sp/targets",
        "/sp/negativeKeywords",
        "/sp/negativeTargets",
        "/sp/productAds",
        "/sp/campaignNegativeKeywords",
        "/sp/campaignNegativeTargets",
        "/sp/targets/bid/recommendations",
    ),
    "reporting": ("/reporting/reports",),
    "history": ("/history",),
    "profiles": ("/v2/profiles",),
    "portfolios": ("/portfolios",),
}


def load(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix in {".yaml", ".yml"}:
        import yaml

        data: dict[str, Any] = yaml.safe_load(raw)
        return data
    loaded: dict[str, Any] = json.loads(raw, strict=False)
    return loaded


def deref(spec: dict[str, Any], node: Any) -> Any:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return node
        target: Any = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target.get(part) if isinstance(target, dict) else None
        node = target
        seen += 1
    return node


def first_line(text: Any, limit: int = 140) -> str:
    if not isinstance(text, str):
        return ""
    line = " ".join(text.strip().split("\n", 1)[0].split())
    line = line.replace(chr(0x2014), "-")  # the repository uses no em dashes
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def operations(slug: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for path, item in sorted((spec.get("paths") or {}).items()):
        item = deref(spec, item) or {}
        shared = [deref(spec, p) for p in item.get("parameters") or []]
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = shared + [deref(spec, p) for p in op.get("parameters") or []]
            path_params, query, headers = [], [], []
            for p in params:
                if not isinstance(p, dict) or "name" not in p:
                    continue
                where, name = p.get("in"), str(p["name"])
                entry = {"name": name, "required": bool(p.get("required"))}
                if where == "path":
                    path_params.append(name)
                elif where == "query":
                    query.append(entry)
                elif where == "header" and name.lower() not in AUTH_HEADERS:
                    headers.append(entry)

            body = deref(spec, op.get("requestBody")) or {}
            content_types = sorted((body.get("content") or {}).keys())
            accept: list[str] = []
            responses = sorted((op.get("responses") or {}).items(), key=lambda kv: str(kv[0]))
            for status, response in responses:
                if str(status).startswith("2"):
                    response = deref(spec, response) or {}
                    for media in response.get("content") or {}:
                        if media not in accept:
                            accept.append(media)
            content_type = _prefer_vendor(content_types)
            # Answer in the same version the request is sent in when the spec allows it,
            # otherwise the newest vendor type it documents.
            if content_type in accept:
                preferred_accept: str | None = content_type
            else:
                preferred_accept = _prefer_vendor(sorted(accept))

            op_id = op.get("operationId") or f"{method}_{path}".replace("/", "_")
            result.append(
                {
                    "api": slug,
                    "id": f"{slug}:{op_id}",
                    "operation_id": op_id,
                    "method": method.upper(),
                    "path": path,
                    "summary": first_line(op.get("summary") or op.get("description")),
                    "tags": op.get("tags") or [],
                    "deprecated": bool(op.get("deprecated")),
                    "path_params": path_params,
                    "query": query,
                    "headers": headers,
                    "content_type": content_type,
                    "accept": preferred_accept,
                    "content_types": content_types,
                    "accepts": sorted(accept),
                    "has_body": bool(content_types),
                }
            )
    return result


def _prefer_vendor(types: list[str]) -> str | None:
    vendor = [t for t in types if "vnd." in t]
    if vendor:
        return vendor[-1]  # highest version sorts last
    return types[0] if types else None


def is_wrapped(op: dict[str, Any]) -> bool:
    prefixes = WRAPPED_PREFIXES.get(op["api"], ())
    return any(op["path"] == p or op["path"].startswith(p + "/") for p in prefixes)


def main() -> int:
    if "--docs-only" in sys.argv:
        data = json.loads(CATALOG.read_text())
        write_coverage(data["apis"], data["operations"])
        write_quirks()
        print("Docs regenerated from the committed catalog")
        return 0
    manifest_path = SPECS / "manifest.json"
    if not manifest_path.exists():
        print("specs/manifest.json missing: run scripts/fetch_specs.py first", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    apis: dict[str, Any] = {}
    ops: list[dict[str, Any]] = []
    for entry in sorted(manifest, key=lambda e: e["slug"]):
        spec = load(SPECS / entry["file"])
        info = spec.get("info") or {}
        apis[entry["slug"]] = {
            "title": first_line(info.get("title"), 100),
            "version": str(info.get("version")),
            "source": entry["url"],
            "section": entry.get("section"),
        }
        ops.extend(operations(entry["slug"], spec))

    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(
        json.dumps({"apis": apis, "operations": ops}, indent=None, separators=(",", ":")) + "\n"
    )
    write_coverage(apis, ops)
    write_quirks()
    print(f"{len(apis)} APIs, {len(ops)} operations -> {CATALOG.relative_to(ROOT)}")
    return 0


def write_coverage(apis: dict[str, Any], ops: list[dict[str, Any]]) -> None:
    by_api: dict[str, list[dict[str, Any]]] = {}
    for op in ops:
        by_api.setdefault(op["api"], []).append(op)
    wrapped = sum(1 for op in ops if is_wrapped(op))
    lines = [
        "# API coverage",
        "",
        "<!-- Generated by scripts/generate_catalog.py. Do not edit by hand. -->",
        "",
        f"The client can send all **{len(ops)} operations** across **{len(apis)} APIs** that "
        "Amazon's Ads API documentation publishes, using",
        "[`ProfileClient.call()`](../guide/any-endpoint.md). "
        f"**{wrapped}** of them also have typed, hand-written methods (marked **typed**).",
        "",
        "Regenerate this page with `python scripts/fetch_specs.py && "
        "python scripts/generate_catalog.py`.",
        "",
        "| API | Operations | Typed | Spec |",
        "|---|---:|---:|---|",
    ]
    for slug in sorted(by_api):
        api = apis[slug]
        n_typed = sum(1 for op in by_api[slug] if is_wrapped(op))
        lines.append(
            f"| [{api['title']}](#{slug}) | {len(by_api[slug])} | {n_typed or ''} | "
            f"[{slug}]({api['source']}) |"
        )
    for slug in sorted(by_api):
        api = apis[slug]
        lines += [
            "",
            f"## {slug}",
            "",
            f"**{api['title']}**, version {api['version']}.",
            "",
            "| Operation id | Method | Path | Typed |",
            "|---|---|---|---|",
        ]
        for op in by_api[slug]:
            flag = "**typed**" if is_wrapped(op) else ""
            dep = " (deprecated)" if op["deprecated"] else ""
            lines.append(f"| `{op['id']}`{dep} | {op['method']} | `{op['path']}` | {flag} |")
    COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE.write_text("\n".join(lines) + "\n")


def write_quirks() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from amazon_ads.notes import API_NOTES

    header = (
        "<!-- Generated from src/amazon_ads/notes.py by scripts/generate_catalog.py. "
        "Edit that file, not this one. -->\n\n"
    )
    body = API_NOTES.replace(
        "# Amazon Ads API: behavior the specs do not spell out",
        "# API quirks\n\nBehavior of the live Amazon Ads API that its documentation and "
        "specs do not state, found by calling it. The library handles each of these; they "
        "are listed so you know what it is doing and why. The MCP server serves the same "
        "text to Claude as the `api_notes` tool.",
    )
    QUIRKS.parent.mkdir(parents=True, exist_ok=True)
    QUIRKS.write_text(header + body)


if __name__ == "__main__":
    raise SystemExit(main())
