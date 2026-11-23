"""Allowlisted native tools for node relationships and Harness communication."""
from copy import deepcopy

CATALOG_VERSION = "2026.11.06"
CATALOG_SCHEMA = "sidecar-communication-skill-catalog/1"
SOURCE = "sidecar-native-communication"


def text(description, required=True, **extra):
    return {"type": "string", "description": description, "required": required, **extra}


NODE = text("对方节点的 agent_id")
PROJECT = text("项目 ID")
ACTOR = text("本机已注册的智能体 actor_id")

# Dispatch is code-owned; installed manifests cannot substitute commands or URLs.
_SPECS = [
    ("node.identity", "节点身份", "节点与信任", "查看本节点的公开签名身份。", {}, False, lambda s, a: s.identity.public().as_dict()),
    ("node.capabilities", "硬件与 Harness 能力", "节点与信任", "读取本机能力与已注册智能体。", {}, False, lambda s, a: s.capabilities),
    ("node.discover", "发现节点", "节点与信任", "读取已验证的节点能力广播，不自动授予信任。", {}, False, lambda s, a: {"nodes": s.capability_nodes()}),
    ("node.peers", "节点关系", "节点与信任", "查看候选节点、双向信任和智能体目录。", {}, False, lambda s, a: {"peers": s.peer_candidates()}),
    ("node.notifications", "信任通知", "节点与信任", "读取并处理到达本节点的签名信任通知。", {}, False, lambda s, a: s.peer_trust_notifications()),
    ("node.trust", "发起节点信任", "节点与信任", "核对公钥后建立单向信任并通知对方。", {"agent_id": NODE, "expected_signing_key": text("独立核对的完整签名公钥")}, True, lambda s, a: s.request_peer_trust(**a)),
    ("node.respond", "回应信任请求", "节点与信任", "接受、拒绝或阻止节点的信任请求。", {"agent_id": NODE, "decision": text("处理决定", enum=["accept", "reject", "block"])}, True, lambda s, a: s.respond_peer_trust(**a)),
    ("node.revoke", "撤销节点信任", "节点与信任", "撤销长期信任，保留独立的项目权限规则。", {"agent_id": NODE}, True, lambda s, a: s.revoke_peer_trust(**a)),
    ("actor.register", "注册智能体", "智能体通信", "在本节点 Harness 下创建持久智能体 ID。", {"name": text("智能体名称"), "role": text("职责", False)}, True, lambda s, a: s.register_actor(**a)),
    ("actor.list", "本机智能体", "智能体通信", "列出本机 Harness 的智能体公开身份。", {}, False, lambda s, a: {"actors": s.store.actors()}),
    ("actor.send", "智能体定向通信", "智能体通信", "由本机智能体向双向信任节点的指定智能体发送加密直连消息。", {"actor_id": ACTOR, "recipient_node_id": NODE, "recipient_actor_id": text("接收智能体 ID"), "text": text("消息正文")}, True, lambda s, a: s.send_actor_message(**a)),
    ("actor.inbox", "智能体收件箱", "智能体通信", "只读取指定本机智能体收到的消息。", {"actor_id": ACTOR}, False, lambda s, a: s.actor_inbox(**a)),
    ("broadcast.list", "查看公开广播", "公开广播", "查看 Relay 公开消息，不视为信任授权。", {}, False, lambda s, a: {"broadcasts": s.broadcasts()}),
    ("broadcast.publish", "发送公开广播", "公开广播", "向公共频道发布签名消息；所有节点可见，不能发送秘密。", {"text": text("公开内容")}, True, lambda s, a: s.publish_broadcast("public", {"text": a["text"]}, "application/json")),
    ("project.list", "项目列表", "项目协作", "查看本机已加入的项目。", {}, False, lambda s, a: {"projects": s.store.projects()}),
    ("project.create", "创建项目", "项目协作", "创建本节点拥有的项目。", {"name": text("项目名称"), "project_id": text("可选稳定项目 ID", False)}, True, lambda s, a: s.create_project(**a)),
    ("project.invite", "邀请项目成员", "项目协作", "发送签名邀请，对方接受后获得项目级临时信任。", {"project_id": PROJECT, "agent_id": NODE}, True, lambda s, a: s.send_project_invitation(**a)),
    ("project.invitations", "项目邀请通知", "项目协作", "查看和同步项目邀请。", {}, False, lambda s, a: s.project_invitation_notifications()),
    ("project.respond", "回应项目邀请", "项目协作", "接受或拒绝项目邀请，不提升为全局信任。", {"invitation_id": text("邀请 ID"), "decision": text("处理决定", enum=["accept", "reject"])}, True, lambda s, a: s.respond_project_invitation(**a)),
    ("project.board_read", "读取项目留言", "项目协作", "读取本节点持有的成员专属项目留言板。", {"project_id": PROJECT}, False, lambda s, a: s.project_board(**a)),
    ("project.board_post", "发布项目留言", "项目协作", "向项目成员发布签名加密留言。", {"project_id": PROJECT, "text": text("留言正文")}, True, lambda s, a: s.post_project_board_event(a["project_id"], "post", {"text": a["text"]})),
    ("project.board_sync", "同步项目留言", "项目协作", "与项目成员直连同步留言，不回退 Relay。", {"project_id": PROJECT}, True, lambda s, a: s.sync_project_board(a["project_id"], force=True)),
    ("project.close", "关闭项目", "项目协作", "由项目发起者结束项目并撤销项目级临时信任。", {"project_id": PROJECT}, True, lambda s, a: s.close_project(**a)),
]


