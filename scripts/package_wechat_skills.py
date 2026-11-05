#!/usr/bin/env python3
"""Build a distributable Sidecar WeChat Skill Bundle.

The bundle is a portable collection of Sidecar Skill manifests, not a Codex
plugin and not an executor for a real WeChat account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from agent_sidecar.wechat_skills import (
    CATALOG_SCHEMA,
    CATALOG_VERSION,
    SOURCE,
    wechat_catalog_payload,
    wechat_skill_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_bundle(output_dir: Path) -> Path:
    skills = wechat_skill_catalog()
    bundle_name = f"wechat-skills-{CATALOG_VERSION}"
    staging = output_dir / bundle_name
    zip_path = output_dir / f"{bundle_name}.zip"

    if staging.exists():
        shutil.rmtree(staging)
    if zip_path.exists():
        zip_path.unlink()
    staging.mkdir(parents=True)

    catalog = wechat_catalog_payload()
    catalog["installed_count"] = 0
    catalog["bundle_name"] = bundle_name
    catalog["source"] = SOURCE
    write_json(staging / "catalog.json", catalog)

    bundle_manifest = {
        "schema": "sidecar-skill-bundle/1",
        "name": bundle_name,
        "catalog_schema": CATALOG_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "source": SOURCE,
        "skill_count": len(skills),
        "execution": "disabled-by-default",
        "format": "one JSON manifest per skill under skills/<category>/<skill_id>.json",
        "install": "POST /v1/skills/catalog/install with optional skill_ids, or import the JSON manifests through the Sidecar Skill API",
    }
    write_json(staging / "bundle.json", bundle_manifest)

    readme = f"""# WeChat Skill Bundle {CATALOG_VERSION}

This is a Private Agent Sidecar Skill Bundle containing {len(skills)} independent
WeChat capability manifests.

- Catalog schema: `{CATALOG_SCHEMA}`
- Source snapshot: `{SOURCE}`
- Execution policy: `disabled-by-default`
- This bundle is **not** a Codex plugin.
- It does not automate a real WeChat account and does not contain credentials.

## Layout

```text
bundle.json       Bundle metadata and import contract
catalog.json      Complete catalog with all manifests
skills/           One JSON manifest per feature, grouped by module
checksums.sha256  SHA-256 checksums for every JSON manifest and catalog file
```

## Sidecar installation

Install the entire catalog through the local Sidecar API:

```text
POST /v1/skills/catalog/install
{{}}
```

Install selected skills:

```json
{{"skill_ids":["wechat.group.create","wechat.pay.payment_code"]}}
```

The manifests describe UI entry points, actions, roles, prerequisites,
visibility, limitations, platform support, and verification status. An
explicit trusted runner is required before any real-world operation can be
executed.
"""
    (staging / "README.md").write_text(readme, encoding="utf-8")

    for skill in skills:
        category_dir = slug(skill["category"])
        write_json(staging / "skills" / category_dir / f"{skill['skill_id']}.json", skill)

    checksum_paths = sorted(path for path in staging.rglob("*.json") if path.is_file())
    checksum_lines = []
    for path in checksum_paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {path.relative_to(staging).as_posix()}")
    (staging / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, f"{bundle_name}/{path.relative_to(staging).as_posix()}")

    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the built-in WeChat Sidecar Skills as a distributable bundle")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    print(build_bundle(output_dir))


if __name__ == "__main__":
    main()
