#!/usr/bin/env python3
"""Local visual dashboard for the API-only Private Agent Sidecar 0.3 service.

The proxy binds to loopback, serves a small browser UI, forwards only a fixed
allowlist of Sidecar API routes, and injects the Sidecar Bearer token upstream.
This keeps the token out of browser URLs and avoids changing a running node.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Type
from urllib.parse import urlsplit


ALLOWED_ROUTES = {
    ("GET", "/healthz"),
    ("GET", "/v1/identity"),
    ("GET", "/v1/peers"),
    ("POST", "/v1/peers/trust"),
    ("POST", "/v1/messages/send"),
    ("GET", "/v1/messages/sync"),
    ("GET", "/v1/tasks"),
    ("GET", "/v1/tasks/next"),
}

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sidecar 0.3 可视化控制台</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1080px;margin:24px auto;padding:0 16px;background:#f5f7fb;color:#172033}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:780px){.grid{grid-template-columns:1fr}}.card{background:#fff;border:1px solid #dbe3ef;border-radius:12px;padding:18px;box-shadow:0 4px 18px #17203312;margin-bottom:16px}h1{margin-bottom:4px}h2{font-size:18px;margin-top:0}.muted{color:#64748b}.ok{color:#166534}.error{color:#b91c1c}input,textarea{box-sizing:border-box;width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:7px;margin:5px 0 10px;font:inherit}button{border:0;border-radius:7px;padding:9px 14px;background:#1769aa;color:#fff;cursor:pointer;margin-right:6px}pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;padding:10px;border-radius:7px;max-height:340px;overflow:auto}.item{border-top:1px solid #e6ebf2;padding:10px 0}.mono{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}
</style>
<h1>Sidecar 0.3 可视化控制台</h1>
<p class="muted">本页面仅在本机回环地址运行；Bearer Token 由本地代理注入，不进入浏览器 URL。</p>
<div class="grid">
<section class="card"><h2>节点状态</h2><button onclick="loadIdentity()">刷新身份</button><button onclick="loadPeers()">刷新信任列表</button><div id="health" class="muted"></div><pre id="identity">尚未读取</pre><div id="peers"></div></section>
<section class="card"><h2>建立节点信任</h2><p class="muted">从对方节点的 <code>/v1/identity</code> 复制 agent_id 和 signing_key，并通过独立渠道核验。</p><label>Agent ID</label><input id="trust_agent" placeholder="agent:..."><label>Signing Key</label><textarea id="trust_key" rows="4" placeholder="对方 signing_key"></textarea><button onclick="trustPeer()">信任该节点</button><p id="trust_status" class="muted"></p></section>
<section class="card"><h2>发送加密消息</h2><label>收件 Agent ID</label><input id="recipient" placeholder="agent:..."><label>消息类型</label><input id="message_type" value="chat.message"><label>Payload JSON</label><textarea id="payload" rows="7">{"text":"来自可视化控制台的测试消息"}</textarea><button onclick="sendMessage()">发送</button><p id="send_status" class="muted"></p></section>
<section class="card"><h2>消息与任务</h2><button onclick="syncMessages()">同步消息</button><button onclick="loadTasks()">刷新任务</button><pre id="inbox">尚未同步</pre></section>
</div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options){const r=await fetch('/api'+path,options);const text=await r.text();let value;try{value=JSON.parse(text)}catch{value={raw:text}}if(!r.ok)throw new Error(`${r.status}: ${JSON.stringify(value)}`);return value}
async function initialize(){try{const h=await api('/healthz');document.querySelector('#health').className='ok';document.querySelector('#health').textContent='连接正常：'+JSON.stringify(h);await Promise.all([loadIdentity(),loadPeers()])}catch(e){document.querySelector('#health').className='error';document.querySelector('#health').textContent='连接失败：'+e.message}}
async function loadIdentity(){try{document.querySelector('#identity').textContent=JSON.stringify(await api('/v1/identity'),null,2)}catch(e){document.querySelector('#identity').textContent=e.message}}
async function loadPeers(){const box=document.querySelector('#peers');try{const d=await api('/v1/peers');const peers=d.peers||d;box.innerHTML=Array.isArray(peers)&&peers.length?peers.map(p=>`<div class="item"><strong>${esc(p.display_name||p.agent_id||'peer')}</strong><div class="mono">${esc(p.agent_id||'')}</div><div class="muted">${esc(p.signing_key||p.key_fingerprint||'')}</div></div>`).join(''):'<p class="muted">暂无已信任节点</p>'}catch(e){box.innerHTML=`<p class="error">${esc(e.message)}</p>`}}
async function trustPeer(){const status=document.querySelector('#trust_status');const agent_id=document.querySelector('#trust_agent').value.trim();const expected_signing_key=document.querySelector('#trust_key').value.trim();if(!agent_id){status.textContent='请输入 Agent ID';return}try{const d=await api('/v1/peers/trust',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id,expected_signing_key})});status.className='ok';status.textContent='信任已保存：'+JSON.stringify(d);await loadPeers()}catch(e){status.className='error';status.textContent='保存失败：'+e.message}}
async function sendMessage(){const status=document.querySelector('#send_status');let payload;try{payload=JSON.parse(document.querySelector('#payload').value)}catch(e){status.className='error';status.textContent='Payload JSON 错误：'+e.message;return}try{const d=await api('/v1/messages/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipient_id:document.querySelector('#recipient').value.trim(),type:document.querySelector('#message_type').value.trim(),payload})});status.className='ok';status.textContent='已提交：'+JSON.stringify(d)}catch(e){status.className='error';status.textContent='发送失败：'+e.message}}
async function syncMessages(){const box=document.querySelector('#inbox');try{box.textContent=JSON.stringify(await api('/v1/messages/sync'),null,2)}catch(e){box.textContent=e.message}}
async function loadTasks(){const box=document.querySelector('#inbox');try{box.textContent=JSON.stringify(await api('/v1/tasks'),null,2)}catch(e){box.textContent=e.message}}
initialize();
</script></html>"""


