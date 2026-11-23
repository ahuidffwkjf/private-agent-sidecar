# 常驻 Tailcat 网络管理

本功能在 Sidecar 内管理 Tailcat v0.7.0 子进程，不修改其加密或穿透协议。需显式配置启用，原 HTTP/HTTPS 运行方式保持兼容。

## 已实现的边界

- 独立常驻 `run_network_node.py` 可由 OS 服务管理；桌面“连接设置”连接该本机服务，关闭窗口不会关闭这个独立服务。
- 固定 Tailcat 服务密钥；每个目标节点另配一份持久客户端密钥，避免多个 `serve`/`forward` 进程复用隧道身份。每个节点一个 `serve`，没有授权节点时明确使用 `--allow=none`，不使用空白名单。
- 入站端口只暴露 `/v1/direct/healthz` 与 `/v1/direct/messages`，不转发 UI、信任、Skills、网络配置或其他管理 API。
- 签名路由包含节点 ID、接收节点 `audience`、本节点用于连接该接收节点的客户端公钥、版本、有效期、Tailcat 地址、端口和 TLS 指纹；只接受发给本机且来自已双向信任节点的路由，版本水位持久保存，拒绝回滚和同版本内容变更。
- 按需连接池默认 8、最大 32，空闲 120 秒回收；进程退出或连接失败后指数退避加随机抖动，最长进程退避 60 秒。监管与消息重试分线程，单个连接阻塞不暂停白名单检查。
- 达到连接池上限时回收最久未使用且没有在途请求的连接；新转发进程至少保留 60 秒且连续失败三次才因链路错误重启，避免启动竞态。脱敏状态区分最近成功时间、传输错误类型和进程退出，不把进程存活当链路在线。
- Tailcat 由独立轻量 worker 托管，worker 监听监管者管道 EOF；监管者崩溃后关闭其子 Tailcat。服务管理器负责重启整个节点，系统锁防止两个 NetworkManager 同时使用同一节点目录。
- 每 2 秒复核信任和路由有效性。撤权后停止对应出站进程，并重启入站 `serve` 关闭已有会话、应用新白名单。Sidecar 接收每个消息时仍即时检查当前信任。
- 私聊/智能体消息先写持久化发件箱与本地历史（同一 SQLite 事务）。网络中只发送原加密信封，重试不生成新 ID；接收端将私聊历史和去重记录在同一事务中持久化后 ACK。
- TCP/TLS 在每次投递重建；显式证书 SHA-256 pin 在写出 HTTP 正文之前检查。ACK 必须匹配消息 ID，不接受仅有 200 的任意响应。
- TTL 到期变为 `expired`，撤权变为 `cancelled`，证书/协议/接收方拒绝变为 `blocked`。不因失败回退到业务 Relay。
- 发件箱最多 1000 个待投递项，每项信封不超过 1 MiB；总保留上限 10000 条。终态且超过有效期七天的记录在下次入队时清理，本地聊天历史不随之删除。
- Relay 不可达时管理模式允许本机启动；已导入且仍有效的路由不依赖 Relay 查询。每 60 秒尝试恢复能力广播和项目留言板同步。

`queued` 只表示本机已持久化，不是对方收到。重试实现至少一次传输、私聊按消息 ID 去重，不承诺任意 Harness 任务“恰好执行一次”。任务进度与模型进程恢复需要各 Harness 自己实现。

## 部署

1. 为每个节点建立独立系统账户/私有目录，POSIX 目录 0700、配置/密钥/地址/证书文件 0600。Windows 需由管理员设置仅运行账户可读的 ACL。
2. 单独安装并核验 Tailcat v0.7.0。首次生成固定服务密钥：`tailcat genkey --key=/PRIVATE/tailcat.private.json --fixed-region`。原始输出包含连接材料，请在私密终端处理，不复制进日志/仓库。后续启动复用，不使用 `--force`、`--key=new`。
   每个目标节点另生成客户端密钥，例如 `tailcat genkey --key=/PRIVATE/client-b.private.json --client`，配置 `client_keys` 为 `{"agent:目标节点的32位ID":"/PRIVATE/client-b.private.json"}`。不复制同一密钥给不同进程。添加目标配置后重启后台服务，已有身份、消息队列不变。
3. 为通信端口配置 TLS 证书和私钥。双方通过独立渠道核对 Sidecar 公钥；TLS pin 放在对方签名的路由中。Relay 仍独立使用 CA/主机名校验，不使用 `curl -k`。
4. 按 `deploy/tailcat/network.example.json` 建立私有配置。`peer_port` 与本机管理端口必须不同。仅数字端口，不启用 Tailcat `all`、文件、SSH 或 exit-node。
5. 运行：

```sh
.venv/bin/python scripts/run_network_node.py \
  --data-dir /PRIVATE/node --network-config /PRIVATE/network.json \
  --port 8802 --relay https://127.0.0.1:18787 \
  --relay-token-file /PRIVATE/relay-token
```

Relay 的 Tailcat 转发仍用原有部署流程；按实际证书设置 `SSL_CERT_FILE` 和 `NO_PROXY`。运行账户能访问本机管理 API 是本项目现有权限边界，此 API 不对远端开放。

