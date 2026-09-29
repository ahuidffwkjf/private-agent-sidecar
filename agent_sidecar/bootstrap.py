from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

BOOTSTRAP_MANIFEST: Dict[str, Any] = {
    "protocol": "agent-bootstrap/1",
    "mcp": {
        "name": "private-agent-sidecar",
        "version": "0.1",
        "tools": [
            "broadcast.publish",
            "broadcast.list",
            "trace.record",
            "trace.list",
            "skill.install",
            "skill.list",
            "mcp.initialize",
            "mcp.tools.list",
            "mcp.tools.call",
            "project.create",
            "project.list",
            "project.trust",
            "project.close",
        ],
    },
    "trace": {
        "schema": "sidecar-trace/1",
        "storage": "sqlite",
        "events": ["bootstrap.installed", "broadcast.published", "broadcast.received", "skill.installed", "skill.enabled"],
    },
    "skills": {
        "manifest_schema": "sidecar-skill/1",
        "execution": "disabled-by-default",
        "allow_user_defined": True,
    },
}


def install_bootstrap(data_dir: Path, manifest: Dict[str, Any]) -> Dict[str, str]:
    mcp_dir = data_dir / "mcp"
    trace_dir = data_dir / "trace"
    skills_dir = data_dir / "skills"
    for directory in (mcp_dir, trace_dir, skills_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "manifest.json").write_text(json.dumps(manifest.get("mcp", {}), ensure_ascii=False, indent=2), "utf-8")
    (trace_dir / "schema.json").write_text(json.dumps(manifest.get("trace", {}), ensure_ascii=False, indent=2), "utf-8")
    (skills_dir / "manifest.schema.json").write_text(json.dumps(manifest.get("skills", {}), ensure_ascii=False, indent=2), "utf-8")
    return {"mcp_dir": str(mcp_dir), "trace_dir": str(trace_dir), "skills_dir": str(skills_dir)}
