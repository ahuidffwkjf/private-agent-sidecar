# Private Agent Sidecar

一个供多个 Harness/Agent 相互发送端到端加密消息的最小可运行原型。

每个 Sidecar 在本地生成独立的 Ed25519 身份密钥和 X25519 加密密钥。中继只保存带签名的加密信封，不能读取消息正文。接收方需要先显式信任发送方，才会解密其消息。

## 当前能力

- Ed25519 身份签名与自证明公钥目录
- 每条消息使用临时 X25519 密钥
- HKDF-SHA256 密钥派生
- ChaCha20-Poly1305 认证加密
- SQLite 离线邮箱和本地状态
- 签名认证的收件箱和 ACK
- TTL、去重、未来时间检查和篡改检测
- 仅使用 Python 标准库实现 HTTP，唯一运行依赖是 `cryptography`

## 安装

### 让 Agent 完成安装与接入

完整入口：[Agent 安装手册](docs/AGENT_INSTALL.md)；可加载的接入 Skill：
[`sidecar-onboarding`](skills/sidecar-onboarding/SKILL.md)。安装程序会准备独立环境、
生成设备公钥申请，并在管理员批准后启动 Tailcat 和 Sidecar。另附此前 361 个
类微信操作 Skill 的 `SKILL.md` 导出，合计 362 项；这些操作目录不是已经实现的
361 个执行器，也不授权 Agent 自动操作真实微信。

```bash
python3 scripts/sidecar_setup.py doctor
python3 scripts/sidecar_setup.py install
```

首次接入仍需可信管理员授权；无需手工复制私钥。已有源码和节点数据不会被删除。
发行包由 `scripts/package_sidecar_installer.py --output <新文件.zip>` 生成。

Tailcat v0.7.0 隧道接入与恢复的部署脚本见
[Tailcat 部署说明](docs/TAILCAT_DEPLOYMENT.md)。当前版本只映射 Relay HTTPS
端口，不沿用旧 0.3/0.4 产品的 WebSocket 参数；隧道不能替代 TLS 和节点信任。

需要 Python 3.9 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 启动三个进程

终端 1，启动中继：

```bash
.venv/bin/agent-sidecar relay --database demo-data/relay.db
```

终端 2，启动 Agent A：

```bash
.venv/bin/agent-sidecar serve --port 8801 --data-dir demo-data/alice
```

终端 3，启动 Agent B：

```bash
.venv/bin/agent-sidecar serve --port 8802 --data-dir demo-data/bob
```

生产环境中不要把 Sidecar API 暴露到公网；默认只监听 `127.0.0.1`。

## 建立信任并发送消息

查询双方身份：

```bash
curl -s http://127.0.0.1:8801/v1/identity
curl -s http://127.0.0.1:8802/v1/identity
```

记下返回的 `agent_id` 和 `signing_key`。在真实环境中，应通过二维码、邀请链接或组织 CA 等独立渠道核对 `signing_key`，不能盲信中继。

A 信任 B：

```bash
curl -s -X POST http://127.0.0.1:8801/v1/peers/trust \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"B_AGENT_ID","expected_signing_key":"B_SIGNING_KEY"}'
```

B 信任 A：

```bash
curl -s -X POST http://127.0.0.1:8802/v1/peers/trust \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"A_AGENT_ID","expected_signing_key":"A_SIGNING_KEY"}'
```

A 向 B 发送任务：

```bash
curl -s -X POST http://127.0.0.1:8801/v1/messages/send \
  -H 'Content-Type: application/json' \
  -d '{"recipient_id":"B_AGENT_ID","type":"task.request","payload":{"task":"总结这段内容"}}'
```

B 拉取、解密并确认消息：

```bash
curl -s http://127.0.0.1:8802/v1/messages/inbox
```

再次拉取会返回空数组，因为成功解密的消息已 ACK，并在本地按 `message_id` 去重。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

也可以直接运行一次 Alice → Bob 的完整演示。它会确认中继数据库没有出现消息明文，并展示 Bob 解密后的结果：

```bash
.venv/bin/python scripts/demo.py
```


### TLS

Relay 和 Sidecar 支持使用 operator-provided TLS certificate：

```bash
agent-sidecar relay \
  --host 0.0.0.0 \
  --port 8787 \
  --database relay.db \
  --access-token "$RELAY_ACCESS_TOKEN" \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem

agent-sidecar serve \
  --host 0.0.0.0 \
  --port 8801 \
  --data-dir node-a \
  --relay https://relay.example:8787 \
  --relay-token "$RELAY_ACCESS_TOKEN" \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem
```

生产环境必须使用受信任 CA 或正确分发的内部 CA，不要把自签名证书验证关闭。Relay 还可以使用 `--access-token` 保护注册、身份目录、消息投递和 ACK；Sidecar 用 `--relay-token` 发送该 token。可用 `--rate-limit N` 设置每个源 IP 每分钟的 protected request 上限，超限返回 HTTP 429。

