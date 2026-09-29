"""Small local MCP-compatible JSON-RPC surface for the Sidecar UI and Harnesses."""
from __future__ import annotations

from typing import Any, Dict

MCP_PROTOCOL = "sidecar-mcp/1"


def tools_for(sidecar: Any) -> list[Dict[str, Any]]:
    tools = [
        {"name": "broadcast.publish", "description": "Publish a plaintext signed broadcast visible to all registered nodes.", "inputSchema": {"type": "object", "required": ["content"], "properties": {"channel": {"type": "string"}, "content": {}, "content_type": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}}},
        {"name": "broadcast.list", "description": "List public broadcasts visible through the Relay.", "inputSchema": {"type": "object", "properties": {"channel": {"type": "string"}, "limit": {"type": "integer"}}}},
        {"name": "trace.record", "description": "Record a local collaboration trace event.", "inputSchema": {"type": "object", "required": ["event_type", "payload"], "properties": {"event_type": {"type": "string"}, "payload": {}}}},
        {"name": "trace.list", "description": "List local trace events.", "inputSchema": {"type": "object", "properties": {"event_type": {"type": "string"}, "limit": {"type": "integer"}}}},
        {"name": "skill.install", "description": "Store a user-designed Skill manifest; execution is disabled unless an explicit runner policy enables it.", "inputSchema": {"type": "object", "required": ["skill_id", "name", "version"], "properties": {"skill_id": {"type": "string"}, "name": {"type": "string"}, "version": {"type": "string"}, "capabilities": {"type": "array"}, "instructions": {"type": "string"}, "execution": {"type": "string"}}}},
        {"name": "skill.list", "description": "List local Skill manifests.", "inputSchema": {"type": "object", "properties": {"enabled_only": {"type": "boolean"}}}},
        {"name": "project.create", "description": "Create or join a local project trust scope.", "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "project_id": {"type": "string"}, "owner_id": {"type": "string"}}}},
        {"name": "project.list", "description": "List local projects and temporary project trust members.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "project.trust", "description": "Temporarily trust a registered Harness for one active project.", "inputSchema": {"type": "object", "required": ["project_id", "agent_id"], "properties": {"project_id": {"type": "string"}, "agent_id": {"type": "string"}, "expected_signing_key": {"type": "string"}}}},
        {"name": "project.close", "description": "Close a project and revoke every project-scoped trust grant.", "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}}},
    ]
    for skill in sidecar.store.skills():
        tools.append({"name": f"skill.{skill['skill_id']}", "description": f"User-designed skill {skill['name']} ({skill['version']}); execution is policy-controlled.", "inputSchema": skill.get("input_schema", {"type": "object"})})
    return tools


def _result(value: Any) -> Dict[str, Any]:
    return {"content": [{"type": "json", "json": value}]}


def handle_rpc(sidecar: Any, request: Dict[str, Any]) -> Dict[str, Any]:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        value = {"protocolVersion": MCP_PROTOCOL, "serverInfo": {"name": "private-agent-sidecar", "version": "0.1"}, "capabilities": {"tools": {}}}
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "result": {}}
    elif method == "tools/list":
        value = {"tools": tools_for(sidecar)}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "broadcast.publish":
            value = sidecar.publish_broadcast(args.get("channel", "public"), args.get("content"), args.get("content_type", "markdown"), args.get("tags", []), int(args.get("ttl_seconds", 86400)))
            value = _result(value)
        elif name == "broadcast.list":
            value = _result(sidecar.broadcasts(int(args.get("limit", 100)), args.get("channel", "")))
        elif name == "trace.record":
            event_id = sidecar.store.add_trace(args["event_type"], sidecar.identity.agent_id, args.get("payload"))
            value = _result({"event_id": event_id})
        elif name == "trace.list":
            value = _result(sidecar.store.traces(int(args.get("limit", 100)), args.get("event_type")))
        elif name == "skill.install":
            sidecar.install_skill(args)
            value = _result({"skill_id": args["skill_id"], "execution": "disabled-by-default"})
        elif name == "skill.list":
            value = _result(sidecar.store.skills(bool(args.get("enabled_only", False))))
        elif name == "project.create":
            value = _result(sidecar.create_project(args["name"], args.get("project_id", ""), args.get("owner_id", "")))
        elif name == "project.list":
            value = _result(sidecar.store.projects())
        elif name == "project.trust":
            value = _result(sidecar.trust_for_project(args["project_id"], args["agent_id"], args.get("expected_signing_key")))
        elif name == "project.close":
            value = _result(sidecar.close_project(args["project_id"]))
        elif isinstance(name, str) and name.startswith("skill."):
            value = {"isError": True, "content": [{"type": "text", "text": "user-defined skill execution is disabled by default; provide an explicit trusted runner policy"}]}
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
    elif method == "ping":
        value = {}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": value}
