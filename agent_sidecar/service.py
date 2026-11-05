from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .client import RelayClient
from .broadcast import create_broadcast, verify_broadcast
from .bootstrap import BOOTSTRAP_MANIFEST, install_bootstrap
from .mcp import handle_rpc, tools_for
from .capabilities import collect_capabilities
from .crypto import Identity, PublicIdentity, b64d, b64e, canonical, verify_signature
from .http_utils import JsonHandler, enable_tls
from .protocol import decrypt_message, encrypt_message
from .project_board import create_board_event, decrypt_board_event_content, event_bucket, event_buckets, event_set_root, participant_set_hash, verify_board_event
from .project_invitation import create_invitation_response, create_project_invitation, verify_invitation_response, verify_project_invitation
from .project_keys import generate_project_key, key_fingerprint
from .key_store import create_project_key_backend
from .store import LocalStore
from .trust import create_trust_statement, verify_trust_statement
from .wechat_skills import CATALOG_VERSION as WECHAT_CATALOG_VERSION, wechat_catalog_payload, wechat_skill_by_id, wechat_skill_catalog


TRUST_UI = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sidecar Trust Manager</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:960px;margin:32px auto;padding:0 16px;color:#172033;background:#f7f9fc}.card{background:white;border:1px solid #dce3ee;border-radius:12px;padding:20px;box-shadow:0 4px 18px #16213d12;margin-bottom:16px}h1{margin-top:0}button{background:#1769aa;color:#fff;border:0;border-radius:7px;padding:8px 12px;font-size:14px;cursor:pointer;margin:4px}.danger{background:#b91c1c}.muted{color:#64748b}.peer{border-top:1px solid #e6ebf2;padding:14px 0}.peer:first-child{border-top:0}.id{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}.status{font-size:12px;padding:3px 7px;border-radius:10px;background:#eef2ff}
</style>
<div class="card"><h1>节点级长期信任管理</h1><p class="muted">发起信任后，对方收到签名加密通知。双方分别确认后才形成双向长期信任；Relay 信任不会传递。</p><div id="notifications"></div><div id="peers">加载中...</div><button id="refresh">刷新</button><div id="message"></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const labels={pending:'待审核',trusting:'我已信任，等待对方',trusted_by_peer:'对方已信任我',mutual:'双向信任',rejected_by_peer:'对方拒绝',revoked_by_peer:'对方已撤销信任',revoked:'已撤销',blocked:'已阻止',blocked_by_peer:'被对方阻止'};
async function call(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d}
async function requestTrust(id,key){await call('/v1/peers/trust-requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:id,expected_signing_key:key})});message.textContent='已发出信任通知';await load()}
async function respond(id,decision){await call('/v1/peers/trust-response',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:id,decision})});message.textContent='信任通知已处理';await load()}
async function revokeTrust(id){await call('/v1/peers/trust/'+encodeURIComponent(id),{method:'DELETE'});message.textContent='已撤销并通知对方';await load()}
async function load(){try{const feed=await call('/v1/peers/trust-notifications');notifications.innerHTML='<h2>收到的通知</h2>'+((feed.notifications||[]).map(n=>`<div class="peer"><strong>${esc(n.issuer_agent_id)}</strong> <span class="status">${esc(n.decision)}</span><div class="muted">${esc(labels[n.relationship_state]||n.relationship_state)}</div>${n.decision==='trust'&&!n.outgoing_trust?`<button onclick='respond(${JSON.stringify(n.issuer_agent_id)},"accept")'>接受并反向信任</button><button onclick='respond(${JSON.stringify(n.issuer_agent_id)},"reject")'>拒绝</button><button class="danger" onclick='respond(${JSON.stringify(n.issuer_agent_id)},"block")'>阻止</button>`:''}</div>`).join('')||'<p class="muted">暂无通知</p>');const d=await call('/v1/peers/candidates');peers.innerHTML='<h2>发现的节点</h2>'+d.peers.map(p=>`<div class="peer"><strong>${esc(p.display_name||p.agent_id)}</strong> <span class="status">${esc(labels[p.relationship_state]||p.relationship_state)}</span><div class="id">${esc(p.agent_id)}</div><div class="muted">signing fingerprint: ${esc(p.key_fingerprint||p.signing_key.slice(0,16))}</div>${p.outgoing_trust?`<button class="danger" onclick='revokeTrust(${JSON.stringify(p.agent_id)})'>撤销我的信任</button>`:`<button onclick='requestTrust(${JSON.stringify(p.agent_id)},${JSON.stringify(p.signing_key)})'>信任此节点</button>`}</div>`).join('')}catch(e){message.textContent='加载失败: '+e.message}}
refresh.onclick=load;load();
</script>"""


DASHBOARD_UI = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sidecar Collaboration Dashboard</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1160px;margin:24px auto;padding:0 16px;color:#172033;background:#f7f9fc}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.card{background:#fff;border:1px solid #dce3ee;border-radius:12px;padding:18px;box-shadow:0 4px 18px #16213d12}h1{margin:0 0 8px}h2{font-size:18px;margin:0 0 12px}.muted{color:#64748b}textarea,input,select{box-sizing:border-box;width:100%;border:1px solid #cbd5e1;border-radius:7px;padding:9px;margin:5px 0 10px;font:inherit}button{background:#1769aa;color:#fff;border:0;border-radius:7px;padding:9px 14px;cursor:pointer}.danger{background:#b91c1c;margin-top:8px}.item{border-top:1px solid #e6ebf2;padding:10px 0}.item:first-child{border-top:0}.tag{display:inline-block;background:#e0f2fe;color:#075985;border-radius:12px;padding:2px 7px;font-size:12px;margin:2px}.mono{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;border-radius:7px;padding:9px;font-size:12px}.notice{background:#eff6ff;border:1px solid #bfdbfe;padding:10px;border-radius:7px;margin:8px 0}.success{color:#166534}.warning{color:#92400e}.modal{position:fixed;inset:0;background:#0f172a66;display:flex;align-items:center;justify-content:center;padding:16px;z-index:10}.modalbox{background:#fff;max-width:720px;width:100%;max-height:85vh;overflow:auto;border-radius:12px;padding:20px}.skill-toolbar{display:grid;grid-template-columns:2fr 1fr 1fr;gap:10px;align-items:end}.skill-toolbar input,.skill-toolbar select{margin-bottom:0}.skill-summary{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.skill-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;max-height:760px;overflow:auto;padding:2px}.skill-card{border:1px solid #dce3ee;border-radius:10px;padding:14px;background:#fbfdff}.skill-card h3{font-size:16px;margin:0 0 6px}.skill-card p{margin:6px 0}.skill-card details{margin-top:8px}.skill-card summary{cursor:pointer;color:#1769aa}.skill-card dl{display:grid;grid-template-columns:76px 1fr;gap:4px 8px;font-size:13px}.skill-card dt{font-weight:600}.skill-card dd{margin:0;color:#475569}.skill-actions{display:flex;gap:8px;align-items:center;margin-top:10px}.skill-actions button{padding:6px 10px}.skill-installed{background:#dcfce7;color:#166534}.skill-unsupported{background:#fee2e2;color:#991b1b}.skill-version{background:#fef3c7;color:#92400e}button:disabled{cursor:not-allowed;opacity:.55}@media(max-width:800px){.skill-toolbar{grid-template-columns:1fr}}
</style>
<h1>Sidecar 协作控制台</h1><p class="muted">Public 广播经过 Relay；项目讨论写入成员共同保存的分布式留言板，私人消息通过已验证的节点直连发送。项目内容不回退 Relay。</p>
<section class="card"><h2>通信对象</h2><label>对象类型</label><select id="target_kind"><option value="public">Public：Relay 广播</option><option value="project">项目：公共留言板</option><option value="private">私人：直连私聊</option></select><div id="public_target"><label>Public 频道</label><input id="channel" value="public"><label>标签（逗号分隔）</label><input id="tags" placeholder="project, recruitment"></div><div id="project_target" style="display:none"><label>项目</label><select id="compose_project"></select><div class="notice">项目消息会写入公共留言板并同步给项目成员；每个成员保存副本，发起者离线不影响使用，内容不经过 Relay。</div><label>项目成员（用于查看能力与信任状态）</label><select id="compose_project_peer"></select></div><div id="private_target" style="display:none"><label>双向信任节点</label><select id="private_message_peer"></select><div class="notice">只有完成双向信任且已交换直连地址的节点才能私聊。消息直接发送到对方 Sidecar，不经过 Relay；Relay 离线时仍可继续交流。</div><button id="private_profile" type="button">查看能力卡片</button> <button id="refresh_private_chat" type="button">刷新私聊记录</button><div id="private_chat_history"><p class="muted">请选择双向信任节点</p></div></div><label>消息内容</label><textarea id="compose_content" rows="5" placeholder="输入要发送的内容"></textarea><button id="send_composed" type="button">发送</button><button id="send_private_message" type="button" style="display:none">发送私聊</button><p id="compose_status" class="muted"></p><p id="status" class="muted"></p></section>
<div class="grid"><section class="card"><h2>节点级长期信任</h2><p class="muted">新节点默认是 pending。发起信任后，对方会收到加密且签名的通知；只有对方也选择信任你时才形成双向长期信任。Relay 或第三方的信任不会传递。双向信任节点会在所有 active 项目中可用作邀请候选，但仍需发送并接受项目邀请。</p><h3>信任通知</h3><div id="trust_notifications">加载中...</div><h3>节点列表</h3><div id="global_peers">加载中...</div><button id="refresh_global_trust" type="button">刷新节点与通知</button><p id="global_trust_status" class="muted"></p><p class="muted">独立入口：<a href="/ui/trust">打开信任管理页面</a></p></section><section class="card"><h2>自定义 Skill</h2><label>Skill ID</label><input id="skill_id" placeholder="my-skill"><label>名称</label><input id="skill_name" placeholder="My Skill"><label>版本</label><input id="skill_version" value="0.1.0"><label>能力（逗号分隔）</label><input id="skill_caps" placeholder="broadcast.read, trace.write"><button id="install">保存 Skill Manifest</button><p id="skill_status" class="muted"></p></section></div>
<section class="card" style="margin-top:16px"><h2>微信全功能 Skill 目录</h2><p class="muted">截至 2026-10-16 的中国大陆个人微信功能地图。每项功能都是独立、可单独安装的 Skill manifest；安装只登记能力说明，执行仍为 disabled-by-default，不会自动操作真实微信账号。</p><div class="skill-toolbar"><div><label>搜索功能</label><input id="wechat_skill_search" placeholder="群聊、朋友圈、支付、扫码、文件传输……"></div><div><label>功能模块</label><select id="wechat_skill_category"><option value="">全部模块</option></select></div><div><label>安装状态</label><select id="wechat_skill_state"><option value="">全部</option><option value="uninstalled">未安装</option><option value="installed">已安装</option><option value="unsupported">不支持/边界说明</option></select></div></div><div class="skill-actions"><button id="install_wechat_filtered" type="button">安装当前筛选</button><button id="install_wechat_all" type="button">安装全部微信 Skills</button><button id="refresh_wechat_catalog" type="button">刷新目录</button><span id="wechat_skill_status" class="muted"></span></div><div id="wechat_skill_summary" class="skill-summary"></div><div id="wechat_skill_catalog" class="skill-grid"><p class="muted">加载中...</p></div></section>
<section class="card" style="margin-top:16px"><h2>新节点 Harness 配置</h2><p class="muted">在新节点自己的 Sidecar 中配置训练 Harness。配置保存在本地；敏感字段不会回显或广播。</p><div class="grid"><div><label>Harness 类型</label><select id="harness_kind"><option value="toy">Toy（本地演示）</option><option value="http">HTTP Harness</option><option value="command">Command</option><option value="deepseek">DeepSeek Command</option><option value="dsh-controller">DSH Controller</option><option value="dsh-training">DSH Training</option><option value="dsh-headless">DSH Headless</option></select><button id="harness_template" type="button">填入示例配置</button></div><div><label>配置状态</label><div id="harness_status" class="item muted">读取中...</div><div id="harness_descriptor" class="muted"></div></div></div><label>JSON 配置</label><textarea id="harness_config_json" rows="13" spellcheck="false" placeholder='{"kind":"toy","seed":7,"samples":40}'></textarea><button id="harness_test" type="button">检查配置</button> <button id="harness_save" type="button">保存并启用</button><p id="harness_message" class="muted"></p></section>
<section class="card" style="margin-top:16px"><h2>项目与项目级信任</h2><p class="muted">项目临时信任只在 active 生命周期内有效。节点主动加入项目后，默认信任项目签名成员集合；该信任不会升级为节点级长期信任。</p><div class="grid"><div><label>项目名称</label><input id="project_name" placeholder="研究项目"><label>项目 ID（留空自动生成）</label><input id="project_id" placeholder="project-demo"><label>Owner Agent ID（留空为当前节点）</label><input id="project_owner" placeholder="agent:..."><button id="create_project">创建/加入本地项目</button></div><div><label>活动项目</label><select id="project_select"></select><label>选择 Harness</label><select id="project_peer"></select><button id="project_trust">发送项目邀请</button> <button id="project_auto_trust" type="button">重新启用成员临时信任</button><p id="project_status" class="muted"></p></div></div><div id="projects"></div><h3>项目邀请</h3><div id="project_invitations">加载中...</div></section>
<section class="card" style="margin-top:16px"><h2>分布式项目留言板</h2><p class="muted">每个项目只有一个逻辑留言板，但每个当前成员都保存经过签名验证的本地副本。留言发布后直接同步给项目成员；任何成员离线都不会阻止其他成员本地读取和发布。</p><label>项目</label><select id="board_project"></select><label>留言内容</label><textarea id="board_content" rows="4" placeholder="发布项目计划、进度、问题或决定"></textarea><button id="board_post" type="button">发布到项目留言板</button> <button id="board_sync" type="button">立即同步本地副本</button><p id="board_status" class="muted"></p><div id="board_events"></div></section>
<section class="card" style="margin-top:16px"><h2>节点能力和网络状态</h2><p class="muted">节点登录时会自动广播硬件、服务、直连地址和 Relay 网络探测结果。</p><button id="refresh_caps">重新检测并广播</button><div id="capabilities">加载中...</div><div id="nodes_caps"></div></section>
<div class="grid" style="margin-top:16px"><section class="card"><h2>公共广播广场</h2><div id="broadcasts">加载中...</div></section><section class="card"><h2>Trace</h2><div id="traces">加载中...</div></section></div><section class="card" style="margin-top:16px"><h2>已安装 Skills</h2><div id="skills">加载中...</div></section><div id="ability_modal" class="modal" style="display:none" onclick="if(event.target===this)this.style.display='none'"><div class="modalbox"><h2 id="ability_modal_title">节点能力卡片</h2><div id="ability_modal_content"></div><button type="button" onclick="document.querySelector('#ability_modal').style.display='none'">关闭</button></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function jsonFetch(url,options){const r=await fetch(url,options);if(!r.ok)throw new Error(`${url} ${r.status}: ${await r.text()}`);return await r.json()}
let peerCache=[];let projectCache=[];let wechatSkillCatalog=[];let installedSkillIds=new Set();let filteredWechatSkillIds=[];
function showAbility(value){const c=value&&value.capabilities?value.capabilities:value||{};document.querySelector('#ability_modal_title').textContent='节点能力卡片';document.querySelector('#ability_modal_content').innerHTML=`<pre>${esc(JSON.stringify(c,null,2))}</pre>`;document.querySelector('#ability_modal').style.display='flex'}
function showManifest(value){document.querySelector('#ability_modal_title').textContent='Skill Manifest';document.querySelector('#ability_modal_content').innerHTML=`<pre>${esc(JSON.stringify(value||{},null,2))}</pre>`;document.querySelector('#ability_modal').style.display='flex'}
function wechatStatusClass(skill){const status=String(skill.verification_status||'');if(status.includes('不支持')||status.includes('边界'))return 'skill-unsupported';if(status.includes('版本')||status.includes('灰度')||status.includes('地区')||status.includes('资格'))return 'skill-version';return ''}
function wechatList(value){return (value||[]).map(x=>esc(x)).join('、')||'—'}
function renderWechatSkills(){const q=document.querySelector('#wechat_skill_search').value.trim().toLowerCase(),category=document.querySelector('#wechat_skill_category').value,state=document.querySelector('#wechat_skill_state').value;const filtered=wechatSkillCatalog.filter(x=>{const installed=installedSkillIds.has(x.skill_id);const text=[x.name,x.skill_id,x.category,x.description,x.operation_entry,...(x.actions||[])].join(' ').toLowerCase();if(q&&!text.includes(q))return false;if(category&&x.category!==category)return false;if(state==='installed'&&!installed)return false;if(state==='uninstalled'&&installed)return false;if(state==='unsupported'&&!String(x.verification_status||'').match(/不支持|边界/))return false;return true});filteredWechatSkillIds=filtered.map(x=>x.skill_id);document.querySelector('#wechat_skill_summary').innerHTML=`<span class="tag">目录 ${wechatSkillCatalog.length}</span><span class="tag">当前显示 ${filtered.length}</span><span class="tag skill-installed">已安装 ${wechatSkillCatalog.filter(x=>installedSkillIds.has(x.skill_id)).length}</span><span class="tag">独立 Skill manifest</span>`;document.querySelector('#wechat_skill_catalog').innerHTML=filtered.map(x=>{const installed=installedSkillIds.has(x.skill_id),statusClass=wechatStatusClass(x);return `<article class="skill-card"><h3>${esc(x.name)} ${installed?'<span class="tag skill-installed">已安装</span>':''}</h3><div><span class="tag">${esc(x.category)}</span><span class="tag ${statusClass}">${esc(x.verification_status)}</span></div><div class="mono">${esc(x.skill_id)}</div><p>${esc(x.description)}</p><p class="muted"><strong>入口：</strong>${esc(x.operation_entry)}</p><details><summary>权限、状态与限制</summary><dl><dt>动作</dt><dd>${wechatList(x.actions)}</dd><dt>角色</dt><dd>${wechatList(x.roles)}</dd><dt>前置</dt><dd>${wechatList(x.prerequisites)}</dd><dt>权限</dt><dd>${esc(x.visibility_permissions)}</dd><dt>状态</dt><dd>${wechatList(x.states_results)}</dd><dt>限制</dt><dd>${wechatList(x.limitations_exceptions)}</dd><dt>平台</dt><dd>${wechatList(x.platforms)}</dd><dt>风险</dt><dd>${esc(x.risk_tier)}</dd></dl></details><div class="skill-actions"><button type="button" ${installed?'disabled':''} onclick='installWechatSkills([${JSON.stringify(x.skill_id)}])'>${installed?'已安装':'安装此 Skill'}</button><button type="button" onclick='showManifest(${JSON.stringify(x).replace(/'/g,"&#39;")})'>查看 Manifest</button></div></article>`}).join('')||'<p class="muted">没有符合筛选条件的功能。</p>'}
async function loadWechatCatalog(){try{const value=await jsonFetch('/v1/skills/catalog');wechatSkillCatalog=value.skills||[];installedSkillIds=new Set(wechatSkillCatalog.filter(x=>x.installed).map(x=>x.skill_id));const select=document.querySelector('#wechat_skill_category'),current=select.value;select.innerHTML='<option value="">全部模块</option>'+(value.categories||[]).map(x=>`<option value="${esc(x.name)}">${esc(x.name)}（${esc(x.count)}）</option>`).join('');select.value=current;document.querySelector('#wechat_skill_status').textContent=`目录版本 ${value.version} · ${value.count} 个独立 Skills`;renderWechatSkills()}catch(e){document.querySelector('#wechat_skill_status').textContent='加载失败：'+e.message}}
async function installWechatSkills(ids){if(!ids.length)return;const label=ids.length===wechatSkillCatalog.length?'全部微信 Skills':`${ids.length} 个 Skills`;try{document.querySelector('#wechat_skill_status').textContent=`正在安装${label}…`;const value=await jsonFetch('/v1/skills/catalog/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_ids:ids})});document.querySelector('#wechat_skill_status').textContent=`已安装 ${value.installed_count} 个 Skills；执行仍为 disabled-by-default`;await loadWechatCatalog();await refresh()}catch(e){document.querySelector('#wechat_skill_status').textContent='安装失败：'+e.message}}
function updateTarget(){const kind=document.querySelector('#target_kind').value;document.querySelector('#public_target').style.display=kind==='public'?'block':'none';document.querySelector('#project_target').style.display=kind==='project'?'block':'none';document.querySelector('#private_target').style.display=kind==='private'?'block':'none';document.querySelector('#send_private_message').style.display=kind==='private'?'inline-block':'none';}
function projectId(){return document.querySelector('#compose_project').value||document.querySelector('#project_select').value||''}
async function refreshProjectCandidates(){const id=projectId();if(!id)return;try{const value=await jsonFetch('/v1/projects/'+encodeURIComponent(id)+'/candidates');const peers=value.peers||[];const options=peers.map(x=>`<option value="${esc(x.agent_id)}">${esc(x.display_name||x.agent_id)}（${esc(x.source||x.trust_state||'pending')}${x.direct&&x.direct.endpoint?'，直连可用':'，无直连'}）</option>`).join('')||'<option value="">暂无候选 Harness</option>';document.querySelector('#project_peer').innerHTML=options;document.querySelector('#compose_project_peer').innerHTML=options}catch(e){document.querySelector('#project_status').textContent='读取项目候选失败: '+e.message}}
function closeProject(projectId){if(!confirm(`结束项目 ${projectId}？项目级信任将立即撤销。`))return;jsonFetch(`/v1/projects/${encodeURIComponent(projectId)}/close`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(()=>refresh()).catch(e=>document.querySelector('#project_status').textContent='结束项目失败: '+e.message)}
const HARNESS_TEMPLATES={toy:{kind:'toy',seed:7,samples:40,node_index:0,non_iid:false},http:{kind:'http',base_url:'http://127.0.0.1:8900',timeout:300},command:{kind:'command',train_command:['python','/path/to/train_entrypoint.py','--request','{input}','--response','{output}'],evaluate_command:['python','/path/to/evaluate_entrypoint.py','--request','{input}','--response','{output}'],cwd:''},deepseek:{kind:'deepseek',train_command:['python','/path/to/deepseek_harness/train_entrypoint.py','--request','{input}','--response','{output}'],evaluate_command:['python','/path/to/deepseek_harness/evaluate_entrypoint.py','--request','{input}','--response','{output}'],cwd:''},'dsh-controller':{kind:'dsh-controller',controller_url:'http://127.0.0.1:8081',registry_token:'',timeout:900,poll_interval:1},'dsh-training':{kind:'dsh-training',controller_url:'http://127.0.0.1:8081',registry_token:'',timeout:900,poll_interval:1,task_capabilities:['python'],task_tools:['dsh','python']},'dsh-headless':{kind:'dsh-headless',dsh_bin:'dsh',profile:'headless',timeout:900,permission_mode:'workspace-write',telemetry_mode:'DISABLED'}};
function setHarnessTemplate(){const kind=document.querySelector('#harness_kind').value;document.querySelector('#harness_config_json').value=JSON.stringify(HARNESS_TEMPLATES[kind],null,2)}
function setHarnessDescriptor(d){document.querySelector('#harness_descriptor').textContent=d?`能力：${d.name||'unknown'} ${d.version||''} · runtime ${d.runtime||'unknown'} · tasks ${(d.supported_tasks||[]).join(', ')||'未声明'}`:''}
async function loadHarnessConfig(){try{const r=await jsonFetch('/v1/harness/config');const c=r.config||HARNESS_TEMPLATES.toy;document.querySelector('#harness_kind').value=c.kind||c.name||'toy';document.querySelector('#harness_config_json').value=JSON.stringify(c,null,2);document.querySelector('#harness_status').textContent=r.configured?'已配置（敏感字段已隐藏）':'未配置，将使用 CLI 的 Toy 默认 Harness'}catch(e){document.querySelector('#harness_status').textContent='读取失败：'+e.message}}
async function inspectHarness(save){let c;try{c=JSON.parse(document.querySelector('#harness_config_json').value)}catch(e){document.querySelector('#harness_message').textContent='JSON 格式错误：'+e.message;return}try{const r=await jsonFetch(save?'/v1/harness/config':'/v1/harness/config/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:c})});document.querySelector('#harness_status').textContent=save?'已保存并启用（敏感字段已隐藏）':'检查通过，尚未保存';document.querySelector('#harness_message').textContent=save?'新节点下次启动联邦 Node 时会自动读取此配置':'配置可用';setHarnessDescriptor(r.descriptor)}catch(e){document.querySelector('#harness_message').textContent=(save?'保存失败：':'检查失败：')+e.message}}
async function requestNodeTrust(agent,key){try{const r=await jsonFetch('/v1/peers/trust-requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:agent,expected_signing_key:key})});document.querySelector('#global_trust_status').textContent='已发出签名信任通知：'+r.relationship_state;await refresh()}catch(e){document.querySelector('#global_trust_status').textContent='发起信任失败: '+e.message}}
async function respondNodeTrust(agent,decision){try{const r=await jsonFetch('/v1/peers/trust-response',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:agent,decision})});document.querySelector('#global_trust_status').textContent=decision==='accept'?'已建立反向信任':decision==='reject'?'已拒绝反向信任':'已阻止节点';await refresh()}catch(e){document.querySelector('#global_trust_status').textContent='处理信任通知失败: '+e.message}}
async function revokeNodeTrust(agent){if(!confirm('撤销对此节点的长期信任？对方会收到撤销通知。'))return;try{await jsonFetch('/v1/peers/trust/'+encodeURIComponent(agent),{method:'DELETE'});document.querySelector('#global_trust_status').textContent='已撤销并通知对方';await refresh()}catch(e){document.querySelector('#global_trust_status').textContent='撤销失败: '+e.message}}
async function refresh(){try{const localCaps=await jsonFetch('/v1/capabilities');const h=localCaps.hardware||{},n=localCaps.network||{},lh=localCaps.harness||{},ld=localCaps.direct||{};document.querySelector('#capabilities').innerHTML=`<div class="item"><strong>Hardware</strong><div class="muted">${esc(h.platform||'unknown')} / ${esc(h.architecture||'unknown')} / CPU ${esc(h.cpu_logical_cores||'?')} / GPU ${h.gpu&&h.gpu.present?'有':'无'} / Camera ${h.camera&&h.camera.present?'有':'无'}</div></div><div class="item"><strong>Network</strong><div class="muted">${esc(n.stability||'unknown')} | success ${esc(n.success_rate??'')} | p95 ${esc(n.p95_latency_ms??'')} ms</div></div><div class="item"><strong>Direct</strong><div class="muted">${ld.enabled?'已启用':'未配置'} ${esc(ld.endpoint||'')}</div></div><div class="item"><strong>Harness</strong><div class="muted">${esc(lh.name||'未配置')} ${esc(lh.version||'')} · ${esc(lh.runtime||'')}</div></div>`;const nodes=await jsonFetch('/v1/nodes/capabilities');document.querySelector('#nodes_caps').innerHTML=`<h3>已广播节点 (${(nodes.nodes||[]).length})</h3>`+(nodes.nodes||[]).map(x=>{const c=x.capabilities||{},hh=c.hardware||{},nn=c.network||{},dd=c.direct||{},nh=c.harness||{};return `<div class="item"><strong>${esc(x.sender_id)}</strong> <span class="tag">${dd.enabled?'direct':'relay only'}</span><button type="button" onclick='showAbility(${JSON.stringify(x).replace(/'/g,"&#39;")})'>查看能力</button><div class="muted">${esc(hh.platform||'')} / GPU ${hh.gpu&&hh.gpu.present?'有':'无'} / network ${esc(nn.stability||'unknown')} / p95 ${esc(nn.p95_latency_ms??'')} ms / Harness ${esc(nh.name||'未声明')} / ${esc(dd.endpoint||'')}</div></div>`}).join('');const bd=await jsonFetch('/v1/broadcasts');document.querySelector('#broadcasts').innerHTML=(bd.broadcasts||[]).map(x=>`<div class="item"><strong>${esc(x.channel)}</strong> <span class="muted">${esc(new Date(x.created_at*1000).toLocaleString())}</span><div>${(x.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div><pre>${esc(typeof x.content==='string'?x.content:JSON.stringify(x.content,null,2))}</pre><div class="mono">${esc(x.sender_id)}</div></div>`).join('')||'<p class="muted">暂无广播</p>';const td=await jsonFetch('/v1/traces');document.querySelector('#traces').innerHTML=(td.traces||[]).map(x=>`<div class="item"><strong>${esc(x.event_type)}</strong><div class="muted">${esc(new Date(x.created_at*1000).toLocaleString())}</div><pre>${esc(JSON.stringify(x.payload,null,2))}</pre></div>`).join('')||'<p class="muted">暂无 trace</p>';const sd=await jsonFetch('/v1/skills');installedSkillIds=new Set((sd.skills||[]).map(x=>x.skill_id));document.querySelector('#skills').innerHTML=(sd.skills||[]).map(x=>`<div class="item"><strong>${esc(x.name)}</strong> <span class="tag">${esc(x.version)}</span><div class="mono">${esc(x.skill_id)}</div></div>`).join('')||'<p class="muted">暂无 Skill</p>';if(wechatSkillCatalog.length)renderWechatSkills();const invitations=await jsonFetch('/v1/project-invitations');const incomingInvites=(invitations.incoming||[]).map(i=>`<div class="item"><strong>${esc(i.project_name)}</strong> <span class="tag">${esc(i.status)}</span><div class="mono">${esc(i.project_id)}</div><div class="muted">Owner ${esc(i.owner_agent_id)} · ${esc(i.invitation_kind)}</div>${i.status==='pending'?`<button onclick='respondProjectInvitation(${JSON.stringify(i.invitation_id)},"accept")'>接受并加入</button> <button onclick='respondProjectInvitation(${JSON.stringify(i.invitation_id)},"reject")'>拒绝</button>`:''}</div>`).join('');const outgoingInvites=(invitations.outgoing||[]).map(i=>`<div class="item"><strong>发给 ${esc(i.invitee_agent_id)}</strong> <span class="tag">${esc(i.status)}</span><div class="mono">${esc(i.project_id)}</div></div>`).join('');document.querySelector('#project_invitations').innerHTML=(incomingInvites+outgoingInvites)||'<p class="muted">暂无项目邀请</p>';const pd=await jsonFetch('/v1/projects');projectCache=pd.projects||[];const active=projectCache.filter(x=>x.status==='active');const opts=active.map(x=>`<option value="${esc(x.project_id)}">${esc(x.name)} (${esc(x.project_id)})</option>`).join('')||'<option value="">暂无活动项目</option>';document.querySelector('#project_select').innerHTML=opts;document.querySelector('#compose_project').innerHTML=opts;document.querySelector('#board_project').innerHTML=opts;document.querySelector('#projects').innerHTML=projectCache.map(x=>`<div class="item"><strong>${esc(x.name)}</strong> <span class="tag">${esc(x.status)}</span><div class="mono">${esc(x.project_id)}</div><div class="muted">owner: ${esc(x.owner_id)} | members: ${(x.project_members||x.members||[]).map(m=>esc(m.agent_id)).join(', ')||'none'} | auto trust: ${x.auto_trust_members?'on':'off'}</div>${x.status==='active'?`<button class="danger" onclick="closeProject('${esc(x.project_id)}')">结束项目并撤销临时信任</button>`:''}</div>`).join('')||'<p class="muted">暂无项目</p>';const trustFeed=await jsonFetch('/v1/peers/trust-notifications');const relationLabels={pending:'待审核',trusting:'我已信任，等待对方',trusted_by_peer:'对方已信任我',mutual:'双向信任',rejected_by_peer:'对方拒绝反向信任',revoked_by_peer:'对方已撤销信任',revoked:'已撤销',blocked:'已阻止',blocked_by_peer:'被对方阻止'};document.querySelector('#trust_notifications').innerHTML=(trustFeed.notifications||[]).map(n=>`<div class="item"><strong>${esc(n.issuer_agent_id)}</strong> <span class="tag">${esc(n.decision)}</span><div class="muted">${esc(relationLabels[n.relationship_state]||n.relationship_state)} · ${esc(new Date(n.issued_at*1000).toLocaleString())}</div>${n.decision==='trust'&&!n.outgoing_trust?`<button onclick='respondNodeTrust(${JSON.stringify(n.issuer_agent_id)},"accept")'>接受并反向信任</button> <button onclick='respondNodeTrust(${JSON.stringify(n.issuer_agent_id)},"reject")'>拒绝</button> <button class="danger" onclick='respondNodeTrust(${JSON.stringify(n.issuer_agent_id)},"block")'>阻止</button>`:''}</div>`).join('')||'<p class="muted">暂无新信任通知</p>';const peers=await jsonFetch('/v1/peers/candidates');peerCache=peers.peers||[];const rows=peerCache.map(x=>{const relation=x.relationship_state||'pending';let actions='';if(x.outgoing_trust){actions=`<button class="danger" onclick='revokeNodeTrust(${JSON.stringify(x.agent_id)})'>撤销我的信任</button>`}else if(relation!=='blocked'){actions=`<button onclick='requestNodeTrust(${JSON.stringify(x.agent_id)},${JSON.stringify(x.signing_key||'')})'>信任此节点</button>`}if(x.incoming_trust&&!x.outgoing_trust){actions+=` <button onclick='respondNodeTrust(${JSON.stringify(x.agent_id)},"accept")'>接受双向信任</button>`}return `<div class="item"><strong>${esc(x.display_name||x.agent_id)}</strong> <span class="tag">${esc(relationLabels[relation]||relation)}</span><div class="mono">${esc(x.agent_id)}</div><div class="muted">来源 ${esc(x.source||x.trust_state||'pending')} · 签名指纹 ${esc(x.key_fingerprint||'unknown')}</div>${actions}</div>`}).join('')||'<p class="muted">暂无其他节点</p>';document.querySelector('#global_peers').innerHTML=rows;const trusted=peerCache.filter(x=>x.relationship_state==='mutual');document.querySelector('#private_message_peer').innerHTML=trusted.map(x=>`<option value="${esc(x.agent_id)}">${esc(x.display_name||x.agent_id)}（双向信任 · 直连 ${x.direct&&x.direct.endpoint?'可用':'未配置'}）</option>`).join('')||'<option value="">暂无可私聊的双向信任节点</option>';await refreshPrivateChat();await refreshProjectCandidates();await refreshBoard();document.querySelector('#status').textContent=''}catch(e){document.querySelector('#status').textContent='控制台读取失败: '+e.message}}
async function refreshPrivateChat(){const peer=document.querySelector('#private_message_peer').value;const box=document.querySelector('#private_chat_history');if(!peer){box.innerHTML='<p class="muted">暂无可私聊的双向信任节点</p>';return}try{const value=await jsonFetch('/v1/private-messages?peer_id='+encodeURIComponent(peer)+'&limit=200');box.innerHTML=(value.messages||[]).map(m=>`<div class="item"><strong>${m.direction==='outgoing'?'我 → 对方':'对方 → 我'}</strong> <span class="tag">${esc(m.type)}</span><span class="muted"> ${esc(new Date(m.created_at*1000).toLocaleString())}</span><pre>${esc(typeof m.payload==='string'?m.payload:JSON.stringify(m.payload,null,2))}</pre></div>`).join('')||'<p class="muted">暂无私聊记录</p>'}catch(e){box.innerHTML=`<p class="warning">读取私聊失败：${esc(e.message)}</p>`}}
async function refreshBoard(){const id=document.querySelector('#board_project').value;if(!id){document.querySelector('#board_events').innerHTML='<p class="muted">暂无活动项目</p>';return}try{const board=await jsonFetch(`/v1/projects/${encodeURIComponent(id)}/board`);document.querySelector('#board_events').innerHTML=(board.events||[]).map(e=>`<div class="item"><strong>${esc(e.event_type)}</strong> <span class="tag">clock ${esc(e.logical_clock)}</span><span class="muted">${esc(new Date(e.created_at*1000).toLocaleString())}</span><div class="mono">${esc(e.author_agent_id)}</div><pre>${esc(typeof e.content==='string'?e.content:JSON.stringify(e.content,null,2))}</pre></div>`).join('')||'<p class="muted">暂无留言</p>';document.querySelector('#board_status').textContent=`本地副本 ${board.event_count||0} 条 · epoch ${board.membership_epoch||''}`}catch(e){document.querySelector('#board_status').textContent='读取留言板失败: '+e.message}}
async function postBoard(){const id=document.querySelector('#board_project').value,text=document.querySelector('#board_content').value.trim();if(!id||!text){document.querySelector('#board_status').textContent='请选择项目并输入留言';return}try{const r=await jsonFetch(`/v1/projects/${encodeURIComponent(id)}/board`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_type:'post',content:{text}})});const failed=(r.sync&&r.sync.deliveries||[]).filter(x=>x.status==='failed').length;document.querySelector('#board_status').textContent=failed?`已保存到本地；${failed} 个成员暂时离线，稍后自动同步`:'已保存并同步';document.querySelector('#board_content').value='';await refreshBoard()}catch(e){document.querySelector('#board_status').textContent='发布失败: '+e.message}}
async function syncBoard(){const id=document.querySelector('#board_project').value;if(!id)return;try{const r=await jsonFetch(`/v1/projects/${encodeURIComponent(id)}/board/sync`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});document.querySelector('#board_status').textContent=`同步完成：${r.event_count||0} 条本地事件`;await refreshBoard()}catch(e){document.querySelector('#board_status').textContent='同步失败: '+e.message}}
async function sendComposed(){const kind=document.querySelector('#target_kind').value;const target={kind};if(kind==='public'){target.channel=document.querySelector('#channel').value;target.tags=document.querySelector('#tags').value.split(',').map(x=>x.trim()).filter(Boolean)}if(kind==='project'){target.project_id=projectId()}if(kind==='private'){target.recipient_id=document.querySelector('#private_message_peer').value}const content=document.querySelector('#compose_content').value.trim();if(!content){document.querySelector('#compose_status').textContent='请输入消息内容';return}try{let r;if(kind==='project'){r=await jsonFetch(`/v1/projects/${encodeURIComponent(target.project_id)}/board`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_type:'post',content:{text:content}})})}else{r=await jsonFetch('/v1/messages/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target,message:{type:kind==='public'?'public.broadcast':'chat.message',payload:{text:content}},ttl_seconds:86400})})}document.querySelector('#compose_status').textContent=kind==='public'?'已通过 Relay 广播':kind==='project'?'已写入项目公共留言板':`已发送，直连投递：${JSON.stringify(r.deliveries||[])}`;document.querySelector('#compose_content').value='';if(kind==='private')await refreshPrivateChat();else await refresh()}catch(e){document.querySelector('#compose_status').textContent='发送失败（不会回退 Relay）：'+e.message}}
async function projectTrust(){const id=document.querySelector('#project_select').value,agent=document.querySelector('#project_peer').value;if(!id||!agent)return;try{const r=await jsonFetch(`/v1/projects/${encodeURIComponent(id)}/invitations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:agent})});document.querySelector('#project_status').textContent='邀请已发送；对方接受前不会成为项目成员。类型：'+r.invitation.invitation_kind;await refresh()}catch(e){document.querySelector('#project_status').textContent='发送项目邀请失败: '+e.message}}
async function respondProjectInvitation(id,decision){try{await jsonFetch(`/v1/project-invitations/${encodeURIComponent(id)}/response`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision})});document.querySelector('#project_status').textContent=decision==='accept'?'已接受邀请，等待 Owner 确认成员清单':'已拒绝邀请';await refresh()}catch(e){document.querySelector('#project_status').textContent='处理项目邀请失败: '+e.message}}
async function enableAutoTrust(){const id=document.querySelector('#project_select').value;if(!id)return;if(!confirm('确认后将自动信任此项目当前成员，以及以后由项目发起者签名加入的成员；不会升级为长期信任。'))return;try{await jsonFetch(`/v1/projects/${encodeURIComponent(id)}/auto-trust`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:true})});document.querySelector('#project_status').textContent='已启用项目成员自动信任';await refresh()}catch(e){document.querySelector('#project_status').textContent='启用失败: '+e.message}}
function bindDashboard(){try{
 document.querySelector('#target_kind').addEventListener('change',updateTarget);
 document.querySelector('#send_composed').addEventListener('click',sendComposed);
 document.querySelector('#send_private_message').addEventListener('click',sendComposed);
 document.querySelector('#project_trust').addEventListener('click',projectTrust);
 document.querySelector('#project_auto_trust').addEventListener('click',enableAutoTrust);
 document.querySelector('#compose_project').addEventListener('change',refreshProjectCandidates);
 document.querySelector('#project_select').addEventListener('change',refreshProjectCandidates);
 document.querySelector('#board_project').addEventListener('change',refreshBoard);
 document.querySelector('#board_post').addEventListener('click',postBoard);
 document.querySelector('#board_sync').addEventListener('click',syncBoard);
 document.querySelector('#private_profile').addEventListener('click',()=>{const p=peerCache.find(x=>x.agent_id===document.querySelector('#private_message_peer').value);showAbility({capabilities:p||{}})});document.querySelector('#private_message_peer').addEventListener('change',refreshPrivateChat);document.querySelector('#refresh_private_chat').addEventListener('click',refreshPrivateChat);
 document.querySelector('#create_project').addEventListener('click',async()=>{try{await jsonFetch('/v1/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.querySelector('#project_name').value,project_id:document.querySelector('#project_id').value,owner_id:document.querySelector('#project_owner').value})});document.querySelector('#project_status').textContent='项目已创建/加入';await refresh()}catch(e){document.querySelector('#project_status').textContent='项目创建失败: '+e.message}});
 document.querySelector('#install').addEventListener('click',async()=>{try{await jsonFetch('/v1/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_id:document.querySelector('#skill_id').value,name:document.querySelector('#skill_name').value,version:document.querySelector('#skill_version').value,capabilities:document.querySelector('#skill_caps').value.split(',').map(x=>x.trim()).filter(Boolean),execution:'disabled-by-default',source:'local-user'})});document.querySelector('#skill_status').textContent='Skill manifest 已保存';await refresh()}catch(e){document.querySelector('#skill_status').textContent='保存失败: '+e.message}});
 document.querySelector('#wechat_skill_search').addEventListener('input',renderWechatSkills);
 document.querySelector('#wechat_skill_category').addEventListener('change',renderWechatSkills);
 document.querySelector('#wechat_skill_state').addEventListener('change',renderWechatSkills);
 document.querySelector('#install_wechat_filtered').addEventListener('click',()=>installWechatSkills(filteredWechatSkillIds));
 document.querySelector('#install_wechat_all').addEventListener('click',()=>installWechatSkills(wechatSkillCatalog.map(x=>x.skill_id)));
 document.querySelector('#refresh_wechat_catalog').addEventListener('click',loadWechatCatalog);
 document.querySelector('#refresh_global_trust').addEventListener('click',refresh);
 document.querySelector('#refresh_caps').addEventListener('click',async()=>{try{await jsonFetch('/v1/capabilities/refresh',{method:'POST'});await refresh()}catch(e){document.querySelector('#status').textContent='能力刷新失败: '+e.message}});
 document.querySelector('#harness_template').addEventListener('click',setHarnessTemplate);
 document.querySelector('#harness_test').addEventListener('click',()=>inspectHarness(false));
 document.querySelector('#harness_save').addEventListener('click',()=>inspectHarness(true));
 document.body.dataset.dashboardReady='true';updateTarget();loadWechatCatalog();refresh();loadHarnessConfig();setInterval(refresh,5000)
}catch(e){document.body.dataset.dashboardReady='false';document.querySelector('#status').textContent='控制台初始化失败: '+e.message;console.error(e)}}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',bindDashboard,{once:true})}else{bindDashboard()}

</script>
"""


