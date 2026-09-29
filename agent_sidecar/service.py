from __future__ import annotations

import json
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .client import RelayClient
from .broadcast import create_broadcast, verify_broadcast
from .bootstrap import BOOTSTRAP_MANIFEST, install_bootstrap
from .mcp import handle_rpc, tools_for
from .capabilities import collect_capabilities
from .crypto import Identity, PublicIdentity
from .http_utils import JsonHandler, enable_tls
from .protocol import decrypt_message, encrypt_message
from .store import LocalStore


TRUST_UI = """<!doctype html>
<html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Sidecar Trust Manager</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:960px;margin:32px auto;padding:0 16px;color:#172033;background:#f7f9fc}
.card{background:white;border:1px solid #dce3ee;border-radius:12px;padding:20px;box-shadow:0 4px 18px #16213d12}h1{margin-top:0}button{background:#1769aa;color:#fff;border:0;border-radius:7px;padding:10px 16px;font-size:15px;cursor:pointer}.muted{color:#64748b}.peer{display:flex;gap:12px;align-items:flex-start;border-top:1px solid #e6ebf2;padding:14px 0}.peer:first-child{border-top:0}.peer input{margin-top:4px}.id{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}.status{font-size:12px;padding:3px 7px;border-radius:10px;background:#eef2ff}.trusted{background:#dcfce7;color:#166534}.revoked{background:#fee2e2;color:#991b1b}#message{margin-top:14px;color:#166534}
</style>
<div class=\"card\"><h1>新节点信任管理</h1><p class=\"muted\">新节点默认不信任任何节点。勾选后，当前 Sidecar 才会保存该节点的公钥并允许加密通信。</p><div id=\"peers\">加载中...</div><button id=\"save\">保存信任选择</button><div id=\"message\"></div></div>
<script>
async function load(){const r=await fetch('/v1/peers/candidates');const d=await r.json();const box=document.querySelector('#peers');box.innerHTML='';for(const p of d.peers){const row=document.createElement('label');row.className='peer';row.innerHTML=`<input type="checkbox" data-id="${p.agent_id}" ${p.trust_state==='trusted'?'checked':''}><div><div><strong>${p.display_name||p.agent_id}</strong> <span class="status ${p.trust_state}">${p.trust_state}</span></div><div class="id">${p.agent_id}</div><div class="muted">signing fingerprint: ${p.key_fingerprint||p.signing_key.slice(0,16)}</div></div>`;box.appendChild(row)}}
document.querySelector('#save').onclick=async()=>{const ids=[...document.querySelectorAll('input[data-id]:checked')].map(x=>x.dataset.id);const r=await fetch('/v1/peers/trust',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({trusted_agent_ids:ids})});const d=await r.json();document.querySelector('#message').textContent=r.ok?'已保存，共信任 '+d.trusted_agent_ids.length+' 个节点':(d.error||'保存失败');await load()};load();
</script>"""


