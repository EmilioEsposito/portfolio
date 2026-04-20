#!/usr/bin/env python3
"""Render Logfire dashboard templates into final JSON.

Usage:
    python logfire/dashboards/render.py            # render all dashboards
    python logfire/dashboards/render.py llm-cost   # render one
    python logfire/dashboards/render.py --check    # exit 1 if any rendered.json is stale

Layout per dashboard (e.g. `llm-cost/`):
    template.json          — human-authored Perses dashboard with {"$file": "<path>"} refs
    queries/*.sql          — SQL extracted out so it's diffable/editable
    rendered.json          — generated output, committed to git, pushed to Logfire

A {"$file": "relative/path.sql"} object anywhere in the template is replaced
with the file's contents (as a string). Paths are resolved relative to the
template file. No other template syntax is supported — keep it boring.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DASHBOARDS_DIR = Path(__file__).parent


def _resolve(node, base_dir: Path):
    if isinstance(node, dict):
        if set(node.keys()) == {"$file"}:
            file_path = (base_dir / node["$file"]).resolve()
            if not file_path.is_relative_to(base_dir):
                raise ValueError(f"$file path escapes dashboard dir: {node['$file']}")
            return file_path.read_text(encoding="utf-8").rstrip("\n")
        return {k: _resolve(v, base_dir) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, base_dir) for v in node]
    return node


def render(dashboard_dir: Path) -> str:
    template_path = dashboard_dir / "template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    resolved = _resolve(template, dashboard_dir)
    return json.dumps(resolved, indent=2, ensure_ascii=False) + "\n"


def _dashboards(name: str | None) -> list[Path]:
    if name:
        d = DASHBOARDS_DIR / name
        if not (d / "template.json").exists():
            raise SystemExit(f"no template.json at {d}")
        return [d]
    return sorted(p.parent for p in DASHBOARDS_DIR.glob("*/template.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="dashboard folder name (default: all)")
    parser.add_argument("--check", action="store_true", help="fail if rendered.json is stale")
    args = parser.parse_args()

    stale: list[str] = []
    for d in _dashboards(args.name):
        rendered = render(d)
        out_path = d / "rendered.json"
        if args.check:
            current = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
            if current != rendered:
                stale.append(str(out_path.relative_to(DASHBOARDS_DIR.parent.parent)))
            continue
        out_path.write_text(rendered, encoding="utf-8")
        print(f"rendered {out_path.relative_to(DASHBOARDS_DIR.parent.parent)}")

    if stale:
        print("stale rendered.json (run render.py to fix):", file=sys.stderr)
        for s in stale:
            print(f"  {s}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