def communication_skill_catalog():
    result = []
    for slug, name, category, description, fields, mutates, _ in _SPECS:
        required = [key for key, value in fields.items() if value.get("required")]
        properties = {key: {k: v for k, v in value.items() if k != "required"} for key, value in fields.items()}
        result.append({
            "skill_id": "sidecar." + slug, "name": name, "version": CATALOG_VERSION,
            "description": description, "category": category, "module": category,
            "feature_name": name, "operation_entry": "MCP tools/call: skill.sidecar." + slug,
            "actions": [slug], "roles": ["节点操作者", "本机 Harness"],
            "prerequisites": ["已安装此 Skill", "本机 Sidecar 可用", "满足节点或项目权限"],
            "visibility_permissions": "沿用节点信任与项目成员权限；本机 API 仅供受信任 Harness 使用",
            "states_results": ["成功", "无权限", "目标不可达", "参数无效"],
            "limitations_exceptions": ["智能体 ID 由所属节点担保，不是独立密钥或本机进程隔离边界"],
            "platforms": ["macOS", "Windows", "Linux"], "verification_status": "原生 Sidecar 接口",
            "execution": "native-sidecar", "mutates": mutates, "enabled": True,
            "source": SOURCE, "catalog_schema": CATALOG_SCHEMA, "catalog_version": CATALOG_VERSION,
            "capabilities": ["sidecar.communication"],
            "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        })
    return deepcopy(result)


def communication_skill_by_id(skill_id):
    return next((s for s in communication_skill_catalog() if s["skill_id"] == skill_id), None)


def communication_catalog_payload(installed_ids=None):
    installed = set(installed_ids or [])
    skills = communication_skill_catalog()
    categories = {}
    for skill in skills:
        skill["installed"] = skill["skill_id"] in installed
        categories[skill["category"]] = categories.get(skill["category"], 0) + 1
    return {"schema": CATALOG_SCHEMA, "version": CATALOG_VERSION, "count": len(skills),
            "installed_count": sum(s["installed"] for s in skills), "skills": skills,
            "categories": [{"name": k, "count": v} for k, v in categories.items()]}


def execute_communication_skill(sidecar, skill_id, arguments):
    manifest = communication_skill_by_id(skill_id)
    if manifest is None:
        raise ValueError("unknown communication skill")
    if not any(s["skill_id"] == skill_id for s in sidecar.store.skills(enabled_only=True)):
        raise ValueError("communication skill is not installed or is disabled")
    schema = manifest["input_schema"]
    if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]):
        raise ValueError("unexpected skill arguments")
    for key in schema["required"]:
        if key not in arguments:
            raise ValueError("missing required argument: " + key)
    for key, value in arguments.items():
        field = schema["properties"][key]
        if not isinstance(value, str) or not value.strip() or len(value) > 20000:
            raise ValueError("invalid string argument: " + key)
        if "enum" in field and value not in field["enum"]:
            raise ValueError("invalid choice: " + key)
    handler = next(spec[-1] for spec in _SPECS if "sidecar." + spec[0] == skill_id)
    return handler(sidecar, arguments)
