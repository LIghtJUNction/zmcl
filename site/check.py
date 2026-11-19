#!/usr/bin/env python3
"""Check the static site, evidence schema, local links, and public source targets."""
from __future__ import annotations
import json
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

SITE = Path(__file__).resolve().parent
REPO = SITE.parent
errors: list[str] = []

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[tuple[str, str]] = []
        self.script_sources: list[str] = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            if values["id"] in self.ids:
                errors.append("Duplicate HTML id: " + values["id"])
            self.ids.add(values["id"])
        for key in ("href", "src"):
            if key in values:
                self.links.append((tag, values[key]))
        if tag == "script":
            self.script_sources.append(values.get("src", ""))

page = Page()
page.feed((SITE / "index.html").read_text())
for tag, raw in page.links:
    url = urlsplit(raw)
    if url.scheme and url.scheme != "https":
        errors.append("Unsupported link scheme: " + raw)
    if not url.scheme and not url.netloc:
        target = unquote(url.path)
        if target.startswith("/") or ".." in PurePosixPath(target).parts:
            errors.append("Nonportable local path: " + raw)
        elif target and not (SITE / target).is_file():
            errors.append("Missing local file: " + target)
        elif not target and url.fragment and url.fragment not in page.ids:
            errors.append("Missing section: " + url.fragment)
    prefix = "/LIghtJUNction/zmcl/blob/main/"
    if url.netloc == "github.com" and url.path.startswith(prefix):
        source = unquote(url.path[len(prefix):])
        if not (REPO / source).is_file():
            errors.append("Missing repository source target: " + source)
    if tag == "img" and url.scheme:
        errors.append("Site image is not self-contained: " + raw)
if page.script_sources != ["app.js"]:
    errors.append("Unexpected or inline script")
data = json.loads((SITE / "data.json").read_text())
if data.get("schema_version") != 1:
    errors.append("Unsupported data schema")
if data["measurement"]["phase"] not in {"initial", "final"}:
    errors.append("Measurement phase must be explicit")
if data["measurement"]["sample_count"] < 1:
    errors.append("Measured data requires a sample count")
for feature in data["features"]:
    if feature["state"] not in {"verified", "implemented", "planned"}:
        errors.append("Invalid feature state: " + feature["name"])
    source = PurePosixPath(feature["evidence"])
    if source.is_absolute() or ".." in source.parts or not (REPO / source).is_file():
        errors.append("Feature evidence file missing: " + feature["evidence"])
    if not feature["summary"].strip():
        errors.append("Feature needs its verification scope: " + feature["name"])
seen = set()
for metric in data["measurements"]:
    if metric["key"] in seen:
        errors.append("Duplicate metric key: " + metric["key"])
    seen.add(metric["key"])
    if metric["unit"] not in {"bytes", "kib", "ms", "ticks", "seconds", "percent"}:
        errors.append("Unknown measurement unit: " + metric["key"])
    for application in ("zmcl", "xmcl"):
        value = metric[application]["value"]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            errors.append("Invalid metric value: " + metric["key"])
        if value is None and not metric[application].get("label"):
            errors.append("Unmeasured data needs a label: " + metric["key"])
    if not metric["context"].strip():
        errors.append("Metric requires its measurement conditions: " + metric["key"])
if shutil.which("node"):
    check = subprocess.run(["node", "--check", str(SITE / "app.js")], capture_output=True, text=True)
    if check.returncode:
        errors.append(check.stderr.strip())
else:
    print("Node unavailable: JavaScript parser check skipped")
if errors:
    raise SystemExit("\n".join(errors))
print(json.dumps({
    "status": "passed",
    "feature_groups": len(data["features"]),
    "metrics": len(data["measurements"]),
    "html_links_checked": len(page.links),
    "site_bytes": sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file()),
    "remote_http_checks": "Not requested; source targets checked against the local repository",
}, indent=2))