def make_handler(upstream: str, token: str) -> Type[BaseHTTPRequestHandler]:
    base = upstream.rstrip("/")

    class DashboardProxyHandler(BaseHTTPRequestHandler):
        server_version = "SidecarLegacyDashboard/1"

        def _send(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json_error(self, status: int, message: str) -> None:
            self._send(status, json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _proxy(self, method: str) -> None:
            parsed = urlsplit(self.path)
            if not parsed.path.startswith("/api/"):
                self._json_error(404, "not found")
                return
            upstream_path = parsed.path.removeprefix("/api")
            if (method, upstream_path) not in ALLOWED_ROUTES:
                self._json_error(404, "route is not exposed by the local dashboard proxy")
                return
            body = None
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json_error(400, "invalid content length")
                    return
                body = self.rfile.read(length)
            target = base + upstream_path + (("?" + parsed.query) if parsed.query else "")
            request = urllib.request.Request(
                target,
                data=body,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": self.headers.get("Content-Type", "application/json"),
                    "Accept": "application/json, text/plain, */*",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = response.read()
                    content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
                    self._send(response.status, data, content_type)
            except urllib.error.HTTPError as exc:
                self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json; charset=utf-8"))
            except Exception as exc:
                self._json_error(502, f"upstream request failed: {exc}")

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/":
                self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._proxy("GET")

        def do_POST(self) -> None:
            self._proxy("POST")

        def log_message(self, format: str, *args: object) -> None:
            # Avoid noisy browser polling logs. No authorization value is logged.
            return

    return DashboardProxyHandler


def _read_token(token_file: Path | None) -> str:
    if token_file is not None:
        token = token_file.read_text("utf-8").strip()
    else:
        token = os.environ.get("SIDECAR_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("set SIDECAR_API_TOKEN or pass --token-file")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description="Local visual dashboard for API-only Sidecar 0.3 nodes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--upstream", default="http://127.0.0.1:8801")
    parser.add_argument("--token-file", type=Path, default=None)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("the compatibility dashboard must bind to a loopback address")
    token = _read_token(args.token_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.upstream, token))
    print(f"Legacy Sidecar dashboard: http://{args.host}:{server.server_port}/")
    print(f"Proxying API to: {args.upstream}")
    server.serve_forever()


if __name__ == "__main__":
    main()