DASHBOARD_UI = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sidecar Broadcast Dashboard</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#172033;background:#f7f9fc}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.card{background:#fff;border:1px solid #dce3ee;border-radius:12px;padding:18px;box-shadow:0 4px 18px #16213d12}h1{margin:0 0 8px}h2{font-size:18px;margin:0 0 12px}.muted{color:#64748b}textarea,input,select{box-sizing:border-box;width:100%;border:1px solid #cbd5e1;border-radius:7px;padding:9px;margin:5px 0 10px;font:inherit}button{background:#1769aa;color:#fff;border:0;border-radius:7px;padding:9px 14px;cursor:pointer}.danger{background:#b91c1c;margin-top:8px}.item{border-top:1px solid #e6ebf2;padding:10px 0}.item:first-child{border-top:0}.tag{display:inline-block;background:#e0f2fe;color:#075985;border-radius:12px;padding:2px 7px;font-size:12px;margin:2px}.mono{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;border-radius:7px;padding:9px;font-size:12px}
</style>
<h1>Sidecar 协作控制台</h1><p class="muted">广播内容默认明文存储并对所有节点可见；私密消息仍使用原有端到端加密通道。</p>
<div class="grid"><section class="card"><h2>发布公共广播</h2><label>频道</label><input id="channel" value="public"><label>标签（逗号分隔）</label><input id="tags" placeholder="project, recruitment"><label>内容（Markdown）</label><textarea id="content" rows="7" placeholder="发布项目需求、节点招募或公共进展"></textarea><button id="publish">发布广播</button><p id="status" class="muted"></p></section>
<section class="card"><h2>自定义 Skill</h2><label>Skill ID</label><input id="skill_id" placeholder="my-skill"><label>名称</label><input id="skill_name" placeholder="My Skill"><label>版本</label><input id="skill_version" value="0.1.0"><label>能力（逗号分隔）</label><input id="skill_caps" placeholder="broadcast.read, trace.write"><button id="install">保存 Skill Manifest</button><p id="skill_status" class="muted"></p></section></div>
<section class="card" style="margin-top:16px"><h2>项目级临时信任</h2><p class="muted">项目中的信任只在项目处于 active 时有效；项目关闭后自动撤销，不会升级为永久全局信任。</p><div class="grid"><div><label>项目名称</label><input id="project_name" placeholder="研究项目"><label>项目 ID（留空自动生成）</label><input id="project_id" placeholder="project-demo"><label>Owner Agent ID（留空为当前节点）</label><input id="project_owner" placeholder="agent:..."><button id="create_project">创建/加入本地项目</button></div><div><label>活动项目</label><select id="project_select"></select><label>临时信任 Harness</label><select id="project_peer"></select><button id="project_trust">授予项目临时信任</button><p id="project_status" class="muted"></p></div></div><div id="projects"></div></section>
<section class="card" style="margin-top:16px"><h2>节点能力和网络状态</h2><p class="muted">节点登录时会自动广播硬件、服务和 Relay 网络探测结果；广播内容对所有节点可见。</p><button id="refresh_caps">重新检测并广播</button><div id="capabilities">加载中...</div><div id="nodes_caps"></div></section><section class="card" style="margin-top:16px"><h2>项目私密消息</h2><p class="muted">选择活动项目和 Harness，发送端到端加密的项目消息。项目结束后发送会被拒绝。</p><label>收件 Harness</label><select id="project_message_peer"></select><label>消息内容</label><textarea id="project_message" rows="4" placeholder="输入项目内消息"></textarea><button id="send_project_message">发送项目消息</button><p id="project_message_status" class="muted"></p></section></div>
<div class="grid" style="margin-top:16px"><section class="card"><h2>公共广播广场</h2><div id="broadcasts">加载中...</div></section><section class="card"><h2>Trace</h2><div id="traces">加载中...</div></section></div><section class="card" style="margin-top:16px"><h2>已安装 Skills</h2><div id="skills">加载中...</div></section>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function jsonFetch(url,options){const r=await fetch(url,options);if(!r.ok)throw new Error(`${url} ${r.status}: ${await r.text()}`);return await r.json()}
async function closeProject(projectId){if(!confirm(`结束项目 ${projectId}？项目级信任将立即撤销。`))return;try{await jsonFetch(`/v1/projects/${encodeURIComponent(projectId)}/close`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});document.querySelector('#project_status').textContent='项目已结束，项目临时信任已撤销';await refresh()}catch(e){document.querySelector('#project_status').textContent='结束项目失败: '+e.message}}
async function refresh(){try{
 const localCaps=await jsonFetch('/v1/capabilities');const h=localCaps.hardware||{};const n=localCaps.network||{};document.querySelector('#capabilities').innerHTML=`<div class="item"><strong>Hardware</strong><div class="muted">${esc(h.platform||'unknown')} / ${esc(h.architecture||'unknown')} / CPU ${esc(h.cpu_logical_cores||'?')} / GPU ${h.gpu&&h.gpu.present?'有':'无'} / Camera ${h.camera&&h.camera.present?'有':'无'}</div></div><div class="item"><strong>Network</strong><div class="muted">${esc(n.stability||'unknown')} | success ${esc(n.success_rate??'')} | p95 ${esc(n.p95_latency_ms??'')} ms</div></div>`;const nodesCaps=await jsonFetch('/v1/nodes/capabilities');document.querySelector('#nodes_caps').innerHTML=`<h3>已广播节点 (${(nodesCaps.nodes||[]).length})</h3>`+(nodesCaps.nodes||[]).map(x=>{const c=x.capabilities||{};const hh=c.hardware||{};const nn=c.network||{};return `<div class="item"><strong>${esc(x.sender_id)}</strong><div class="muted">${esc(hh.platform||'')} / GPU ${hh.gpu&&hh.gpu.present?'有':'无'} / Camera ${hh.camera&&hh.camera.present?'有':'无'} / network ${esc(nn.stability||'unknown')} / p95 ${esc(nn.p95_latency_ms??'')} ms</div></div>`}).join('');
 const bd=await jsonFetch('/v1/broadcasts');document.querySelector('#broadcasts').innerHTML=(bd.broadcasts||[]).map(x=>`<div class="item"><strong>${esc(x.channel)}</strong> <span class="muted">${esc(new Date(x.created_at*1000).toLocaleString())}</span><div>${(x.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div><pre>${esc(x.content)}</pre><div class="mono">${esc(x.sender_id)}</div></div>`).join('')||'<p class="muted">暂无广播</p>';
 const td=await jsonFetch('/v1/traces');document.querySelector('#traces').innerHTML=(td.traces||[]).map(x=>`<div class="item"><strong>${esc(x.event_type)}</strong><div class="muted">${esc(new Date(x.created_at*1000).toLocaleString())}</div><pre>${esc(JSON.stringify(x.payload,null,2))}</pre></div>`).join('')||'<p class="muted">暂无 trace</p>';
 const sd=await jsonFetch('/v1/skills');document.querySelector('#skills').innerHTML=(sd.skills||[]).map(x=>`<div class="item"><strong>${esc(x.name)}</strong> <span class="tag">${esc(x.version)}</span><div class="mono">${esc(x.skill_id)}</div><div class="muted">${esc((x.capabilities||[]).join(', ')||'no declared capabilities')} | execution: ${esc(x.execution||'disabled-by-default')}</div></div>`).join('')||'<p class="muted">暂无 Skill</p>';
 const pd=await jsonFetch('/v1/projects');const projects=pd.projects||[];const active=projects.filter(x=>x.status==='active');document.querySelector('#project_select').innerHTML=active.map(x=>`<option value="${esc(x.project_id)}">${esc(x.name)} (${esc(x.project_id)})</option>`).join('')||'<option value="">暂无活动项目</option>';document.querySelector('#projects').innerHTML=projects.map(x=>`<div class="item"><strong>${esc(x.name)}</strong> <span class="tag">${esc(x.status)}</span><div class="mono">${esc(x.project_id)}</div><div class="muted">owner: ${esc(x.owner_id)} | project peers: ${(x.members||[]).map(m=>`${esc(m.agent_id)}${m.revoked_at?'（已撤销）':''}`).join(', ')||'none'}</div>${x.status==='active'?`<button class="danger" onclick="closeProject('${esc(x.project_id)}')">结束项目并撤销临时信任</button>`:''}</div>`).join('')||'<p class="muted">暂无项目</p>';
 const peers=await jsonFetch('/v1/peers/candidates');const peerOptions=(peers.peers||[]).map(x=>`<option value="${esc(x.agent_id)}">${esc(x.display_name||x.agent_id)}</option>`).join('')||'<option value="">暂无其他节点</option>';document.querySelector('#project_peer').innerHTML=peerOptions;document.querySelector('#project_message_peer').innerHTML=peerOptions;
 document.querySelector('#status').textContent=''
}catch(e){document.querySelector('#status').textContent='控制台读取失败: '+e.message}}
document.querySelector('#publish').onclick=async()=>{try{await jsonFetch('/v1/broadcasts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:document.querySelector('#channel').value,content_type:'markdown',content:document.querySelector('#content').value,tags:document.querySelector('#tags').value.split(',').map(x=>x.trim()).filter(Boolean)})});document.querySelector('#status').textContent='已发布';await refresh()}catch(e){document.querySelector('#status').textContent='发布失败: '+e.message}};
document.querySelector('#install').onclick=async()=>{try{await jsonFetch('/v1/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_id:document.querySelector('#skill_id').value,name:document.querySelector('#skill_name').value,version:document.querySelector('#skill_version').value,capabilities:document.querySelector('#skill_caps').value.split(',').map(x=>x.trim()).filter(Boolean),execution:'disabled-by-default',source:'local-user'})});document.querySelector('#skill_status').textContent='Skill manifest 已保存';await refresh()}catch(e){document.querySelector('#skill_status').textContent='保存失败: '+e.message}};
document.querySelector('#create_project').onclick=async()=>{try{await jsonFetch('/v1/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.querySelector('#project_name').value,project_id:document.querySelector('#project_id').value,owner_id:document.querySelector('#project_owner').value})});document.querySelector('#project_status').textContent='项目已创建/加入';await refresh()}catch(e){document.querySelector('#project_status').textContent='项目创建失败: '+e.message}};
document.querySelector('#project_trust').onclick=async()=>{const projectId=document.querySelector('#project_select').value;const agentId=document.querySelector('#project_peer').value;if(!projectId||!agentId)return;try{await jsonFetch(`/v1/projects/${encodeURIComponent(projectId)}/trust`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:agentId})});document.querySelector('#project_status').textContent='已授予项目临时信任；项目结束后自动撤销';await refresh()}catch(e){document.querySelector('#project_status').textContent='项目临时信任失败: '+e.message}};
document.querySelector('#send_project_message').onclick=async()=>{const projectId=document.querySelector('#project_select').value;const recipientId=document.querySelector('#project_message_peer').value;const content=document.querySelector('#project_message').value;if(!projectId||!recipientId||!content){document.querySelector('#project_message_status').textContent='请先选择项目、收件 Harness 并输入内容';return}try{const r=await jsonFetch('/v1/messages/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipient_id:recipientId,project_id:projectId,type:'project.message',payload:{text:content}})});document.querySelector('#project_message_status').textContent='项目消息已发送: '+r.message_id;document.querySelector('#project_message').value='';await refresh()}catch(e){document.querySelector('#project_message_status').textContent='发送失败: '+e.message}};
document.querySelector('#refresh_caps').onclick=async()=>{try{await jsonFetch('/v1/capabilities/refresh',{method:'POST'});document.querySelector('#status').textContent='节点能力已重新检测并广播';await refresh()}catch(e){document.querySelector('#status').textContent='能力刷新失败: '+e.message}};
refresh();setInterval(refresh,5000);
</script>"""


class Sidecar:
    def __init__(self, data_dir: Path, relay_url: str, relay_token: str = ""):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.identity = Identity.load_or_create(data_dir / "identity.json")
        self.store = LocalStore(data_dir / "state.db")
        self.relay = RelayClient(relay_url, self.identity, relay_token)
        self.relay.register()
        self.bootstrap_runtime()
        self.capabilities = collect_capabilities(self.identity.agent_id, data_dir, relay_url, relay_token)
        (data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        self.announce_capabilities()

    def announce_capabilities(self) -> Dict[str, Any]:
        broadcast = create_broadcast(self.identity, "node.capabilities", self.capabilities, "json", ["node", "capabilities", "login"], 900)
        self.relay.publish_broadcast(broadcast)
        self.store.add_trace("node.capabilities.broadcast", self.identity.agent_id, {"broadcast_id": broadcast["broadcast_id"], "hardware": self.capabilities["hardware"], "network": self.capabilities["network"]})
        return broadcast

    def refresh_capabilities(self) -> Dict[str, Any]:
        self.capabilities = collect_capabilities(self.identity.agent_id, self.store.data_dir, self.relay.relay_url(), self.relay.access_token)
        (self.store.data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return self.announce_capabilities()

    def capability_nodes(self, limit: int = 100) -> list[Dict[str, Any]]:
        values = self.relay.broadcasts(limit, "node.capabilities")
        latest: Dict[str, Dict[str, Any]] = {}
        for broadcast in values:
            sender = broadcast.get("sender_id")
            if sender and sender not in latest:
                latest[sender] = {"broadcast_id": broadcast.get("broadcast_id"), "sender_id": sender, "created_at": broadcast.get("created_at"), "capabilities": broadcast.get("content", {})}
        return list(latest.values())

    def bootstrap_runtime(self) -> Dict[str, Any]:
        try:
            manifest = self.relay.bootstrap()
        except Exception:
            manifest = BOOTSTRAP_MANIFEST
        root = self.store.data_dir
        paths = install_bootstrap(root, manifest)
        self.store.install_bootstrap(manifest)
        self.store.add_trace("bootstrap.installed", self.identity.agent_id, {"manifest_protocol": manifest.get("protocol"), "paths": paths})
        return manifest

    def publish_broadcast(self, channel: str, content: Any, content_type: str = "markdown", tags: Optional[list[str]] = None, ttl_seconds: int = 86400) -> Dict[str, Any]:
        broadcast = create_broadcast(self.identity, channel, content, content_type, tags, ttl_seconds)
        self.relay.publish_broadcast(broadcast)
        self.store.add_trace("broadcast.published", self.identity.agent_id, {"broadcast_id": broadcast["broadcast_id"], "channel": channel, "content_type": content_type})
        return broadcast

    def broadcasts(self, limit: int = 100, channel: str = "") -> list[Dict[str, Any]]:
        values = self.relay.broadcasts(limit, channel)
        for value in values:
            broadcast_id = value.get("broadcast_id")
            if broadcast_id and self.store.record_broadcast_receipt(broadcast_id):
                self.store.add_trace("broadcast.received", value.get("sender_id", "unknown"), {"broadcast_id": broadcast_id, "channel": value.get("channel")})
        return values

    def install_skill(self, manifest: Dict[str, Any]) -> None:
        self.store.upsert_skill(manifest)
        self.store.add_trace("skill.installed", self.identity.agent_id, {"skill_id": manifest.get("skill_id"), "version": manifest.get("version")})

    def trust(self, agent_id: str, expected_signing_key: Optional[str] = None):
        peer = self.relay.identity_for(agent_id)
        if expected_signing_key and peer.signing_key != expected_signing_key:
            raise ValueError("peer signing key does not match the expected fingerprint material")
        self.store.trust(peer)
        return peer

    def revoke(self, agent_id: str) -> bool:
        return self.store.revoke(agent_id)

    def peer_candidates(self) -> list[Dict[str, Any]]:
        existing = {peer["agent_id"]: peer for peer in self.store.peers()}
        output = []
        for identity in self.relay.identities():
            if identity.agent_id == self.identity.agent_id:
                continue
            local = existing.get(identity.agent_id, {})
            output.append({
                **identity.as_dict(),
                "trust_state": local.get("trust_state", "pending"),
                "trusted_at": local.get("trusted_at"),
                "revoked_at": local.get("revoked_at"),
                "key_fingerprint": identity.signing_key[:16],
                "display_name": local.get("display_name", ""),
            })
        return output

    def set_trust(self, agent_ids: list[str], expected_signing_keys: Optional[Dict[str, str]] = None) -> list[str]:
        expected_signing_keys = expected_signing_keys or {}
        identities = []
        for agent_id in agent_ids:
            identity = self.relay.identity_for(agent_id)
            expected = expected_signing_keys.get(agent_id)
            if expected and expected != identity.signing_key:
                raise ValueError(f"signing key mismatch for {agent_id}")
            identities.append(identity)
        self.store.set_trusted(identities)
        return [identity.agent_id for identity in identities]

    def create_project(self, name: str, project_id: str = "", owner_id: str = "") -> Dict[str, Any]:
        project_id = project_id or "project-" + uuid.uuid4().hex[:12]
        owner_id = owner_id or self.identity.agent_id
        project = self.store.create_project(project_id, name, owner_id)
        self.store.add_trace("project.created", self.identity.agent_id, {"project_id": project_id, "name": name, "owner_id": owner_id})
        return project

    def trust_for_project(self, project_id: str, agent_id: str, expected_signing_key: Optional[str] = None) -> Dict[str, Any]:
        peer = self.relay.identity_for(agent_id)
        if expected_signing_key and peer.signing_key != expected_signing_key:
            raise ValueError("peer signing key does not match the expected fingerprint material")
        self.store.trust_for_project(project_id, peer)
        self.store.add_trace("project.peer_trusted", self.identity.agent_id, {"project_id": project_id, "agent_id": agent_id, "scope": "project"})
        return {"project_id": project_id, "agent_id": agent_id, "trust_scope": "project", "status": "trusted"}

    def trusted_peer(self, agent_id: str, project_id: str = "") -> Optional[PublicIdentity]:
        global_peer = self.store.peer(agent_id) if agent_id in self.store.trusted_ids() else None
        if project_id:
            if not self.store.project_active(project_id):
                return None
            return global_peer or self.store.project_peer(project_id, agent_id)
        return global_peer

    def close_project(self, project_id: str, notify_members: bool = True) -> Dict[str, Any]:
        before = self.store.project(project_id)
        if not before:
            raise ValueError("project not found")
        if before["status"] == "closed":
            return before
        notified = []
        if notify_members and before.get("owner_id") == self.identity.agent_id:
            for member in before.get("members", []):
                agent_id = member["agent_id"]
                if member.get("revoked_at") is None and agent_id != self.identity.agent_id:
                    try:
                        self.send(agent_id, "project.lifecycle.close", {"project_id": project_id}, project_id=project_id)
                        notified.append(agent_id)
                    except Exception:
                        pass
        project = self.store.close_project(project_id)
        revoked = [member["agent_id"] for member in before.get("members", []) if member.get("revoked_at") is None]
        self.store.add_trace("project.closed", self.identity.agent_id, {"project_id": project_id, "revoked_project_trust": revoked, "notified_members": notified})
        return project

    def send(self, recipient_id: str, message_type: str, payload, ttl_seconds: int = 86400, project_id: str = ""):
        peer = self.trusted_peer(recipient_id, project_id)
        if not peer:
            if project_id and not self.store.project_active(project_id):
                raise ValueError("project is closed or unknown")
            if project_id:
                raise ValueError("recipient is not trusted for this project")
            raise ValueError("recipient is not globally trusted; use project_id for temporary project trust")
        envelope = encrypt_message(self.identity, peer, self.store.next_sequence(recipient_id), message_type, payload, ttl_seconds, project_id)
        self.relay.send(envelope)
        self.store.add_trace("message.sent", self.identity.agent_id, {"message_id": envelope["message_id"], "recipient_id": recipient_id, "project_id": project_id or None})
        return envelope["message_id"]

    def receive(self, auto_ack: bool = True):
        output = []
        for envelope in self.relay.inbox():
            message_id = envelope["message_id"]
            if self.store.has_received(message_id):
                if auto_ack:
                    self.relay.acknowledge(message_id)
                continue
            project_id = envelope.get("project_id", "")
            if project_id and not self.store.project_active(project_id):
                output.append({"message_id": message_id, "project_id": project_id, "error": "project is closed or unknown"})
                if auto_ack:
                    self.relay.acknowledge(message_id)
                continue
            sender = self.trusted_peer(envelope["sender_id"], project_id)
            if not sender:
                output.append({"message_id": message_id, "project_id": project_id or None, "error": "sender is not trusted for this message scope"})
                continue
            try:
                body = decrypt_message(self.identity, envelope, sender)
                if self.store.save_received(envelope, body) and auto_ack:
                    self.relay.acknowledge(message_id)
                    self.store.mark_acknowledged(message_id)
                if body.get("type") == "project.lifecycle.close" and project_id:
                    project = self.store.project(project_id)
                    if project and project.get("owner_id") == envelope["sender_id"]:
                        self.store.close_project(project_id)
                        self.store.add_trace("project.closed_remote", envelope["sender_id"], {"project_id": project_id, "reason": "owner_closed"})
                output.append({"message_id": message_id, "sender_id": envelope["sender_id"], "sequence": envelope["sequence"], "project_id": project_id or None, **body})
                self.store.add_trace("message.received", envelope["sender_id"], {"message_id": message_id, "project_id": project_id or None})
            except Exception as exc:
                output.append({"message_id": message_id, "error": str(exc)})
        return output


def make_handler(sidecar: Sidecar):
    class SidecarHandler(JsonHandler):
        def send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/ui/trust":
                    self.send_html(TRUST_UI)
                    return
                if parsed.path in ("/", "/ui"):
                    self.send_html(DASHBOARD_UI)
                    return
                if parsed.path == "/v1/identity":
                    self.send_json(200, sidecar.identity.public().as_dict())
                    return
                if parsed.path in ("/v1/peers", "/v1/peers/trust"):
                    self.send_json(200, {"peers": sidecar.store.peers()})
                    return
                if parsed.path == "/v1/peers/candidates":
                    self.send_json(200, {"peers": sidecar.peer_candidates()})
                    return
                if parsed.path == "/v1/projects":
                    self.send_json(200, {"projects": sidecar.store.projects()})
                    return
                if parsed.path.startswith("/v1/projects/"):
                    project_id = parsed.path.removeprefix("/v1/projects/").split("/", 1)[0]
                    project = sidecar.store.project(project_id)
                    self.send_json(200, project) if project else self.send_json(404, {"error": "project not found"})
                    return
                if parsed.path == "/v1/messages/inbox":
                    self.send_json(200, {"messages": sidecar.receive(auto_ack=True)})
                    return
                if parsed.path == "/v1/broadcasts":
                    self.send_json(200, {"broadcasts": sidecar.broadcasts()})
                    return
                if parsed.path == "/v1/traces":
                    self.send_json(200, {"traces": sidecar.store.traces()})
                    return
                if parsed.path == "/v1/skills":
                    self.send_json(200, {"skills": sidecar.store.skills()})
                    return
                if parsed.path == "/v1/capabilities":
                    self.send_json(200, sidecar.capabilities)
                    return
                if parsed.path == "/v1/nodes/capabilities":
                    self.send_json(200, {"nodes": sidecar.capability_nodes()})
                    return
                if parsed.path == "/v1/bootstrap":
                    self.send_json(200, sidecar.store.bootstrap() or {})
                    return
                if parsed.path == "/v1/mcp/manifest":
                    self.send_json(200, {"protocol": "sidecar-mcp/1", "tools": tools_for(sidecar)})
                    return
                if parsed.path == "/v1/federated/rounds":
                    self.send_json(200, {"rounds": sidecar.store.fl_rounds()})
                    return
                if parsed.path == "/v1/federated/recoveries":
                    self.send_json(200, {"recoveries": sidecar.store.fl_recoveries()})
                    return
                if parsed.path == "/healthz":
                    self.send_json(200, {"status": "ok", "agent_id": sidecar.identity.agent_id})
                    return
                self.send_json(404, {"error": "not found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                body = self.read_json()
                if parsed.path in ("/mcp", "/v1/mcp"):
                    self.send_json(200, handle_rpc(sidecar, body))
                    return
                if parsed.path == "/v1/projects":
                    project = sidecar.create_project(body.get("name", ""), body.get("project_id", ""), body.get("owner_id", ""))
                    self.send_json(201, project)
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/trust"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/trust").rstrip("/")
                    result = sidecar.trust_for_project(project_id, body["agent_id"], body.get("expected_signing_key"))
                    self.send_json(200, result)
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/close"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/close").rstrip("/")
                    self.send_json(200, sidecar.close_project(project_id))
                    return
                if parsed.path == "/v1/capabilities/refresh":
                    self.send_json(201, sidecar.refresh_capabilities())
                    return
                if parsed.path == "/v1/broadcasts":
                    broadcast = sidecar.publish_broadcast(body.get("channel", "public"), body.get("content"), body.get("content_type", "markdown"), body.get("tags", []), int(body.get("ttl_seconds", 86400)))
                    self.send_json(201, broadcast)
                    return
                if parsed.path == "/v1/skills":
                    sidecar.install_skill(body)
                    self.send_json(201, {"skill_id": body["skill_id"], "enabled": True})
                    return
                if parsed.path == "/v1/peers/trust":
                    peer = sidecar.trust(body["agent_id"], body.get("expected_signing_key"))
                    self.send_json(200, peer.as_dict())
                    return
                if parsed.path == "/v1/messages/send":
                    message_id = sidecar.send(body["recipient_id"], body.get("type", "message"), body.get("payload"), int(body.get("ttl_seconds", 86400)), body.get("project_id", ""))
                    self.send_json(201, {"message_id": message_id})
                    return
                self.send_json(404, {"error": "not found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def do_PUT(self) -> None:
            if urlparse(self.path).path != "/v1/peers/trust":
                self.send_json(404, {"error": "not found"})
                return
            try:
                body = self.read_json()
                ids = sidecar.set_trust(body.get("trusted_agent_ids", []), body.get("expected_signing_keys"))
                self.send_json(200, {"trusted_agent_ids": ids})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def do_DELETE(self) -> None:
            prefix = "/v1/peers/trust/"
            if not self.path.startswith(prefix):
                self.send_json(404, {"error": "not found"})
                return
            try:
                changed = sidecar.revoke(self.path[len(prefix):])
                self.send_json(200 if changed else 404, {"revoked": changed})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

    return SidecarHandler


def serve_sidecar(host: str, port: int, data_dir: Path, relay_url: str, tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None, relay_token: str = "") -> None:
    sidecar = Sidecar(data_dir, relay_url, relay_token)
    server = ThreadingHTTPServer((host, port), make_handler(sidecar))
    enable_tls(server, tls_cert, tls_key)
    scheme = "https" if tls_cert else "http"
    print(f"Sidecar {sidecar.identity.agent_id} listening on {scheme}://{host}:{port}")
    print(f"Trust UI: {scheme}://{host}:{port}/ui/trust")
    server.serve_forever()