Linux 使用 `sidecar-network.service` 模板，Mac 使用 launchd 模板；必须按实际路径/账户调整和检查后再安装。本任务不会修改系统服务或启动项。Windows 可在任务计划程序配置上述 Python 命令、登录/开机触发和失败重启；Windows 常驻运行尚需实机验证，当前不提供伪装成原生 Windows Service 的 Python 脚本。

## 建立和更新路由

先经 Relay 完成两个节点的双向信任，再在各自本机管理接口：

```text
POST /v1/network/routes/export  {"version":1,"recipient_id":"agent:对方节点ID","ttl_seconds":86400}
POST /v1/network/routes/import  <对方 export 返回的整个签名 JSON>
GET  /v1/network/status
```

导出需要 Tailcat 已生成地址文件；导出版本必须严格递增，接收端也有持久水位。返回的路由包含敏感连接材料，只通过获授权的保密渠道传送，不能公开广播。导入不授予信任。目录上限 128 个节点，路由有效期最多 30 天。续期、TLS 换证或地址变更均需更高版本并重新分发。

当前路由交换是显式导出/导入，尚未自动化续期或分发，不得声称已经完成无人值守多月运行。没有有效路由时消息留队等待，直到恢复或 TTL 到期。`blocked` 不盲目自动重试，需管理员确认原因并由调用方决定是否重新发送。

项目同步可复用已配置 Tailcat 路由，但本版本路由授权要求全局双向信任；只有项目临时信任的成员仍需原有可达 HTTPS 端点。不得为了获得路由自动提升项目成员为全局信任。

## 验收与剩余工作

- `tests/test_network_manager.py`：路由签名/回滚/过期、持久队列、单实例、退避、撤权、TLS pin、去重、事务回滚和 TLS 通信端口隔离。
- `scripts/run_tailcat_recovery_demo.py`：三个隔离节点，真实 Tailcat 子进程、TLS 消息、杀死服务重启、丢 ACK 重试、不回退业务 Relay；使用临时新密钥，不使用真实用户数据。
- 本机三节点演示不证明真实跨机器 NAT 穿透；Tailcat 可能使用 DERP，除非有独立路径测量，否则不声称 UDP 直连。
- 尚待：自动保密路由分发/续期、备用 DERP 的可验证切换、任务 checkpoint 恢复、Windows 服务验收、多机网络切换/睡眠唤醒/长期内存和队列压力测试。
- 本轮不 fork Tailcat Go 源码，静态白名单通过进程重启更新；撤权会短暂影响所有入站连接，消息由上层队列或留言板恢复。

## 本轮交付记录（2026-11-15）

文件：新增 `agent_sidecar/network.py`、`agent_sidecar/tailcat_worker.py`、`scripts/run_network_node.py`、`scripts/run_tailcat_recovery_demo.py`、`tests/test_network_manager.py` 和三个部署模板；更新 `service.py`、`store.py`、`cli.py`、聊天状态显示、浏览器验收脚本、源码分发清单与 README。已有其他工作区改动保持不变，没有安装系统服务或替换用户密钥。

验证命令与结果：

- 修改前 Tailcat 白名单、转发映射、秘密文件校验 3 项基线测试通过；初次网络测试权限审核超时未执行，后续已获权限执行。
- `.venv/bin/python -m unittest discover -s tests -v`：**156 项全部通过**；日志 `/tmp/sidecar-network-20261115-final.log`。
- `scripts/run_tailcat_recovery_demo.py --output runs/tailcat-recovery-20261115-supervised`：**真实 Tailcat v0.7.0 三节点六方向 TLS 投递、杀死服务后恢复、丢 ACK 重投去重、不回退业务 Relay 全部通过**。结果保存在该目录 `summary.json`。前几次失败结果也保留，曾发现转发启动宽限、身份复用与监管时机问题，不能仅用早期成功结果替代最终验收。
- `scripts/check_chat_ui.mjs`：10 组浏览器验收通过，包括排队不显示为已送达、私聊失败保留草稿、会话隔离及桌面/320px/390px 布局；结果 `runs/network-manager-ui-20261115/summary.json`。
- `scripts/run_federated_demo.py`、`scripts/run_federated_process_demo.py` 通过，未运行真实 DeepSeek 训练。
- 小型 Benchmark：`runs/network-manager-benchmark-20261114/`，已保留配置、结果、逐轮数据和摘要。3 节点、3 轮、Secure FedAvg、Gossip、无攻击，Toy accuracy 0.995833、loss 0.096966，18 条模型估计网络消息、3405 bytes。它是旧聚合逻辑回归，不是隧道性能测量或正式安全证明。
- Python 编译、JavaScript 语法检查和 `git diff --check` 通过。测试服务和临时密钥均已清理。

外部阻塞：实际部署还需各机器的持久密钥、TLS 证书、明确互信以及可用 DERP/网络路径；未进行跨机器长期压测，未验收 Windows 服务。桌面安装包未重新发行，此功能通过源码常驻入口显式启用。

下一项唯一最高优先任务：实现针对已授权节点的**加密路由自动分发与续期**，使节点长期运行不依赖手动交换到期路由；之后再验收备用中继和多机长时间网络变化。当前只能声明常驻监管和消息恢复基础已实现，不能声明无人值守生产网络全部完成。
