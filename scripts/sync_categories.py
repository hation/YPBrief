"""Sync categories.yaml (canonical channel-category file) into sources.yaml.

categories.yaml is the single source of truth for YPBrief channel categories
and their channels. GitHub Actions Lite and the Web UI import both read
sources.yaml, so this script regenerates sources.yaml from categories.yaml so
you only ever maintain one file.

Usage:
    .venv/bin/python scripts/sync_categories.py
    .venv/bin/python scripts/sync_categories.py --output /tmp/sources.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ALLOWED_TYPES = {"channel", "playlist", "video"}
GROUP_FIELDS = (
    "group_name",
    "display_name",
    "description",
    "enabled",
    "digest_title",
    "digest_language",
    "run_time",
    "timezone",
    "max_videos_per_source",
)
SOURCE_FIELDS = ("type", "name", "display_name", "url", "enabled", "group")


def _validate(data: dict) -> None:
    categories = data.get("categories", [])
    if not isinstance(categories, list):
        raise ValueError("categories.yaml must contain a list under 'categories'")
    seen_names: set[str] = set()
    for cat in categories:
        if not isinstance(cat, dict):
            raise ValueError("Each category entry must be a mapping")
        name = str(cat.get("name") or "").strip()
        if not name:
            raise ValueError("Each category entry must include a non-empty 'name'")
        if name in seen_names:
            raise ValueError(f"Duplicate category name: {name}")
        seen_names.add(name)
        sources = cat.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError(f"Category '{name}' must have a list under 'sources'")
        for src in sources:
            if not isinstance(src, dict):
                raise ValueError(f"Each source in category '{name}' must be a mapping")
            if not (src.get("url") or src.get("id")):
                raise ValueError(f"Each source in category '{name}' must include url or id")
            stype = src.get("type")
            if stype is not None and stype not in ALLOWED_TYPES:
                raise ValueError(
                    f"Source '{src.get('name', src.get('url'))}' in category '{name}' "
                    f"has invalid type '{stype}' (allowed: {sorted(ALLOWED_TYPES)})"
                )


def _build_groups_and_sources(data: dict) -> tuple[list[dict], list[dict]]:
    groups: list[dict] = []
    sources: list[dict] = []
    for cat in data.get("categories", []):
        group = {
            "group_name": cat["name"],
            "display_name": cat.get("display_name"),
            "description": cat.get("description"),
            "enabled": bool(cat.get("enabled", True)),
            "digest_title": cat.get("digest_title"),
            "digest_language": cat.get("digest_language") or "zh",
            "run_time": cat.get("run_time") or "07:00",
            "timezone": cat.get("timezone") or "Asia/Shanghai",
            "max_videos_per_source": int(cat.get("max_videos_per_source") or 10),
        }
        # Keep only explicitly-set fields to match sources.example.yaml style.
        groups.append({k: v for k, v in group.items() if v is not None})

        for src in cat.get("sources", []):
            item = {
                "type": src.get("type", "channel"),
                "name": src.get("name"),
                "display_name": src.get("display_name"),
                "url": src.get("url"),
                "enabled": bool(src.get("enabled", True)),
                "group": cat["name"],
            }
            sources.append({k: v for k, v in item.items() if v is not None})
    return groups, sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate sources.yaml from categories.yaml"
    )
    parser.add_argument(
        "--categories",
        default="categories.yaml",
        help="Path to the canonical categories.yaml (default: categories.yaml)",
    )
    parser.add_argument(
        "--output",
        default="sources.yaml",
        help="Output sources.yaml path (default: sources.yaml)",
    )
    args = parser.parse_args(argv)

    categories_path = Path(args.categories)
    if not categories_path.exists():
        print(f"ERROR: {categories_path} not found", file=sys.stderr)
        return 1

    data = yaml.safe_load(categories_path.read_text(encoding="utf-8")) or {}
    try:
        _validate(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    groups, sources = _build_groups_and_sources(data)
    output: dict = {"groups": groups, "sources": sources}
    output_path = Path(args.output)
    output_path.write_text(
        yaml.safe_dump(output, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(
        f"synced {categories_path} -> {output_path}: "
        f"{len(groups)} group(s), {len(sources)} source(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