## 安全边界与下一步

这是供功能验证的 MVP，还不是生产安全产品。当前中继仍能看到发送方、接收方、时间和密文大小；HTTP 也只适合本机测试，跨机器部署必须增加 TLS。私钥目前以权限 `0600` 的文件保存，生产版应迁移到 macOS Keychain、TPM、HSM 或其他系统密钥库。

投入生产前还应实现：

- Signal Double Ratchet 或 MLS，以提供会话级前向保密和群组密钥轮换；
- 公钥透明日志、撤销和密钥轮换协议；
- Sidecar 本地 API 调用者认证与能力令牌；
- 中继限流、垃圾消息治理、审计与密文自动清理；
- 附件的分块加密对象存储；
- 联邦中继发现与跨域投递。

## 统一通信频道与直连协作

Dashboard 的“通信对象”选择器提供三条明确路径：

```text
Public        -> Relay 广播
项目留言板    -> 成员本地签名事件副本 + Sidecar 直连 HTTPS 同步
私人私聊      -> 对单个节点的直连 HTTPS
```

项目和私人消息不会自动回退 Relay。节点需要使用 `--direct-endpoint` 发布可达地址；生产部署必须为 Sidecar 启用 TLS，并配置证书指纹 pinning。NAT、端口映射和防火墙由部署方负责，当前版本不提供 STUN/TURN 或自动打洞。

项目 Owner 选择候选节点时先发送签名加密邀请，不会直接修改成员清单。只有被邀请节点主动接受并返回签名响应后，Owner 才发布包含新成员的签名清单。加入后默认信任当前以及以后由 Owner 签名加入的项目成员，但不会升级为全局长期信任。项目留言先写入本地副本，再直接同步给其他成员；发起者离线不会阻止其余成员继续读取和发布。

直连接口和 JSON 示例见 [`docs/interfaces/`](docs/interfaces/README.md)。

## 联邦学习

当前版本已经提供轻量联邦学习核心、真实加密 Sidecar 消息链路、显式信任界面、跨 Harness 契约和可复现 Benchmark。

### 本地端到端演示

```bash
.venv/bin/python scripts/run_federated_demo.py
```

该演示启动 5 个逻辑节点，运行 10 轮 Trimmed Mean，并注入一个缩放攻击节点。结果写入 `runs/federated-demo/`。

真实 Sidecar 加密消息链路演示：

```bash
.venv/bin/python scripts/run_federated_network_demo.py
```

Coordinator 和 Node 作为独立 OS 进程通过 Relay 运行：

```bash
.venv/bin/python scripts/run_federated_process_demo.py
```

### 信任界面

启动 Relay 和 Sidecar 后访问：

```text
http://127.0.0.1:<sidecar-port>/ui/trust
```

新节点默认是 `pending`，只有在本节点界面勾选并保存后才会进入本地 allowlist。信任不会传递。

FL 审计接口：

```text
GET /v1/federated/rounds
GET /v1/federated/recoveries
```

它们返回本地 SQLite 中的 round 状态、abort/completed 结果和 recovery_id 审计记录。

### 聚合策略

- `fedavg`
- `fedprox`
- `median`
- `trimmed_mean`
- `krum`
- `secure_fedavg`

```bash
agent-sidecar federated run --nodes 5 --rounds 10 --strategy median --output runs/median
```

### Benchmark

```bash
agent-sidecar benchmark --config configs/benchmark/small.json --output runs/benchmark-small
```

Benchmark 比较 6 种聚合策略（含 Secure FedAvg）、3 种通信结构（Star、Hierarchical、Gossip）和 5 种攻击场景，输出安全性、通信量和 round 延迟。根目录额外生成 `comparison.csv` 和 `comparison.md`。Secure FedAvg 会对 round participant set 做 hash commitment，并要求参与节点在本地 trust UI 中互相信任以完成 pairwise X25519 mask；如果预期参与节点掉线，配置 `secure_max_dropouts > 0` 时会通过 surviving nodes 的 pairwise mask contribution 做恢复；每个 recovery response 有持久化 `recovery_id`，重复提交会被拒绝并记录审计；恢复失败仍会 fail-closed 中止 round，不会聚合不完整 masked sum；可选 `dp_clip_norm` 和 `dp_noise_std` 用于隐私/效用实验；Benchmark 会输出 basic Gaussian RDP upper bound 的 epsilon/delta，并标记为 `upper_bound_under_declared_assumptions`；由于当前 simulator 对 model update 加噪而不是对已证明敏感度的逐样本梯度加噪，不会把它标记为端到端形式化 DP 保证。

### 跨 Harness

启动符合统一 HTTP 契约的参考 Harness：

```bash
agent-sidecar harness serve --port 8900
```

检查配置中的 Harness 能力：

```bash
agent-sidecar harness describe --config configs/harness/toy.example.json
```