class Sidecar:
    def __init__(self, data_dir: Path, relay_url: str, relay_token: str = "", direct_endpoint: str = "", direct_tls_fingerprint: str = "", project_key_backend: str = "sqlite", project_key_command: Optional[list[str]] = None):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.identity = Identity.load_or_create(data_dir / "identity.json")
        self.store = LocalStore(data_dir / "state.db", create_project_key_backend(project_key_backend, data_dir, project_key_command))
        self.project_key_backend = project_key_backend
        self.relay = RelayClient(relay_url, self.identity, relay_token)
        self.direct_endpoint = str(direct_endpoint or "").rstrip("/")
        self.direct_tls_fingerprint = str(direct_tls_fingerprint or "").lower().replace(":", "")
        self.relay.register()
        self.bootstrap_runtime()
        self.capabilities = collect_capabilities(self.identity.agent_id, data_dir, relay_url, relay_token, self.direct_endpoint, self.direct_tls_fingerprint)
        descriptor_path = data_dir / "harness-descriptor.json"
        if descriptor_path.exists():
            try:
                descriptor = json.loads(descriptor_path.read_text("utf-8"))
                if isinstance(descriptor, dict):
                    self.capabilities["harness"] = descriptor
            except (OSError, json.JSONDecodeError):
                pass
        (data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        self.announce_capabilities()

    def announce_capabilities(self) -> Dict[str, Any]:
        broadcast = create_broadcast(self.identity, "node.capabilities", self.capabilities, "json", ["node", "capabilities", "login"], 900)
        self.relay.publish_broadcast(broadcast)
        self.store.add_trace("node.capabilities.broadcast", self.identity.agent_id, {"broadcast_id": broadcast["broadcast_id"], "hardware": self.capabilities["hardware"], "network": self.capabilities["network"]})
        return broadcast

    def refresh_capabilities(self) -> Dict[str, Any]:
        harness = self.capabilities.get("harness")
        self.capabilities = collect_capabilities(self.identity.agent_id, self.store.data_dir, self.relay.relay_url(), self.relay.access_token, self.direct_endpoint, self.direct_tls_fingerprint)
        if harness:
            self.capabilities["harness"] = harness
        (self.store.data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return self.announce_capabilities()

    def capability_nodes(self, limit: int = 100) -> list[Dict[str, Any]]:
        values = self.relay.broadcasts(limit, "node.capabilities")
        latest: Dict[str, Dict[str, Any]] = {}
        for broadcast in values:
            sender = broadcast.get("sender_id")
            if not sender or sender in latest:
                continue
            try:
                identity = self.relay.identity_for(sender)
                verify_broadcast(broadcast, identity)
            except Exception:
                self.store.add_trace("node.capabilities.rejected", self.identity.agent_id, {"sender_id": sender, "broadcast_id": broadcast.get("broadcast_id"), "reason": "invalid signature"})
                continue
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

    def wechat_skill_catalog(self) -> Dict[str, Any]:
        installed_ids = [skill.get("skill_id", "") for skill in self.store.skills()]
        return wechat_catalog_payload(installed_ids)

    def install_wechat_skills(self, skill_ids: Optional[list[str]] = None) -> Dict[str, Any]:
        requested = list(skill_ids or [])
        if not requested:
            manifests = wechat_skill_catalog()
        else:
            if len(requested) > 500 or any(not isinstance(skill_id, str) for skill_id in requested):
                raise ValueError("skill_ids must be a list of at most 500 strings")
            manifests = []
            missing = []
            for skill_id in dict.fromkeys(requested):
                manifest = wechat_skill_by_id(skill_id)
                if manifest is None:
                    missing.append(skill_id)
                else:
                    manifests.append(manifest)
            if missing:
                raise ValueError("unknown WeChat skill IDs: " + ", ".join(missing[:10]))
        for manifest in manifests:
            self.store.upsert_skill(manifest)
        installed_ids = [manifest["skill_id"] for manifest in manifests]
        self.store.add_trace(
            "wechat.skill_catalog.installed",
            self.identity.agent_id,
            {"catalog_version": WECHAT_CATALOG_VERSION, "count": len(installed_ids), "skill_ids": installed_ids[:50], "truncated": len(installed_ids) > 50},
        )
        return {"catalog_version": WECHAT_CATALOG_VERSION, "installed_count": len(installed_ids), "skill_ids": installed_ids}

    def trust(self, agent_id: str, expected_signing_key: Optional[str] = None):
        peer = self.relay.identity_for(agent_id)
        if expected_signing_key and peer.signing_key != expected_signing_key:
            raise ValueError("peer signing key does not match the expected fingerprint material")
        self.store.trust(peer)
        return peer

    def revoke(self, agent_id: str) -> bool:
        return self.store.revoke(agent_id)

    def _peer_relationship(self, agent_id: str) -> Dict[str, Any]:
        local = next((peer for peer in self.store.peers() if peer["agent_id"] == agent_id), {})
        incoming = self.store.latest_peer_trust_statement(agent_id, "incoming")
        outgoing = self.store.latest_peer_trust_statement(agent_id, "outgoing")
        local_state = str(local.get("trust_state") or "pending")
        incoming_decision = str((incoming or {}).get("decision") or "")
        outgoing_decision = str((outgoing or {}).get("decision") or "")
        outgoing_trust = local_state == "trusted"
        incoming_trust = incoming_decision == "trust"
        if local_state == "blocked":
            relationship_state = "blocked"
        elif incoming_decision == "block":
            relationship_state = "blocked_by_peer"
        elif outgoing_trust and incoming_trust:
            relationship_state = "mutual"
        elif outgoing_trust and incoming_decision == "reject":
            relationship_state = "rejected_by_peer"
        elif outgoing_trust and incoming_decision == "revoke":
            relationship_state = "revoked_by_peer"
        elif outgoing_trust:
            relationship_state = "trusting"
        elif incoming_trust:
            relationship_state = "trusted_by_peer"
        elif local_state == "revoked":
            relationship_state = "revoked"
        else:
            relationship_state = "pending"
        return {
            "relationship_state": relationship_state,
            "outgoing_trust": outgoing_trust,
            "incoming_trust": incoming_trust,
            "incoming_decision": incoming_decision or None,
            "outgoing_decision": outgoing_decision or None,
        }

    def _private_trust_ready(self, agent_id: str) -> bool:
        relationship = self._peer_relationship(agent_id)
        if relationship["relationship_state"] == "mutual":
            return True
        # Compatibility for the legacy API: callers that explicitly called
        # trust() on both nodes have no signed trust statements to describe
        # the relationship, but the receiving side still enforces its own
        # local trust decision.
        return bool(
            relationship["outgoing_trust"]
            and not relationship["incoming_decision"]
            and not relationship["outgoing_decision"]
        )

    def _send_peer_trust_statement(self, peer: PublicIdentity, decision: str) -> Dict[str, Any]:
        statement = create_trust_statement(self.identity, peer, decision, self.capabilities.get("direct"))
        envelope = encrypt_message(
            self.identity,
            peer,
            self.store.next_sequence(peer.agent_id),
            "node.trust.statement",
            statement,
            ttl_seconds=30 * 86400,
        )
        self.relay.send(envelope)
        self.store.save_peer_trust_statement(statement, "outgoing", envelope["message_id"])
        self.store.add_trace(
            "node.trust.statement_sent",
            self.identity.agent_id,
            {"statement_id": statement["statement_id"], "subject_agent_id": peer.agent_id, "decision": decision, "message_id": envelope["message_id"]},
        )
        return {"statement": statement, "message_id": envelope["message_id"], **self._peer_relationship(peer.agent_id)}

    def request_peer_trust(self, agent_id: str, expected_signing_key: Optional[str] = None) -> Dict[str, Any]:
        peer = self.relay.identity_for(agent_id)
        if peer.agent_id == self.identity.agent_id:
            raise ValueError("cannot create a trust relationship with the local node")
        if expected_signing_key and peer.signing_key != expected_signing_key:
            raise ValueError("peer signing key does not match the expected fingerprint material")
        self.store.trust(peer)
        return self._send_peer_trust_statement(peer, "trust")

    def respond_peer_trust(self, agent_id: str, decision: str) -> Dict[str, Any]:
        normalized = str(decision or "").lower()
        if normalized == "accept":
            normalized = "trust"
        if normalized not in {"trust", "reject", "block"}:
            raise ValueError("trust response must be accept, reject or block")
        incoming = self.store.latest_peer_trust_statement(agent_id, "incoming")
        if not incoming or incoming.get("decision") != "trust":
            raise ValueError("no incoming trust request exists for this peer")
        peer = self.relay.identity_for(agent_id)
        if normalized == "trust":
            self.store.trust(peer)
        elif normalized == "block":
            self.store.block(peer)
        else:
            self.store.revoke(agent_id)
        return self._send_peer_trust_statement(peer, normalized)

    def revoke_peer_trust(self, agent_id: str) -> Dict[str, Any]:
        peer = self.store.peer(agent_id)
        if peer is None:
            try:
                peer = self.relay.identity_for(agent_id)
            except Exception:
                peer = None
        changed = self.store.revoke(agent_id)
        if peer is None:
            return {"revoked": changed, "notification_status": "peer_identity_unavailable", **self._peer_relationship(agent_id)}
        try:
            result = self._send_peer_trust_statement(peer, "revoke")
            return {"revoked": changed, "notification_status": "sent", **result}
        except Exception as exc:
            self.store.add_trace("node.trust.revoke_notification_failed", self.identity.agent_id, {"agent_id": agent_id, "error": str(exc)})
            return {"revoked": changed, "notification_status": "failed", "notification_error": str(exc), **self._peer_relationship(agent_id)}

    def poll_peer_trust_notifications(self) -> Dict[str, Any]:
        accepted = []
        rejected = []
        for envelope in self.relay.inbox(500):
            message_id = str(envelope.get("message_id") or "")
            if envelope.get("project_id"):
                continue
            if self.store.has_received(message_id):
                self.relay.acknowledge(message_id)
                self.store.mark_acknowledged(message_id)
                continue
            try:
                sender = self.relay.identity_for(str(envelope.get("sender_id") or ""))
                body = decrypt_message(self.identity, envelope, sender)
            except Exception:
                continue
            if body.get("type") != "node.trust.statement":
                continue
            try:
                statement = body.get("payload") or {}
                verify_trust_statement(statement, sender, self.identity.public())
                issuer_direct = statement.get("issuer_direct") or {}
                if issuer_direct.get("endpoint"):
                    self.store.save_peer_direct_contact(
                        sender.agent_id,
                        issuer_direct["endpoint"],
                        issuer_direct.get("tls_fingerprint", ""),
                        issuer_direct.get("protocol", "sidecar-direct/1"),
                        "signed-trust-statement",
                    )
                inserted = self.store.save_peer_trust_statement(statement, "incoming", message_id)
                self.store.save_received(envelope, body)
                self.relay.acknowledge(message_id)
                self.store.mark_acknowledged(message_id)
                if inserted:
                    accepted.append(statement["statement_id"])
                    self.store.add_trace("node.trust.statement_received", sender.agent_id, {"statement_id": statement["statement_id"], "decision": statement["decision"], "message_id": message_id})
            except Exception as exc:
                rejected.append({"message_id": message_id, "sender_id": envelope.get("sender_id"), "error": str(exc)})
                self.relay.acknowledge(message_id)
                self.store.add_trace("node.trust.statement_rejected", self.identity.agent_id, {"message_id": message_id, "sender_id": envelope.get("sender_id"), "error": str(exc)})
        return {"accepted_statement_ids": accepted, "rejected": rejected}

    def peer_trust_notifications(self) -> Dict[str, Any]:
        polling = self.poll_peer_trust_notifications()
        latest = {}
        for statement in self.store.peer_trust_statements("incoming", limit=500):
            latest.setdefault(statement["issuer_agent_id"], statement)
        notifications = []
        for agent_id, statement in latest.items():
            notifications.append({**statement, **self._peer_relationship(agent_id)})
        notifications.sort(key=lambda item: (int(item.get("issued_at") or 0), item.get("statement_id", "")), reverse=True)
        return {"notifications": notifications, "polling": polling}

    def send_project_invitation(self, project_id: str, agent_id: str, ttl_seconds: int = 7 * 86400) -> Dict[str, Any]:
        project = self.store.project(project_id)
        if not project or not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        if project.get("owner_id") != self.identity.agent_id:
            raise ValueError("only the project owner can invite members")
        if self.store.project_member(project_id, agent_id):
            raise ValueError("node is already a project member")
        invitee = self.relay.identity_for(agent_id)
        if invitee.agent_id == self.identity.agent_id:
            raise ValueError("cannot invite the local node")
        relationship = self._peer_relationship(agent_id)
        invitation_kind = "mutual-node-trust" if relationship["relationship_state"] == "mutual" else "project-temporary-trust"
        invitation = create_project_invitation(
            self.identity,
            invitee,
            project,
            self.project_manifest(project_id),
            invitation_kind,
            ttl_seconds,
        )
        envelope = encrypt_message(
            self.identity,
            invitee,
            self.store.next_sequence(invitee.agent_id),
            "project.invitation",
            invitation,
            ttl_seconds=max(60, int(ttl_seconds)),
        )
        self.relay.send(envelope)
        self.store.save_project_invitation(invitation, "outgoing", envelope["message_id"])
        self.store.add_trace("project.invitation.sent", self.identity.agent_id, {"project_id": project_id, "invitation_id": invitation["invitation_id"], "invitee_agent_id": agent_id, "invitation_kind": invitation_kind})
        return {"invitation": invitation, "message_id": envelope["message_id"], "status": "pending"}

    def respond_project_invitation(self, invitation_id: str, decision: str) -> Dict[str, Any]:
        self.poll_project_invitation_notifications()
        stored = self.store.project_invitation(invitation_id)
        if not stored or stored.get("direction") != "incoming":
            raise ValueError("project invitation not found")
        if stored.get("status") != "pending":
            raise ValueError("project invitation was already handled")
        owner = self.relay.identity_for(stored["owner_agent_id"])
        verify_project_invitation(stored, owner, self.identity.public())
        normalized = str(decision or "").lower()
        response = create_invitation_response(self.identity, stored, normalized)
        envelope = encrypt_message(
            self.identity,
            owner,
            self.store.next_sequence(owner.agent_id),
            "project.invitation.response",
            response,
            ttl_seconds=max(60, int(stored["expires_at"]) - int(time.time())),
        )
        if normalized == "accept":
            self.create_project(stored["project_name"], stored["project_id"], owner_id=stored["owner_agent_id"])
        self.relay.send(envelope)
        updated = self.store.update_project_invitation(invitation_id, "accepted" if normalized == "accept" else "rejected", response, envelope["message_id"])
        self.store.add_trace("project.invitation.responded", self.identity.agent_id, {"project_id": stored["project_id"], "invitation_id": invitation_id, "decision": normalized})
        return updated

    def poll_project_invitation_notifications(self) -> Dict[str, Any]:
        accepted = []
        rejected = []
        completed = []
        for envelope in self.relay.inbox(500):
            message_id = str(envelope.get("message_id") or "")
            if envelope.get("project_id"):
                continue
            if self.store.has_received(message_id):
                self.relay.acknowledge(message_id)
                self.store.mark_acknowledged(message_id)
                continue
            try:
                sender = self.relay.identity_for(str(envelope.get("sender_id") or ""))
                body = decrypt_message(self.identity, envelope, sender)
            except Exception:
                continue
            message_type = body.get("type")
            if message_type not in {"project.invitation", "project.invitation.response"}:
                continue
            try:
                payload = body.get("payload") or {}
                if message_type == "project.invitation":
                    verify_project_invitation(payload, sender, self.identity.public())
                    inserted = self.store.save_project_invitation(payload, "incoming", message_id)
                    if inserted:
                        accepted.append(payload["invitation_id"])
                        self.store.add_trace("project.invitation.received", sender.agent_id, {"project_id": payload["project_id"], "invitation_id": payload["invitation_id"], "invitation_kind": payload["invitation_kind"]})
                else:
                    invitation_id = str(payload.get("invitation_id") or "")
                    stored = self.store.project_invitation(invitation_id)
                    if not stored or stored.get("direction") != "outgoing":
                        raise ValueError("project invitation response has no matching outgoing invitation")
                    verify_invitation_response(payload, stored, sender, self.identity.public())
                    status = "accepted" if payload["decision"] == "accept" else "rejected"
                    self.store.update_project_invitation(invitation_id, status, payload, message_id)
                    if payload["decision"] == "accept":
                        membership = self.trust_for_project(stored["project_id"], sender.agent_id, sender.signing_key)
                        target_delivery = next((item for item in membership.get("membership_deliveries", []) if item.get("recipient_id") == sender.agent_id), None)
                        delivery_complete = bool(target_delivery and target_delivery.get("status") != "failed")
                        final_status = "completed" if delivery_complete else "accepted"
                        self.store.update_project_invitation(invitation_id, final_status, payload, message_id)
                        completed.append({"invitation_id": invitation_id, "membership": membership, "membership_delivery_pending": not delivery_complete})
                    else:
                        completed.append({"invitation_id": invitation_id, "decision": "reject"})
                    self.store.add_trace("project.invitation.response_received", sender.agent_id, {"project_id": stored["project_id"], "invitation_id": invitation_id, "decision": payload["decision"]})
                self.store.save_received(envelope, body)
                self.relay.acknowledge(message_id)
                self.store.mark_acknowledged(message_id)
            except Exception as exc:
                rejected.append({"message_id": message_id, "sender_id": envelope.get("sender_id"), "error": str(exc)})
                self.relay.acknowledge(message_id)
                self.store.add_trace("project.invitation.rejected", self.identity.agent_id, {"message_id": message_id, "sender_id": envelope.get("sender_id"), "error": str(exc)})
        return {"accepted_invitation_ids": accepted, "completed": completed, "rejected": rejected}

    def retry_accepted_project_invitations(self) -> list[Dict[str, Any]]:
        results = []
        for invitation in self.store.project_invitations("outgoing", "accepted", 500):
            try:
                project_id = invitation["project_id"]
                invitee_id = invitation["invitee_agent_id"]
                if not self.store.project_member(project_id, invitee_id):
                    raise ValueError("accepted invitee is not present in the project membership")
                membership = self.project_manifest(project_id)
                deliveries = self._distribute_project_manifest(project_id, membership)
                target_delivery = next((item for item in deliveries if item.get("recipient_id") == invitee_id), None)
                if not target_delivery or target_delivery.get("status") == "failed":
                    raise ValueError((target_delivery or {}).get("error") or "invitee membership delivery is still unavailable")
                self.store.update_project_invitation(invitation["invitation_id"], "completed", invitation.get("response"), invitation.get("response_message_id") or "")
                result = {"invitation_id": invitation["invitation_id"], "status": "completed", "delivery": target_delivery}
                self.store.add_trace("project.invitation.membership_delivered", self.identity.agent_id, {"project_id": project_id, "invitation_id": invitation["invitation_id"], "invitee_agent_id": invitee_id})
            except Exception as exc:
                result = {"invitation_id": invitation["invitation_id"], "status": "accepted", "error": str(exc)}
            results.append(result)
        return results

    def project_invitation_notifications(self) -> Dict[str, Any]:
        polling = self.poll_project_invitation_notifications()
        retries = self.retry_accepted_project_invitations()
        return {
            "incoming": self.store.project_invitations("incoming", limit=500),
            "outgoing": self.store.project_invitations("outgoing", limit=500),
            "polling": polling,
            "membership_delivery_retries": retries,
        }

    def peer_candidates(self, project_id: str = "") -> list[Dict[str, Any]]:
        existing = {peer["agent_id"]: peer for peer in self.store.peers()}
        capability_map = {item["sender_id"]: item.get("capabilities", {}) for item in self.capability_nodes()}
        identities: Dict[str, PublicIdentity] = {}
        for identity in self.relay.identities():
            if identity.agent_id != self.identity.agent_id:
                identities[identity.agent_id] = identity
        project_members = {item["agent_id"]: item for item in self.store.project_members(project_id)} if project_id else {}
        for agent_id, member in project_members.items():
            if agent_id == self.identity.agent_id:
                continue
            try:
                identities[agent_id] = PublicIdentity.from_dict(member)
            except Exception:
                continue
        output = []
        for agent_id, identity in sorted(identities.items()):
            local = existing.get(agent_id, {})
            project_member = project_members.get(agent_id)
            if project_member:
                source = "project-member"
            elif local.get("trust_state") == "trusted":
                source = "global-trusted"
            else:
                source = local.get("trust_state", "pending")
            capabilities = capability_map.get(agent_id, {})
            advertised_direct = capabilities.get("direct") if isinstance(capabilities.get("direct"), dict) else {}
            member_endpoint = str(project_member.get("direct_endpoint") or "") if project_member else ""
            direct = {
                "endpoint": member_endpoint or str(advertised_direct.get("endpoint") or "").rstrip("/"),
                "tls_fingerprint": str(advertised_direct.get("tls_fingerprint") or "").lower().replace(":", ""),
                "enabled": bool(member_endpoint or advertised_direct.get("enabled")),
            }
            output.append({
                **identity.as_dict(),
                "trust_state": local.get("trust_state", "pending"),
                "project_trusted": bool(project_id and self.store.project_peer(project_id, agent_id)),
                "trusted_at": local.get("trusted_at"),
                "revoked_at": local.get("revoked_at"),
                "key_fingerprint": identity.signing_key[:16],
                "display_name": local.get("display_name", ""),
                "source": source,
                "direct": direct,
                **self._peer_relationship(agent_id),
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

    _HARNESS_SECRET_KEYS = {"api_key", "access_token", "registry_token", "token", "password", "secret"}
    _HARNESS_SECRET_PLACEHOLDERS = {"", "********", "[configured]"}

    @property
    def harness_config_path(self) -> Path:
        return self.store.data_dir / "harness-config.json"

    @classmethod
    def _redact_harness_config(cls, value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {name: cls._redact_harness_config(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_harness_config(item, key) for item in value]
        if key.lower() in cls._HARNESS_SECRET_KEYS and value:
            return "********"
        return value

    @classmethod
    def _preserve_harness_secrets(cls, value: Any, previous: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            previous = previous if isinstance(previous, dict) else {}
            return {name: cls._preserve_harness_secrets(item, previous.get(name), name) for name, item in value.items()}
        if key.lower() in cls._HARNESS_SECRET_KEYS and value in cls._HARNESS_SECRET_PLACEHOLDERS and previous:
            return previous
        return value

    def _read_harness_config(self) -> Optional[Dict[str, Any]]:
        if not self.harness_config_path.exists():
            return None
        value = json.loads(self.harness_config_path.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("harness config must be a JSON object")
        return value

    def harness_config(self) -> Dict[str, Any]:
        config = self._read_harness_config()
        if config is None:
            return {"configured": False, "config": {"kind": "toy"}}
        return {"configured": True, "config": self._redact_harness_config(config)}

    def test_harness_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        # Import after the federated package is initialized; the existing
        # Harness classes depend on federated.types.
        from . import federated  # noqa: F401
        from .harness_factory import load_harness_config
        adapter = load_harness_config(config)
        descriptor = asdict(adapter.describe())
        return {"ok": True, "config": self._redact_harness_config(config), "descriptor": descriptor}

    def configure_harness(self, config: Dict[str, Any]) -> Dict[str, Any]:
        previous = self._read_harness_config() or {}
        config = self._preserve_harness_secrets(config, previous)
        result = self.test_harness_config(config)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.store.data_dir, prefix=".harness-config-", suffix=".json", delete=False) as handle:
                temporary_path = Path(handle.name)
                json.dump(config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.harness_config_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        descriptor_path = self.store.data_dir / "harness-descriptor.json"
        descriptor_path.write_text(json.dumps(result["descriptor"], ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.chmod(descriptor_path, 0o600)
        self.capabilities["harness"] = result["descriptor"]
        (self.store.data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        self.announce_capabilities()
        self.store.add_trace("harness.configured", self.identity.agent_id, {"kind": config.get("kind", "toy"), "name": result["descriptor"].get("name"), "version": result["descriptor"].get("version")})
        return {"configured": True, **result}

    def set_direct_endpoint(self, endpoint: str, tls_fingerprint: str = "") -> Dict[str, Any]:
        self.direct_endpoint = str(endpoint or "").rstrip("/")
        self.direct_tls_fingerprint = str(tls_fingerprint or "").lower().replace(":", "")
        self.capabilities["direct"] = {
            "enabled": bool(self.direct_endpoint),
            "endpoint": self.direct_endpoint,
            "tls_fingerprint": self.direct_tls_fingerprint,
            "protocol": "sidecar-direct/1",
        }
        (self.store.data_dir / "capabilities.json").write_text(json.dumps(self.capabilities, ensure_ascii=False, indent=2) + "\n", "utf-8")
        for project in self.store.projects():
            if project.get("status") == "active":
                self.store.set_project_member_endpoint(project["project_id"], self.identity.agent_id, self.direct_endpoint)
        return self.announce_capabilities()

    def _capability_for(self, agent_id: str) -> Dict[str, Any]:
        for value in self.capability_nodes():
            if value.get("sender_id") == agent_id:
                capabilities = value.get("capabilities") or {}
                return capabilities if isinstance(capabilities, dict) else {}
        return {}

    def _direct_info_for(self, project_id: str, agent_id: str) -> Dict[str, Any]:
        project_endpoint = self.store.project_member_endpoint(project_id, agent_id) if project_id else ""
        capabilities = self._capability_for(agent_id)
        direct = capabilities.get("direct") if isinstance(capabilities.get("direct"), dict) else {}
        # A fresh signed capability with direct.enabled=false is an explicit
        # revocation of a previously cached trust-statement endpoint.
        if project_endpoint:
            return {
                "endpoint": project_endpoint,
                "tls_fingerprint": str(direct.get("tls_fingerprint") or "").lower().replace(":", ""),
                "enabled": True,
                "source": "project-membership",
            }
        if direct and direct.get("enabled") is False:
            return {"endpoint": "", "tls_fingerprint": "", "enabled": False, "source": "signed-capability-broadcast"}
        cached = self.store.peer_direct_contact(agent_id) or {}
        if cached.get("endpoint") and not direct:
            return {
                "endpoint": cached["endpoint"],
                "tls_fingerprint": str(cached.get("tls_fingerprint") or "").lower().replace(":", ""),
                "enabled": True,
                "source": cached.get("source", "cached"),
            }
        endpoint = str(direct.get("endpoint") or "").rstrip("/")
        if endpoint:
            self.store.save_peer_direct_contact(
                agent_id,
                endpoint,
                direct.get("tls_fingerprint", ""),
                direct.get("protocol", "sidecar-direct/1"),
                "signed-capability-broadcast",
            )
        return {
            "endpoint": endpoint,
            "tls_fingerprint": str(direct.get("tls_fingerprint") or "").lower().replace(":", ""),
            "enabled": bool(endpoint and direct.get("enabled", True)),
            "source": "signed-capability-broadcast" if endpoint else "unavailable",
        }

    @staticmethod
    def _direct_url(endpoint: str) -> str:
        endpoint = str(endpoint or "").rstrip("/")
        if not endpoint:
            raise ValueError("recipient has no direct endpoint")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("direct endpoint must be an http(s) URL")
        return endpoint + "/v1/direct/messages"

    def _direct_request(self, endpoint: str, payload: Dict[str, Any], tls_fingerprint: str = "") -> Dict[str, Any]:
        url = self._direct_url(endpoint)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        parsed = urlparse(url)
        context = None
        expected = str(tls_fingerprint or "").lower().replace(":", "")
        if parsed.scheme == "https" and expected:
            # Pinning is explicit; certificate-chain validation is replaced by
            # the configured fingerprint check for operator-managed certificates.
            context = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=10, context=context) as response:
                if parsed.scheme == "https" and expected:
                    raw = getattr(getattr(response, "fp", None), "raw", None)
                    sock = getattr(raw, "_sock", None)
                    certificate = sock.getpeercert(binary_form=True) if sock is not None else b""
                    actual = hashlib.sha256(certificate).hexdigest() if certificate else ""
                    if actual != expected:
                        raise ValueError("direct TLS fingerprint mismatch")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"direct endpoint returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"direct endpoint unavailable: {exc.reason}") from exc

    def _send_direct_to_peer(self, peer: PublicIdentity, endpoint: str, message_type: str, payload: Any, ttl_seconds: int = 86400, project_id: str = "", scope: str = "project", trace_prefix: str = "project.direct") -> Dict[str, Any]:
        if not endpoint:
            raise ValueError("recipient has no direct endpoint")
        envelope = encrypt_message(self.identity, peer, self.store.next_sequence(peer.agent_id), message_type, payload, ttl_seconds, project_id)
        direct = self._direct_info_for(project_id, peer.agent_id)
        try:
            result = self._direct_request(endpoint, {"scope": scope, "envelope": envelope}, direct.get("tls_fingerprint", ""))
        except Exception as exc:
            self.store.add_trace(f"{trace_prefix}.delivery_failed", self.identity.agent_id, {"message_id": envelope["message_id"], "recipient_id": peer.agent_id, "project_id": project_id or None, "error": str(exc)})
            raise
        self.store.add_trace(f"{trace_prefix}.sent", self.identity.agent_id, {"message_id": envelope["message_id"], "recipient_id": peer.agent_id, "project_id": project_id or None, "transport": "direct"})
        return {"message_id": envelope["message_id"], "recipient_id": peer.agent_id, "status": result.get("status", "delivered")}

    def send_direct(self, recipient_id: str, message_type: str, payload: Any, ttl_seconds: int = 86400, project_id: str = "", scope: str = "private") -> Dict[str, Any]:
        peer = self.trusted_peer(recipient_id, project_id if scope == "project" else "")
        if not peer:
            raise ValueError("recipient is not trusted for this direct scope")
        if scope == "private" and not self._private_trust_ready(recipient_id):
            raise ValueError("private chat requires established mutual trust")
        direct = self._direct_info_for(project_id, recipient_id)
        if not direct.get("endpoint"):
            raise ValueError("recipient direct endpoint is unavailable")
        result = self._send_direct_to_peer(peer, direct["endpoint"], message_type, payload, ttl_seconds, project_id, scope, "private.direct" if scope == "private" else "project.direct")
        if scope == "private":
            self.store.save_private_message(result["message_id"], recipient_id, "outgoing", message_type, payload, int(time.time()), result.get("status", "delivered"))
        return result

    def send_routed(self, target: Dict[str, Any], message: Dict[str, Any], ttl_seconds: int = 86400) -> Dict[str, Any]:
        kind = str((target or {}).get("kind") or "").lower()
        message_type = str((message or {}).get("type") or "chat.message")
        payload = (message or {}).get("payload")
        if kind == "public":
            broadcast = self.publish_broadcast(str((target or {}).get("channel") or "public"), payload, "json", (target or {}).get("tags") or [], ttl_seconds)
            return {"kind": "public", "transport": "relay", "broadcast_id": broadcast["broadcast_id"]}
        if kind == "private":
            recipient_id = str((target or {}).get("recipient_id") or "")
            result = self.send_direct(recipient_id, message_type, payload, ttl_seconds, "", "private")
            return {"kind": "private", "transport": "direct", "deliveries": [result]}
        if kind == "project":
            project_id = str((target or {}).get("project_id") or "")
            if not project_id or not self.store.project_active(project_id):
                raise ValueError("project is closed or unknown")
            deliveries = []
            members = self.store.project_members(project_id)
            if not members:
                raise ValueError("project has no verified members")
            for member in members:
                if member["agent_id"] == self.identity.agent_id:
                    continue
                try:
                    peer = self.trusted_peer(member["agent_id"], project_id)
                    if not peer:
                        raise ValueError("member is not trusted for this project")
                    endpoint = str(member.get("direct_endpoint") or self._direct_info_for(project_id, member["agent_id"]).get("endpoint") or "")
                    deliveries.append(self._send_direct_to_peer(peer, endpoint, message_type, payload, ttl_seconds, project_id, "project", "project.direct"))
                except Exception as exc:
                    deliveries.append({"recipient_id": member["agent_id"], "status": "failed", "error": str(exc)})
            if not deliveries:
                raise ValueError("project has no other members")
            return {"kind": "project", "transport": "direct", "project_id": project_id, "deliveries": deliveries}
        raise ValueError("target.kind must be public, project or private")

    def _project_owner(self, project_id: str) -> Optional[PublicIdentity]:
        project = self.store.project(project_id)
        if not project:
            return None
        owner = self.store.project_member(project_id, project["owner_id"])
        if owner:
            return owner
        try:
            return self.relay.identity_for(project["owner_id"])
        except Exception:
            return None

    def _build_project_manifest(self, project_id: str) -> Dict[str, Any]:
        project = self.store.project(project_id)
        if not project or project["owner_id"] != self.identity.agent_id:
            raise ValueError("only the project owner can issue membership manifests")
        members = self.store.project_members(project_id)
        if not any(item["agent_id"] == self.identity.agent_id for item in members):
            self.store.upsert_project_member(project_id, self.identity.public(), self.direct_endpoint, "owner")
            members = self.store.project_members(project_id)
        previous = self.store.project_manifest(project_id)
        epoch = int(previous["epoch"]) + 1 if previous else 1
        content_key = generate_project_key()
        manifest = {"project_id": project_id, "owner_id": self.identity.agent_id, "epoch": epoch, "issued_at": int(time.time()), "members": [{key: item[key] for key in ("agent_id", "signing_key", "encryption_key", "proof", "direct_endpoint", "role")} for item in members]}
        manifest["content_key_fingerprint"] = key_fingerprint(content_key)
        manifest["content_key_envelopes"] = {}
        for member in members:
            member_identity = PublicIdentity.from_dict(member)
            key_envelope = encrypt_message(
                self.identity,
                member_identity,
                self.store.next_sequence(member_identity.agent_id),
                "project.content.key",
                {"project_id": project_id, "epoch": epoch, "key_b64": b64e(content_key), "key_fingerprint": key_fingerprint(content_key)},
                ttl_seconds=365 * 86400,
                project_id=project_id,
            )
            manifest["content_key_envelopes"][member_identity.agent_id] = key_envelope
        manifest["participant_set_hash"] = participant_set_hash(manifest)
        signature = self.identity.sign(canonical(manifest))
        self.store.save_project_manifest(project_id, epoch, manifest, signature)
        self.store.save_project_content_key(project_id, epoch, b64e(content_key), key_fingerprint(content_key))
        return {"manifest": manifest, "signature": signature, "history": self.store.project_manifest_history(project_id)}

    def _distribute_project_manifest(self, project_id: str, membership: Dict[str, Any]) -> list[Dict[str, Any]]:
        deliveries = []
        for member in self.store.project_members(project_id):
            agent_id = member["agent_id"]
            if agent_id == self.identity.agent_id:
                continue
            try:
                peer = self.trusted_peer(agent_id, project_id)
                endpoint = str(member.get("direct_endpoint") or self._direct_info_for(project_id, agent_id).get("endpoint") or "")
                if not peer or not endpoint:
                    raise ValueError("project member direct endpoint is unavailable")
                deliveries.append(self._send_direct_to_peer(peer, endpoint, "project.membership.manifest", membership, project_id=project_id, scope="membership", trace_prefix="project.membership"))
            except Exception as exc:
                deliveries.append({"recipient_id": agent_id, "status": "failed", "error": str(exc)})
        return deliveries

    def project_manifest(self, project_id: str) -> Dict[str, Any]:
        saved = self.store.project_manifest(project_id)
        if saved:
            return {"manifest": saved["manifest"], "signature": saved["signature"], "history": self.store.project_manifest_history(project_id)}
        return self._build_project_manifest(project_id)

    def _verify_and_store_manifest_history(self, project_id: str, history: Any, owner: PublicIdentity, current_epoch: int = 0) -> None:
        if history is None:
            return
        if not isinstance(history, list) or len(history) > 5000:
            raise ValueError("project membership history must be a list")
        seen = set()
        for entry in history:
            if not isinstance(entry, dict):
                raise ValueError("malformed project membership history entry")
            historical_manifest = entry.get("manifest") or {}
            historical_signature = str(entry.get("signature") or "")
            epoch = int(historical_manifest.get("epoch") or 0)
            if epoch <= 0 or epoch in seen:
                raise ValueError("invalid or duplicate project membership history epoch")
            seen.add(epoch)
            if current_epoch and epoch == int(current_epoch):
                continue
            if historical_manifest.get("project_id") != project_id or historical_manifest.get("owner_id") != owner.agent_id:
                raise ValueError("project membership history scope mismatch")
            if historical_manifest.get("participant_set_hash") != participant_set_hash(historical_manifest):
                raise ValueError("project membership history participant set hash mismatch")
            if not verify_signature(owner.signing_key, historical_signature, canonical(historical_manifest)):
                raise ValueError("invalid project membership history signature")
            self.store.save_project_manifest(project_id, epoch, historical_manifest, historical_signature)
            self._install_manifest_content_key(project_id, historical_manifest, owner)

    def _install_manifest_content_key(self, project_id: str, manifest: Dict[str, Any], owner: PublicIdentity) -> bool:
        epoch = int(manifest.get("epoch") or 0)
        envelopes = manifest.get("content_key_envelopes") or {}
        envelope = envelopes.get(self.identity.agent_id)
        if not envelope:
            return False
        body = decrypt_message(self.identity, envelope, owner)
        if body.get("type") != "project.content.key":
            raise ValueError("invalid project content key envelope type")
        payload = body.get("payload") or {}
        if payload.get("project_id") != project_id or int(payload.get("epoch") or 0) != epoch:
            raise ValueError("project content key envelope scope mismatch")
        key_b64 = str(payload.get("key_b64") or "")
        key = b64d(key_b64)
        if len(key) != 32 or key_fingerprint(key) != payload.get("key_fingerprint") or payload.get("key_fingerprint") != manifest.get("content_key_fingerprint"):
            raise ValueError("project content key fingerprint mismatch")
        self.store.save_project_content_key(project_id, epoch, key_b64, key_fingerprint(key))
        return True

    def apply_project_manifest(self, value: Dict[str, Any]) -> Dict[str, Any]:
        manifest = value.get("manifest") or {}
        signature = str(value.get("signature") or "")
        project_id = str(manifest.get("project_id") or "")
        project = self.store.project(project_id)
        if not project or not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        if manifest.get("owner_id") != project.get("owner_id"):
            raise ValueError("project owner mismatch")
        owner = self._project_owner(project_id)
        if not owner or not verify_signature(owner.signing_key, signature, canonical(manifest)):
            self.store.add_trace("project.membership.rejected", self.identity.agent_id, {"project_id": project_id, "reason": "invalid owner signature"})
            raise ValueError("invalid project membership signature")
        if int(manifest.get("epoch", 0)) <= 0:
            raise ValueError("invalid project membership epoch")
        members = manifest.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError("project membership manifest requires members")
        parsed_members = [PublicIdentity.from_dict(member) for member in members]
        incoming_ids = [identity.agent_id for identity in parsed_members]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("project membership manifest contains duplicate members")
        owner_entries = [member for member, identity in zip(members, parsed_members) if identity.agent_id == manifest["owner_id"] and member.get("role") == "owner"]
        if len(owner_entries) != 1:
            raise ValueError("project membership manifest must include exactly one owner entry")
        if manifest.get("participant_set_hash") != participant_set_hash(manifest):
            raise ValueError("project membership participant set hash mismatch")
        is_new_manifest = self.store.save_project_manifest(project_id, int(manifest["epoch"]), manifest, signature)
        self._install_manifest_content_key(project_id, manifest, owner)
        self._verify_and_store_manifest_history(project_id, value.get("history"), owner, int(manifest["epoch"]))
        if not is_new_manifest:
            return {"project_id": project_id, "status": "stale", "history_epochs": [item["epoch"] for item in self.store.project_manifest_history(project_id)]}
        existing_ids = {member["agent_id"] for member in self.store.project_members(project_id)}
        for removed_id in sorted(existing_ids - set(incoming_ids)):
            self.store.revoke_project_member(project_id, removed_id)
            self.store.add_trace("project.member_revoked", self.identity.agent_id, {"project_id": project_id, "agent_id": removed_id, "epoch": manifest["epoch"]})
        for member, identity in zip(members, parsed_members):
            self.store.upsert_project_member(project_id, identity, member.get("direct_endpoint", ""), member.get("role", "member"))
            if self.store.project_auto_trust(project_id) and identity.agent_id != self.identity.agent_id:
                self.store.trust_for_project(project_id, identity)
                self.store.add_trace("project.member_auto_trusted", self.identity.agent_id, {"project_id": project_id, "agent_id": identity.agent_id})
        self.store.add_trace("project.membership.accepted", self.identity.agent_id, {"project_id": project_id, "epoch": manifest["epoch"], "member_count": len(manifest.get("members", []))})
        return {"project_id": project_id, "status": "accepted", "epoch": manifest["epoch"]}

    def enable_project_auto_trust(self, project_id: str, enabled: bool = True) -> Dict[str, Any]:
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        self.store.set_project_auto_trust(project_id, enabled)
        trusted = []
        if enabled:
            for member in self.store.project_members(project_id):
                if member["agent_id"] != self.identity.agent_id:
                    self.store.trust_for_project(project_id, PublicIdentity.from_dict(member))
                    trusted.append(member["agent_id"])
            self.store.add_trace("project.auto_trust.enabled", self.identity.agent_id, {"project_id": project_id, "member_ids": trusted})
        else:
            self.store.add_trace("project.auto_trust.disabled", self.identity.agent_id, {"project_id": project_id})
        return {"project_id": project_id, "auto_trust_members": bool(enabled), "trusted_member_ids": trusted}

    def _safe_capability_for(self, agent_id: str) -> Dict[str, Any]:
        try:
            return self._capability_for(agent_id)
        except Exception:
            return {}

    def _probe_direct_health(self, endpoint: str, tls_fingerprint: str = "") -> Dict[str, Any]:
        endpoint = str(endpoint or "").rstrip("/")
        if not endpoint:
            return {"online": False, "latency_ms": None, "error": "missing endpoint"}
        parsed = urlparse(endpoint + "/v1/direct/healthz")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"online": False, "latency_ms": None, "error": "invalid endpoint"}
        context = None
        expected = str(tls_fingerprint or "").lower().replace(":", "")
        if parsed.scheme == "https" and expected:
            context = ssl._create_unverified_context()
        started = time.monotonic()
        request = urllib.request.Request(endpoint + "/v1/direct/healthz", method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=3, context=context) as response:
                if parsed.scheme == "https" and expected:
                    raw = getattr(getattr(response, "fp", None), "raw", None)
                    sock = getattr(raw, "_sock", None)
                    certificate = sock.getpeercert(binary_form=True) if sock is not None else b""
                    actual = hashlib.sha256(certificate).hexdigest() if certificate else ""
                    if actual != expected:
                        raise ValueError("direct TLS fingerprint mismatch")
                body = json.loads(response.read().decode("utf-8"))
                if body.get("status") != "ok":
                    raise ValueError("direct health check returned a non-ok status")
                return {"online": True, "latency_ms": round((time.monotonic() - started) * 1000.0, 3), "error": None}
        except Exception as exc:
            return {"online": False, "latency_ms": round((time.monotonic() - started) * 1000.0, 3), "error": str(exc)}

    def _gossip_peer_score(self, project_id: str, member: Dict[str, Any], probe: bool = False) -> Dict[str, Any]:
        agent_id = member["agent_id"]
        endpoint = str(member.get("direct_endpoint") or "").rstrip("/")
        capability = self._safe_capability_for(agent_id)
        advertised_direct = capability.get("direct") if isinstance(capability.get("direct"), dict) else {}
        if not endpoint:
            endpoint = str(advertised_direct.get("endpoint") or "").rstrip("/")
        probe_result = self._probe_direct_health(endpoint, str(advertised_direct.get("tls_fingerprint") or "")) if probe else {"online": None, "latency_ms": None, "error": None}
        network = capability.get("network") if isinstance(capability.get("network"), dict) else {}
        try:
            p95_latency = float(network.get("p95_latency_ms") or 999999.0)
        except (TypeError, ValueError):
            p95_latency = 999999.0
        metric = self.store.project_gossip_peer_metric(project_id, agent_id) or {}
        now = int(time.time())
        half_life = 3600.0
        attempts = self.store.project_gossip_attempts(project_id, agent_id, 100)
        success = 0.0
        failure = 0.0
        for attempt in attempts[:20]:
            age = max(0, now - int(attempt.get("recorded_at") or now))
            weight = math.pow(0.5, age / half_life)
            if attempt.get("success"):
                success += weight
            else:
                failure += weight
        if not attempts:
            success_age = max(0, now - int(metric.get("last_success_at") or now))
            failure_age = max(0, now - int(metric.get("last_failure_at") or now))
            success = float(metric.get("success_score") or 0.0) * math.pow(0.5, success_age / half_life)
            failure = float(metric.get("failure_score") or 0.0) * math.pow(0.5, failure_age / half_life)
        last_failure = int(metric.get("last_failure_at") or 0)
        cooldown_seconds = 30
        cooldown_active = bool(last_failure and (now - last_failure) < cooldown_seconds)
        availability = 3 if probe_result["online"] is True else (0 if probe_result["online"] is False else (1 if cooldown_active else (2 if endpoint else 0)))
        observed_latency = probe_result.get("latency_ms")
        if isinstance(observed_latency, (int, float)) and observed_latency >= 0:
            p95_latency = min(p95_latency, float(observed_latency))
        health = max(-20, min(20, success - (failure * 2)))
        score = (availability, health, -p95_latency, agent_id)
        return {
            "agent_id": agent_id,
            "endpoint": endpoint,
            "available": bool(endpoint),
            "probe_online": probe_result["online"],
            "probe_latency_ms": probe_result["latency_ms"],
            "probe_error": probe_result["error"],
            "p95_latency_ms": p95_latency if p95_latency < 999999.0 else None,
            "recent_successes": round(success, 4),
            "recent_failures": round(failure, 4),
            "success_score_raw": float(metric.get("success_score") or 0.0),
            "failure_score_raw": float(metric.get("failure_score") or 0.0),
            "window_attempts": len(attempts),
            "window_size": 20,
            "decay_half_life_seconds": half_life,
            "last_failure_at": last_failure or None,
            "failure_cooldown_seconds": cooldown_seconds,
            "failure_cooldown_active": cooldown_active,
            "score": [availability, health, -p95_latency, agent_id],
        }

    def project_gossip_peers(self, project_id: str, force: bool = False) -> list[Dict[str, Any]]:
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        members = [member for member in self.store.project_members(project_id) if member["agent_id"] != self.identity.agent_id]
        scores = {member["agent_id"]: self._gossip_peer_score(project_id, member) for member in members}
        for member in members:
            member["gossip"] = scores[member["agent_id"]]
        members.sort(key=lambda member: (not scores[member["agent_id"]]["available"], -scores[member["agent_id"]]["recent_successes"], scores[member["agent_id"]]["recent_failures"], scores[member["agent_id"]]["p95_latency_ms"] if scores[member["agent_id"]]["p95_latency_ms"] is not None else 999999.0, member["agent_id"]))
        if force:
            return members
        fanout = min(self.store.project_gossip_fanout(project_id), len(members))
        if fanout <= 0:
            return []
        return members[:fanout]

    def project_gossip_peer_scores(self, project_id: str, probe: bool = False) -> list[Dict[str, Any]]:
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        return [self._gossip_peer_score(project_id, member, probe=probe) for member in self.store.project_members(project_id) if member["agent_id"] != self.identity.agent_id]

    def set_project_gossip_fanout(self, project_id: str, fanout: int) -> Dict[str, Any]:
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        self.store.set_project_gossip_fanout(project_id, fanout)
        return {"project_id": project_id, "gossip_fanout": self.store.project_gossip_fanout(project_id), "peer_ids": [member["agent_id"] for member in self.project_gossip_peers(project_id)]}

    def remove_project_member(self, project_id: str, agent_id: str) -> Dict[str, Any]:
        project = self.store.project(project_id)
        if not project or not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        if project.get("owner_id") != self.identity.agent_id:
            raise ValueError("only the project owner can remove members")
        if agent_id == self.identity.agent_id:
            raise ValueError("project owner cannot remove itself")
        removed_identity = self.store.project_member(project_id, agent_id)
        if not removed_identity:
            raise ValueError("project member not found")
        removed_endpoint = self.store.project_member_endpoint(project_id, agent_id) or self._direct_info_for(project_id, agent_id).get("endpoint", "")
        self.store.revoke_project_member(project_id, agent_id)
        membership = self._build_project_manifest(project_id)
        deliveries = self._distribute_project_manifest(project_id, membership)
        if removed_endpoint:
            try:
                deliveries.append(self._send_direct_to_peer(removed_identity, removed_endpoint, "project.membership.manifest", membership, project_id=project_id, scope="membership", trace_prefix="project.membership.revocation"))
            except Exception as exc:
                deliveries.append({"recipient_id": agent_id, "status": "failed", "error": str(exc), "reason": "revocation_manifest"})
        self.store.add_trace("project.member_removed", self.identity.agent_id, {"project_id": project_id, "agent_id": agent_id, "epoch": membership["manifest"]["epoch"]})
        return {"project_id": project_id, "agent_id": agent_id, "status": "removed", "membership_epoch": membership["manifest"]["epoch"], "membership_deliveries": deliveries}

    def create_project(self, name: str, project_id: str = "", owner_id: str = "") -> Dict[str, Any]:
        project_id = project_id or "project-" + uuid.uuid4().hex[:12]
        owner_id = owner_id or self.identity.agent_id
        existing = self.store.project(project_id)
        project = self.store.create_project(project_id, name, owner_id)
        owner_identity = self.identity.public() if owner_id == self.identity.agent_id else self.relay.identity_for(owner_id)
        owner_direct = self.direct_endpoint if owner_id == self.identity.agent_id else self._direct_info_for(project_id, owner_id).get("endpoint", "")
        self.store.upsert_project_member(project_id, owner_identity, owner_direct, "owner")
        # Accepting/creating a project establishes project-scoped trust for the
        # signed member set by default. It never changes global trust.
        self.store.set_project_auto_trust(project_id, True)
        if owner_identity.agent_id != self.identity.agent_id:
            self.store.trust_for_project(project_id, owner_identity)
        if owner_id == self.identity.agent_id and existing is None:
            self._build_project_manifest(project_id)
        event_type = "project.joined" if existing is not None else "project.created"
        self.store.add_trace(event_type, self.identity.agent_id, {"project_id": project_id, "name": name, "owner_id": owner_id})
        return {**project, "project_members": self.store.project_members(project_id), "auto_trust_members": self.store.project_auto_trust(project_id)}

    def trust_for_project(self, project_id: str, agent_id: str, expected_signing_key: Optional[str] = None) -> Dict[str, Any]:
        peer = self.relay.identity_for(agent_id)
        if expected_signing_key and peer.signing_key != expected_signing_key:
            raise ValueError("peer signing key does not match the expected fingerprint material")
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        direct = self._direct_info_for(project_id, agent_id)
        globally_trusted = agent_id in self.store.trusted_ids()
        self.store.upsert_project_member(project_id, peer, direct.get("endpoint", ""), "member")
        self.store.trust_for_project(project_id, peer)
        self.store.add_trace("project.peer_trusted", self.identity.agent_id, {"project_id": project_id, "agent_id": agent_id, "scope": "global" if globally_trusted else "project", "source": "global" if globally_trusted else "manual"})
        membership = None
        deliveries = []
        if self.store.project(project_id).get("owner_id") == self.identity.agent_id:
            membership = self._build_project_manifest(project_id)
            deliveries = self._distribute_project_manifest(project_id, membership)
        return {"project_id": project_id, "agent_id": agent_id, "trust_scope": "global" if globally_trusted else "project", "status": "already_trusted" if globally_trusted else "trusted", "membership_epoch": (membership or {}).get("manifest", {}).get("epoch"), "membership_deliveries": deliveries}

    def _active_project_manifest(self, project_id: str) -> Dict[str, Any]:
        if not self.store.project_active(project_id):
            raise ValueError("project is closed or unknown")
        saved = self.store.project_manifest(project_id)
        if not saved:
            raise ValueError("project membership manifest is unavailable")
        if not self.store.project_member(project_id, self.identity.agent_id):
            raise ValueError("local node is not a current project member")
        manifest = saved["manifest"]
        if manifest.get("participant_set_hash") and manifest["participant_set_hash"] != participant_set_hash(manifest):
            raise ValueError("project membership participant set hash mismatch")
        return manifest

    def _project_manifest_for_event(self, project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        epoch = int(event.get("membership_epoch") or 0)
        proof_manifest = ((event.get("membership_proof") or {}).get("manifest") or {})
        if int(proof_manifest.get("epoch") or 0) != epoch:
            raise ValueError("project board event membership proof epoch mismatch")
        historical = self.store.project_manifest_at(project_id, epoch)
        if historical:
            if historical["manifest"] != proof_manifest or historical["signature"] != str((event.get("membership_proof") or {}).get("signature") or ""):
                raise ValueError("project board event conflicts with stored membership history")
            return historical["manifest"]
        owner = self._project_owner(project_id)
        if not owner or proof_manifest.get("project_id") != project_id or proof_manifest.get("owner_id") != owner.agent_id:
            raise ValueError("project board event has an unknown membership proof")
        if proof_manifest.get("participant_set_hash") != participant_set_hash(proof_manifest):
            raise ValueError("project board event membership participant hash mismatch")
        if not verify_signature(owner.signing_key, str((event.get("membership_proof") or {}).get("signature") or ""), canonical(proof_manifest)):
            raise ValueError("project board event membership proof signature is invalid")
        self.store.save_project_manifest(project_id, epoch, proof_manifest, str((event.get("membership_proof") or {}).get("signature") or ""))
        return proof_manifest

    def project_board(self, project_id: str, after_clock: int = 0, limit: int = 1000) -> Dict[str, Any]:
        manifest = self._active_project_manifest(project_id)
        if not self.store.project_member(project_id, self.identity.agent_id):
            raise ValueError("local node is not a current project member")
        events = self.store.project_board_events(project_id, after_clock, limit)
        rendered_events = []
        for event in events:
            rendered = dict(event)
            key_record = self.store.project_content_key(project_id, int(event.get("membership_epoch") or 0))
            rendered["content"] = decrypt_board_event_content(event, b64d(key_record["key_b64"]) if key_record else None)
            rendered_events.append(rendered)
        return {
            "project_id": project_id,
            "board_id": f"board:{project_id}",
            "membership_epoch": int(manifest["epoch"]),
            "participant_set_hash": participant_set_hash(manifest),
            "event_count": self.store.project_board_event_count(project_id),
            "event_set_root": event_set_root(self.store.project_board_events(project_id, 0, 5000)),
            "event_buckets": event_buckets(self.store.project_board_events(project_id, 0, 5000)),
            "events": rendered_events,
        }

    def _project_board_summary(self, project_id: str) -> Dict[str, Any]:
        self._active_project_manifest(project_id)
        events = self.store.project_board_events(project_id, 0, 5000)
        return {
            "project_id": project_id,
            "board_id": f"board:{project_id}",
            "event_count": len(events),
            "event_set_root": event_set_root(events),
            "event_buckets": event_buckets(events),
        }

    @staticmethod
    def _summary_bucket_map(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        values = summary.get("event_buckets") or []
        return {str(value.get("bucket")): value for value in values if isinstance(value, dict) and value.get("bucket") is not None}

    def _project_board_events_for_buckets(self, project_id: str, buckets: set[str]) -> list[Dict[str, Any]]:
        return [
            event for event in self.store.project_board_events(project_id, 0, 5000)
            if event_bucket(event["event_id"]) in buckets
        ]

    def _send_board_sync_exchange(self, peer: PublicIdentity, endpoint: str, project_id: str, summary: Dict[str, Any], gossip_id: str, hop_count: int, max_hops: int) -> Dict[str, Any]:
        if hop_count < 0 or max_hops < 1 or hop_count > max_hops:
            raise ValueError("project board gossip hop limit exceeded")
        envelope = encrypt_message(
            self.identity,
            peer,
            self.store.next_sequence(peer.agent_id),
            "project.board.sync.request",
            {"summary": summary, "gossip_id": gossip_id, "hop_count": hop_count, "max_hops": max_hops},
            ttl_seconds=86400,
            project_id=project_id,
        )
        direct = self._direct_info_for(project_id, peer.agent_id)
        result = self._direct_request(endpoint, {"scope": "board", "envelope": envelope}, direct.get("tls_fingerprint", ""))
        response_envelope = result.get("board_sync_response") if isinstance(result, dict) else None
        if not isinstance(response_envelope, dict):
            raise ValueError("board sync response is missing")
        response = decrypt_message(self.identity, response_envelope, peer)
        if response.get("type") != "project.board.sync.response":
            raise ValueError("unexpected board sync response type")
        payload = response.get("payload") or {}
        if payload.get("project_id") != project_id:
            raise ValueError("board sync response project mismatch")
        if payload.get("gossip_id") != gossip_id or int(payload.get("hop_count", -1)) > max_hops:
            raise ValueError("board sync response gossip scope mismatch")
        return payload

    def sync_project_board_merkle(self, project_id: str, max_rounds: int = 4) -> Dict[str, Any]:
        self._active_project_manifest(project_id)
        totals = []
        current_summary = self._project_board_summary(project_id)
        gossip_id = str(uuid.uuid4())
        max_hops = max(1, min(int(max_rounds), 8))
        for member in self.project_gossip_peers(project_id):
            agent_id = member["agent_id"]
            if agent_id == self.identity.agent_id:
                continue
            try:
                peer = self.trusted_peer(agent_id, project_id)
                endpoint = str(member.get("direct_endpoint") or self._direct_info_for(project_id, agent_id).get("endpoint") or "")
                if not peer or not endpoint:
                    raise ValueError("project member direct endpoint is unavailable")
                received_ids: list[str] = []
                sent_buckets: set[str] = set()
                for round_index in range(max_hops):
                    payload = self._send_board_sync_exchange(peer, endpoint, project_id, current_summary, gossip_id, round_index, max_hops)
                    incoming = payload.get("events") or []
                    applied = self.apply_project_board_events(project_id, incoming)
                    received_ids.extend(applied.get("accepted_event_ids") or [])
                    peer_summary = payload.get("summary") or {}
                    current_summary = self._project_board_summary(project_id)
                    local_buckets = self._summary_bucket_map(current_summary)
                    peer_buckets = self._summary_bucket_map(peer_summary)
                    differing = {bucket for bucket in set(local_buckets) | set(peer_buckets) if local_buckets.get(bucket, {}).get("root") != peer_buckets.get(bucket, {}).get("root")}
                    sent_buckets.update(differing)
                    if current_summary.get("event_set_root") == peer_summary.get("event_set_root"):
                        break
                self.store.record_project_gossip_result(project_id, agent_id, True)
                totals.append({"recipient_id": agent_id, "status": "delivered", "gossip_id": gossip_id, "max_hops": max_hops, "rounds": round_index + 1, "received_event_ids": received_ids, "differing_buckets": sorted(sent_buckets)})
            except Exception as exc:
                self.store.record_project_gossip_result(project_id, agent_id, False)
                totals.append({"recipient_id": agent_id, "status": "failed", "error": str(exc)})
        return {
            "project_id": project_id,
            "transport": "direct-merkle-range",
            "event_set_root": self._project_board_summary(project_id)["event_set_root"],
            "event_count": sum(len(item.get("received_event_ids", [])) for item in totals),
            "deliveries": totals,
        }

    def apply_project_board_events(self, project_id: str, events: Any) -> Dict[str, Any]:
        self._active_project_manifest(project_id)
        if not isinstance(events, list):
            raise ValueError("project board sync requires an event list")
        accepted = []
        duplicates = []
        for event in events[:5000]:
            manifest = self._project_manifest_for_event(project_id, event)
            key_record = self.store.project_content_key(project_id, int(event.get("membership_epoch") or 0))
            verify_board_event(event, project_id, manifest, b64d(key_record["key_b64"]) if key_record else None)
            if self.store.save_project_board_event(event):
                accepted.append(event["event_id"])
            else:
                duplicates.append(event["event_id"])
        if accepted:
            self.store.add_trace("project.board.events_accepted", self.identity.agent_id, {"project_id": project_id, "event_ids": accepted})
        return {"project_id": project_id, "accepted_event_ids": accepted, "duplicate_event_ids": duplicates}

    def sync_project_board(self, project_id: str, events: Optional[list[Dict[str, Any]]] = None, exclude_agent_ids: Optional[set[str]] = None, force: bool = False) -> Dict[str, Any]:
        self._active_project_manifest(project_id)
        events = list(events if events is not None else self.store.project_board_events(project_id, 0, 5000))
        excluded = set(exclude_agent_ids or set()) | {self.identity.agent_id}
        deliveries = []
        for member in self.project_gossip_peers(project_id, force=force):
            agent_id = member["agent_id"]
            if agent_id in excluded:
                continue
            try:
                peer = self.trusted_peer(agent_id, project_id)
                endpoint = str(member.get("direct_endpoint") or self._direct_info_for(project_id, agent_id).get("endpoint") or "")
                if not peer or not endpoint:
                    raise ValueError("project member direct endpoint is unavailable")
                known = set() if force else self.store.project_board_sent_event_ids(project_id, agent_id)
                pending = [event for event in events if event["event_id"] not in known]
                if not pending:
                    continue
                delivery = self._send_direct_to_peer(peer, endpoint, "project.board.events", {"events": pending}, project_id=project_id, scope="board", trace_prefix="project.board")
                if delivery.get("status") in {"delivered", "accepted", "duplicate"}:
                    self.store.record_project_board_sent_events(project_id, agent_id, [event["event_id"] for event in pending])
                    self.store.record_project_gossip_result(project_id, agent_id, True)
                delivery["event_count"] = len(pending)
                deliveries.append(delivery)
            except Exception as exc:
                self.store.record_project_gossip_result(project_id, agent_id, False)
                deliveries.append({"recipient_id": agent_id, "status": "failed", "error": str(exc)})
        return {"project_id": project_id, "event_count": sum(int(item.get("event_count", 0)) for item in deliveries), "available_event_count": len(events), "force": force, "deliveries": deliveries}

    def post_project_board_event(self, project_id: str, event_type: str, content: Any, parent_event_id: str = "", content_type: str = "application/json") -> Dict[str, Any]:
        manifest = self._active_project_manifest(project_id)
        saved_manifest = self.store.project_manifest_at(project_id, int(manifest["epoch"])) or self.store.project_manifest(project_id)
        key_record = self.store.project_content_key(project_id, int(manifest["epoch"]))
        if not key_record:
            raise ValueError("project content key is unavailable for the active epoch")
        event = create_board_event(
            self.identity,
            project_id,
            manifest,
            self.store.next_project_board_clock(project_id),
            event_type,
            content,
            parent_event_id,
            content_type,
            str((saved_manifest or {}).get("signature") or ""),
            b64d(key_record["key_b64"]),
        )
        self.store.save_project_board_event(event)
        self.store.add_trace("project.board.event_created", self.identity.agent_id, {"project_id": project_id, "event_id": event["event_id"], "event_type": event_type})
        sync = self.sync_project_board(project_id, [event])
        return {"event": event, "sync": sync}

    def sync_all_project_boards(self) -> list[Dict[str, Any]]:
        output = []
        for project in self.store.projects():
            if project.get("status") == "active":
                try:
                    output.append(self.sync_project_board_merkle(project["project_id"]))
                except Exception as exc:
                    output.append({"project_id": project["project_id"], "error": str(exc)})
        return output

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
                        peer = self.trusted_peer(agent_id, project_id)
                        direct = self._direct_info_for(project_id, agent_id)
                        if peer and direct.get("endpoint"):
                            self._send_direct_to_peer(peer, direct["endpoint"], "project.lifecycle.close", {"project_id": project_id}, project_id=project_id, scope="project", trace_prefix="project.direct")
                        else:
                            # Compatibility for pre-direct nodes that only expose
                            # the legacy Relay lifecycle channel. User project
                            # chat/private messages never use this path.
                            self.send(agent_id, "project.lifecycle.close", {"project_id": project_id}, project_id=project_id)
                            self.store.add_trace("project.lifecycle.close_legacy_relay", self.identity.agent_id, {"project_id": project_id, "recipient_id": agent_id})
                        notified.append(agent_id)
                    except Exception as exc:
                        self.store.add_trace("project.direct.delivery_failed", self.identity.agent_id, {"project_id": project_id, "recipient_id": agent_id, "error": str(exc), "reason": "project close notification"})
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

    def receive_direct(self, body: Dict[str, Any]) -> Dict[str, Any]:
        envelope = body.get("envelope") if isinstance(body, dict) else None
        scope = str(body.get("scope") or "") if isinstance(body, dict) else ""
        if not isinstance(envelope, dict):
            raise ValueError("direct message requires an envelope")
        if self.store.has_received(envelope.get("message_id", "")):
            return {"status": "duplicate", "message_id": envelope.get("message_id")}
        project_id = str(envelope.get("project_id") or "")
        if scope == "private":
            if project_id:
                raise ValueError("private direct messages cannot include project_id")
            sender_id = str(envelope.get("sender_id") or "")
            sender = self.trusted_peer(sender_id)
            if not self._private_trust_ready(sender_id):
                sender = None
            event_prefix = "private.direct"
        elif scope in {"project", "board"}:
            if not project_id or not self.store.project_active(project_id):
                raise ValueError("project is closed or unknown")
            sender = self.trusted_peer(envelope.get("sender_id", ""), project_id)
            event_prefix = "project.board" if scope == "board" else "project.direct"
        elif scope == "membership":
            if not project_id or not self.store.project_active(project_id):
                raise ValueError("project is closed or unknown")
            sender = self._project_owner(project_id)
            if not sender or envelope.get("sender_id") != sender.agent_id:
                self.store.add_trace("direct.identity_rejected", self.identity.agent_id, {"project_id": project_id, "sender_id": envelope.get("sender_id")})
                raise ValueError("membership messages must be signed by the project owner")
            event_prefix = "project.membership"
        else:
            raise ValueError("direct scope must be private, project, board or membership")
        if not sender:
            self.store.add_trace("direct.identity_rejected", self.identity.agent_id, {"project_id": project_id or None, "sender_id": envelope.get("sender_id"), "scope": scope})
            raise ValueError("sender is not trusted for this direct scope")
        try:
            message = decrypt_message(self.identity, envelope, sender)
            if self.store.save_received(envelope, message):
                if scope == "private":
                    self.store.save_private_message(
                        envelope["message_id"],
                        envelope["sender_id"],
                        "incoming",
                        message.get("type", "message"),
                        message.get("payload"),
                        int(envelope.get("created_at") or time.time()),
                        "delivered",
                    )
                self.store.add_trace(f"{event_prefix}.received", envelope["sender_id"], {"message_id": envelope["message_id"], "recipient_id": self.identity.agent_id, "project_id": project_id or None, "transport": "direct"})
            if scope == "membership" and message.get("type") == "project.membership.manifest":
                result = self.apply_project_manifest(message.get("payload") or {})
                return {"status": "accepted", "message_id": envelope["message_id"], "membership": result}
            if scope == "board" and message.get("type") == "project.board.events":
                result = self.apply_project_board_events(project_id, (message.get("payload") or {}).get("events"))
                return {"status": "accepted", "message_id": envelope["message_id"], "board": result}
            if scope == "board" and message.get("type") == "project.board.sync.request":
                request_payload = message.get("payload") or {}
                gossip_id = str(request_payload.get("gossip_id") or "")
                hop_count = int(request_payload.get("hop_count", -1))
                max_hops = int(request_payload.get("max_hops", 0))
                if not gossip_id or hop_count < 0 or max_hops < 1 or hop_count > max_hops:
                    raise ValueError("invalid project board gossip TTL")
                requester_summary = request_payload.get("summary") or {}
                local_summary = self._project_board_summary(project_id)
                requester_buckets = self._summary_bucket_map(requester_summary)
                local_buckets = self._summary_bucket_map(local_summary)
                differing = {bucket for bucket in set(requester_buckets) | set(local_buckets) if requester_buckets.get(bucket, {}).get("root") != local_buckets.get(bucket, {}).get("root")}
                response_events = self._project_board_events_for_buckets(project_id, differing)
                response = encrypt_message(
                    self.identity,
                    sender,
                    self.store.next_sequence(sender.agent_id),
                    "project.board.sync.response",
                    {"project_id": project_id, "summary": local_summary, "events": response_events, "gossip_id": gossip_id, "hop_count": hop_count + 1, "max_hops": max_hops},
                    ttl_seconds=86400,
                    project_id=project_id,
                )
                return {"status": "accepted", "message_id": envelope["message_id"], "board_sync_response": response, "differing_buckets": sorted(differing)}
            return {"status": "delivered", "message_id": envelope["message_id"], "type": message.get("type")}
        except Exception as exc:
            self.store.add_trace(f"{event_prefix}.delivery_failed", self.identity.agent_id, {"message_id": envelope.get("message_id"), "sender_id": envelope.get("sender_id"), "project_id": project_id or None, "error": str(exc)})
            raise

    def receive(self, auto_ack: bool = True):
        # Control-plane notifications are valid before ordinary peer trust or
        # project membership exists. Consume them first so a trusted sender's
        # control message is not mistaken for an application message.
        self.poll_peer_trust_notifications()
        self.poll_project_invitation_notifications()
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
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
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
                if parsed.path == "/v1/peers/trust-notifications":
                    self.send_json(200, sidecar.peer_trust_notifications())
                    return
                if parsed.path == "/v1/project-invitations":
                    self.send_json(200, sidecar.project_invitation_notifications())
                    return
                if parsed.path == "/v1/harness/config":
                    self.send_json(200, sidecar.harness_config())
                    return
                if parsed.path == "/v1/projects":
                    projects = sidecar.store.projects()
                    for project in projects:
                        project["project_members"] = sidecar.store.project_members(project["project_id"])
                        project["auto_trust_members"] = sidecar.store.project_auto_trust(project["project_id"])
                        project["gossip_fanout"] = sidecar.store.project_gossip_fanout(project["project_id"])
                    self.send_json(200, {"projects": projects})
                    return
                if parsed.path.startswith("/v1/projects/"):
                    project_path = parsed.path.removeprefix("/v1/projects/")
                    project_id, _, suffix = project_path.partition("/")
                    if suffix == "candidates":
                        self.send_json(200, {"peers": sidecar.peer_candidates(project_id)})
                        return
                    if suffix == "membership":
                        self.send_json(200, sidecar.project_manifest(project_id))
                        return
                    if suffix == "membership/history":
                        self.send_json(200, {"project_id": project_id, "history": sidecar.store.project_manifest_history(project_id)})
                        return
                    if suffix == "board":
                        query = parse_qs(parsed.query)
                        after_clock = int((query.get("after_clock") or [0])[0])
                        limit = int((query.get("limit") or [1000])[0])
                        self.send_json(200, sidecar.project_board(project_id, after_clock, limit))
                        return
                    if suffix == "gossip-peers":
                        query = parse_qs(parsed.query)
                        probe = str((query.get("probe") or [""])[0]).lower() in {"1", "true", "yes"}
                        self.send_json(200, {"project_id": project_id, "probe": probe, "peers": sidecar.project_gossip_peer_scores(project_id, probe=probe)})
                        return
                    project = sidecar.store.project(project_id)
                    if project:
                        project["project_members"] = sidecar.store.project_members(project_id)
                        project["auto_trust_members"] = sidecar.store.project_auto_trust(project_id)
                        project["gossip_fanout"] = sidecar.store.project_gossip_fanout(project_id)
                    self.send_json(200, project) if project else self.send_json(404, {"error": "project not found"})
                    return
                if parsed.path == "/v1/private-messages":
                    query = parse_qs(parsed.query)
                    peer_id = str((query.get("peer_id") or [""])[0])
                    limit = int((query.get("limit") or [200])[0])
                    self.send_json(200, {"messages": sidecar.store.private_messages(peer_id, limit)})
                    return
                if parsed.path == "/v1/direct/healthz":
                    self.send_json(200, {"status": "ok", "agent_id": sidecar.identity.agent_id, "direct": sidecar.capabilities.get("direct", {})})
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
                if parsed.path == "/v1/skills/catalog":
                    self.send_json(200, sidecar.wechat_skill_catalog())
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
                if parsed.path == "/v1/direct/messages":
                    self.send_json(202, sidecar.receive_direct(body))
                    return
                if parsed.path == "/v1/harness/config/test":
                    self.send_json(200, sidecar.test_harness_config(body.get("config", {})))
                    return
                if parsed.path == "/v1/harness/config":
                    self.send_json(200, sidecar.configure_harness(body.get("config", {})))
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
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/auto-trust"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/auto-trust").rstrip("/")
                    self.send_json(200, sidecar.enable_project_auto_trust(project_id, bool(body.get("enabled", True))))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/gossip-settings"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/gossip-settings").rstrip("/")
                    self.send_json(200, sidecar.set_project_gossip_fanout(project_id, int(body.get("gossip_fanout", 2))))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/members/remove"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/members/remove").rstrip("/")
                    self.send_json(200, sidecar.remove_project_member(project_id, body["agent_id"]))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/membership"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/membership").rstrip("/")
                    if body.get("manifest"):
                        self.send_json(200, sidecar.apply_project_manifest(body))
                    else:
                        self.send_json(200, sidecar.project_manifest(project_id))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/board/sync"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/board/sync").rstrip("/")
                    self.send_json(200, sidecar.sync_project_board(project_id, force=True))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/board/sync/merkle"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/board/sync/merkle").rstrip("/")
                    self.send_json(200, sidecar.sync_project_board_merkle(project_id))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/board"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/board").rstrip("/")
                    self.send_json(201, sidecar.post_project_board_event(project_id, str(body.get("event_type") or "post"), body.get("content"), str(body.get("parent_event_id") or ""), str(body.get("content_type") or "application/json")))
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
                if parsed.path == "/v1/skills/catalog/install":
                    self.send_json(201, sidecar.install_wechat_skills(body.get("skill_ids")))
                    return
                if parsed.path == "/v1/skills":
                    sidecar.install_skill(body)
                    self.send_json(201, {"skill_id": body["skill_id"], "enabled": True})
                    return
                if parsed.path == "/v1/peers/trust":
                    peer = sidecar.trust(body["agent_id"], body.get("expected_signing_key"))
                    self.send_json(200, peer.as_dict())
                    return
                if parsed.path == "/v1/peers/trust-requests":
                    self.send_json(201, sidecar.request_peer_trust(body["agent_id"], body.get("expected_signing_key")))
                    return
                if parsed.path == "/v1/peers/trust-response":
                    self.send_json(200, sidecar.respond_peer_trust(body["agent_id"], body.get("decision", "accept")))
                    return
                if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/invitations"):
                    project_id = parsed.path.removeprefix("/v1/projects/").removesuffix("/invitations").rstrip("/")
                    self.send_json(201, sidecar.send_project_invitation(project_id, body["agent_id"], int(body.get("ttl_seconds", 7 * 86400))))
                    return
                if parsed.path.startswith("/v1/project-invitations/") and parsed.path.endswith("/response"):
                    invitation_id = parsed.path.removeprefix("/v1/project-invitations/").removesuffix("/response").rstrip("/")
                    self.send_json(200, sidecar.respond_project_invitation(invitation_id, body.get("decision", "accept")))
                    return
                if parsed.path == "/v1/messages/send":
                    if body.get("target"):
                        self.send_json(201, sidecar.send_routed(body["target"], body.get("message", {}), int(body.get("ttl_seconds", 86400))))
                    else:
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
                agent_id = unquote(urlparse(self.path).path[len(prefix):])
                result = sidecar.revoke_peer_trust(agent_id)
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

    return SidecarHandler


def certificate_fingerprint(certfile: Optional[Path]) -> str:
    if not certfile:
        return ""
    pem = certfile.read_text("utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def serve_sidecar(host: str, port: int, data_dir: Path, relay_url: str, tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None, relay_token: str = "", direct_endpoint: str = "", project_key_backend: str = "sqlite", project_key_command: Optional[list[str]] = None) -> None:
    scheme = "https" if tls_cert else "http"
    advertised_endpoint = str(direct_endpoint or "").rstrip("/")
    if not advertised_endpoint and host not in {"0.0.0.0", "::"}:
        advertised_endpoint = f"{scheme}://{host}:{port}"
    sidecar = Sidecar(data_dir, relay_url, relay_token, advertised_endpoint, certificate_fingerprint(tls_cert), project_key_backend, project_key_command)
    server = ThreadingHTTPServer((host, port), make_handler(sidecar))
    enable_tls(server, tls_cert, tls_key)
    print(f"Sidecar {sidecar.identity.agent_id} listening on {scheme}://{host}:{port}")
    print(f"Trust UI: {scheme}://{host}:{port}/ui/trust")
    stop_heartbeat = threading.Event()

    def capability_heartbeat() -> None:
        while not stop_heartbeat.wait(300):
            try:
                sidecar.refresh_capabilities()
                sidecar.sync_all_project_boards()
            except Exception as exc:
                sidecar.store.add_trace("node.capabilities.heartbeat_failed", sidecar.identity.agent_id, {"error": str(exc)})

    heartbeat = threading.Thread(target=capability_heartbeat, name="sidecar-capability-heartbeat", daemon=True)
    heartbeat.start()
    try:
        server.serve_forever()
    finally:
        stop_heartbeat.set()
        server.server_close()
