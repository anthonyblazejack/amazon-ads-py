"""Download every OpenAPI spec Amazon's Ads API documentation publishes.

Amazon's docs site is driven by a table of contents at
https://d3a0d0y2hgofx6.cloudfront.net/en-us/toc2.json. Every API reference entry in it
carries an ``openapi`` URL, and two entries point at sub-indexes (the "Amazon Ads API v1"
specs and the betas) written as ``"$<url>$"``. This script walks all of them, resolves
relative URLs against the docs host, and saves each spec plus a manifest to ``specs/``.

The specs are not committed (they are Amazon's documents, about 11 MB). Only the
operation catalog generated from them is. Run::

    python scripts/fetch_specs.py            # writes specs/*.json|yaml and specs/manifest.json
    python scripts/generate_catalog.py       # rebuilds the catalog and the coverage docs
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "specs"
TOC_URL = "https://d3a0d0y2hgofx6.cloudfront.net/en-us/toc2.json"
DOCS_BASE = "https://d3a0d0y2hgofx6.cloudfront.net/en-us/"
# The CloudFront hosts answer bare scripts with a bot block, so identify as a browser.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) amazon-ads-py spec fetcher"

# Friendlier slugs for the APIs people look for by name.
SLUGS = {
    "SponsoredProducts_prod_3p.json": "sponsored-products",
    "sponsored-products/2-0/openapi.yaml": "sponsored-products-v2",
    "sponsored-brands/4-0/openapi.json": "sponsored-brands-v4",
    "sponsored-brands/3-0/openapi.yaml": "sponsored-brands-v3",
    "sponsored-display/3-0/openapi.yaml": "sponsored-display",
    "profiles/3-0/openapi.yaml": "profiles",
    "OfflineReport_prod_3p.json": "reporting",
    "Changehistory_prod_3p.json": "history",
    "dsp/3-1/openapi.yaml": "dsp-campaigns",
    "dsp/3-0/advertiser.yaml": "dsp-advertiser",
    "dsp/2-2/reports_previous.yaml": "dsp-reports-v2",
    "data-provider/openapi.yaml": "data-provider",
    "creative-asset-library/creative-asset-library-openapi.yaml": "creative-assets",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data: bytes = response.read()
        return data


def resolve(url: str) -> str:
    return url if url.startswith("http") else DOCS_BASE + url.lstrip("/")


def walk(node: Any, found: dict[str, str], section: str, indexes: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        name = node.get("name") if isinstance(node.get("name"), str) else section
        if isinstance(node.get("openapi"), str):
            found.setdefault(resolve(node["openapi"]), name)
        items = node.get("items")
        if isinstance(items, str) and items.startswith("$") and items.endswith("$"):
            indexes.append((items.strip("$"), name))
        for value in node.values():
            walk(value, found, name, indexes)
    elif isinstance(node, list):
        for value in node:
            walk(value, found, section, indexes)


def slug_for(url: str) -> str:
    relative = url.split("/en-us/", 1)[1]
    relative = re.sub(r"^(openapi/en-us/)?dest/", "", relative)
    base = url.rsplit("/", 1)[1]
    for key in (relative, base):
        if key in SLUGS:
            return SLUGS[key]
    if relative.startswith("mcp/"):
        base = "mcp-" + base
    stem = re.sub(r"_prod_3p(_BETA)?", "", base)
    stem = re.sub(r"\.(json|ya?ml)$", "", stem)
    stem = stem.replace("AmazonAdsAPI", "v1-").replace("Contract", "")
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", stem)
    stem = re.sub(r"[_\s]+", "-", stem)
    return re.sub(r"-+", "-", stem.lower()).strip("-")


def parse(raw: bytes, url: str) -> dict[str, Any]:
    if url.endswith((".yaml", ".yml")):
        import yaml

        data = yaml.safe_load(raw)
    else:
        # A few DSP specs contain raw tab characters inside strings.
        data = json.loads(raw, strict=False)
    if not isinstance(data, dict) or not ("openapi" in data or "swagger" in data):
        raise ValueError("not an OpenAPI document")
    return data


def main() -> int:
    OUT.mkdir(exist_ok=True)
    toc = json.loads(fetch(TOC_URL))
    found: dict[str, str] = {}
    indexes: list[tuple[str, str]] = []
    walk(toc, found, "", indexes)
    for index_url, section in indexes:
        walk(json.loads(fetch(index_url)), found, section, [])

    manifest: list[dict[str, Any]] = []
    used: set[str] = set()
    failures = 0
    for url, section in sorted(found.items()):
        slug = slug_for(url)
        while slug in used:
            slug += "-2"
        used.add(slug)
        suffix = ".yaml" if url.endswith((".yaml", ".yml")) else ".json"
        path = OUT / f"{slug}{suffix}"
        try:
            raw = fetch(url)
            spec = parse(raw, url)
        except Exception as exc:
            failures += 1
            print(f"FAIL {url}: {exc}", file=sys.stderr)
            continue
        path.write_bytes(raw)
        info = spec.get("info") or {}
        manifest.append(
            {
                "slug": slug,
                "file": path.name,
                "url": url,
                "section": section,
                "title": info.get("title"),
                "version": str(info.get("version")),
                "paths": len(spec.get("paths") or {}),
            }
        )
        print(f"ok   {slug:60} {len(spec.get('paths') or {}):4} paths")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"\n{len(manifest)} specs saved to {OUT}, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