核心代码通过 `HarnessAdapter` 工作，不把 DeepSeek 写死在联邦学习核心中。当前提供 `ToyHarness`、`CommandHarnessAdapter`、`HttpHarnessAdapter`、`DeepSeekHarnessAdapter` 和针对现有 DSH + MicroVM Controller 的 `DshControllerAdapter`。

现有 DSH Controller 的任务接口通过以下配置接入：

```bash
agent-sidecar harness describe \
  --config configs/harness/dsh-controller.example.json
```

真实 DeepSeek 训练命令通过 `configs/harness/deepseek.example.json` 映射。Node 可以通过 `--harness-config` 载入 Toy、HTTP、Command 或 DeepSeek adapter 配置。

运行 Sidecar Dashboard 后，也可以在“新节点 Harness 配置”面板中直接粘贴 JSON 配置、检查能力并保存。配置写入该节点数据目录下的 `harness-config.json`，使用 `agent-sidecar federated node` 启动时会自动读取；显式传入 `--harness-config` 时仍以命令行参数为准。配置接口为：

```text
GET  /v1/harness/config
POST /v1/harness/config/test
POST /v1/harness/config
```

Dashboard 只回显脱敏后的 `registry_token`、API token 等字段；新节点的 Harness descriptor 会随 `node.capabilities` 广播，Relay 不会看到 Harness 凭据。

### 微信全功能 Skill 目录

Dashboard 提供截至 2026-10-16 的微信全功能 Skill 目录。账号、聊天、联系人、群聊、朋友圈、发现、视频号、公众号、小程序、微信支付、个人工具、隐私安全和桌面端能力被拆分为独立 manifest；可搜索、按模块筛选、逐个安装或批量安装。安装只写入本地 Skill store，所有目录 Skill 均保持 `disabled-by-default`，不会自动操作真实微信账号。

```text
GET  /v1/skills/catalog
POST /v1/skills/catalog/install   {"skill_ids":["wechat.group.create"]}
POST /v1/skills/catalog/install   {}  # 安装完整目录
```

对应 MCP 管理工具为 `skill.catalog.list` 和 `skill.catalog.install`。每个 manifest 都包含入口、动作、角色、前置条件、可见性、状态、限制、平台和核验状态；`wechat.group.mute_capability_check` 明确记录个人微信群原生禁言边界，避免与企业微信、QQ 或腾讯云 IM SDK 混淆。

运维启动和故障处理见：`docs/OPERATOR_RUNBOOK.md`。
实现状态和当前外部前置条件见：`docs/IMPLEMENTATION_STATUS.md`。

公共广播、MCP、Trace 和开放 Skill 的规格见：`docs/BROADCAST_SPEC.md`。
项目级临时信任和项目结束自动撤销规则见：`docs/PROJECT_TRUST_SPEC.md`。

详细规格见：

- `docs/FL_SPEC.md`
- `docs/BENCHMARK_SPEC.md`
- `docs/HARNESS_SPEC.md`


Coordinator 重启恢复：

```python
pending = coordinator.pending_round()
if pending:
    result = coordinator.resume_pending_round(timeout_seconds=30)
```

Round 配置保存在 `fl_rounds.config_json`，全局模型和 round index 保存在 `fl_coordinator_state`，重启后不会直接回到初始模型。


本机直接使用已安装的 DSH：

```bash
agent-sidecar harness describe \
  --config configs/harness/dsh-headless.example.json
```

真实执行需要 DSH credentials service 或 `DEEPSEEK_API_KEY`。如果凭据缺失，适配器会返回清晰的 credential error，不会把错误文本当作联邦模型更新。

配置 DSH credentials 的两种方式：

```bash
# 单次运行优先级最高，不会写入仓库
DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" dsh --profile headless "return a structured result"
```

或通过 DSH credentials service / `~/.dsh/.credentials.yaml` 保存 `DEEPSEEK_API_KEY` 引用；保存后再次运行：

```bash
agent-sidecar harness doctor --dsh-bin dsh --profile headless
```

Doctor 只显示是否配置，不显示密钥值。


真实 DSH 联邦训练 smoke test：

```bash
python scripts/run_dsh_federated_smoke.py
```

脚本先运行非敏感 preflight；credentials 未配置时会在调用 DSH 前退出，不会消耗模型调用。credentials 配置后，它会要求 DSH 返回结构化 `model`、`num_examples` 和 `metrics`。


### 节点能力广播

Sidecar 启动并登录 Relay 后，会自动在 `node.capabilities` 频道广播节点能力，包括：

- 操作系统和架构；
- CPU 逻辑核心数和内存；
- GPU 是否存在及可识别名称；
- 摄像头是否存在及可识别名称；
- Relay 可达性、成功率、平均延迟、P95 延迟和稳定性；
- MCP、Trace、Skills、联邦学习等服务能力。

接口：

```text
GET  /v1/capabilities
GET  /v1/nodes/capabilities
POST /v1/capabilities/refresh
```

能力广播是明文公共广播，不应包含密钥、凭据、私有路径或用户敏感信息。
