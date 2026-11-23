# Toy 协作实验模式

本版聚焦真实用户可以操作的任务协作闭环，不要求报酬、FL 或决策模型。
任务目录与旧 `sidecar-bounty/1` 分离，使用签名的 `sidecar-toy-request/1` 操作。
目录服务是实验中可信的元数据协调者，不是拜占庭共识；私聊与群聊仍使用原有加密传输。

## 一台机器演示

```sh
.venv/bin/python scripts/toy_workspace.py --output runs/my-toy --demo-summary
```

启动一个 Relay、一个研究采集服务和四个独立 Sidecar 进程，输出四个用户 URL。
每个用户在微信式界面“发现 → 广播大厅”填写昵称并确认实验内容采集后进入。
不同端口对应不同用户，勿将单个端口当成多账户服务器。
`--demo-summary` 必须显式指定，界面显示“演示摘要 · 非模型生成”；省略时摘要保持未配置，
任务仍可发布，但不会伪造模型摘要成功。演示目录不含真实用户数据。

```sh
.venv/bin/python scripts/toy_workspace.py --stop runs/my-toy/workspace.json
```

停止脚本检查进程命令所属目录，不触碰现有节点。SIGTERM 停止不会删除数据库。
Windows 用户请通过独立终端/服务管理器停止对应进程；当前自动停止脚本依赖 POSIX `ps`。

## 实际用户部署

1. 每位用户配置独立 Sidecar 数据目录、节点密钥、昵称与 Harness；稳定 user_id 等于 node_id。
2. 运行共享 Relay 和独立研究服务；跨机器部署必须使用 TLS、私有目录和访问控制。
3. 将 `configs/toy/node.example.json` 配成实际地址，保存为各节点数据目录的 `toy-config.json`。
   同一实验使用相同 experiment_id。配置不含密钥；密钥仍走本机安全环境/秘密文件。
4. 沿用原 `serve` 或网络管理入口，配置真实可达的直连 HTTPS 端点与证书指纹。
   项目临时信任不自动变成全局信任；Tailcat 的现有路由策略不因 toy 模式放宽。
5. 用户打开自己的微信式 UI，确认采集后使用；本机管理接口不能开放给不可信远端。
   桌面已有会话认证仍有效。Toy 目前没有共享 Sidecar 的多用户登录隔离。

研究服务：

```sh
.venv/bin/python -m agent_sidecar.research \
  --database /PRIVATE/study/research.db --experiment study-01 \
  --admin-token-file /PRIVATE/study/admin-token --host 127.0.0.1 --port 8799
```

非回环监听必须同时配置 `--tls-cert` 与 `--tls-key`。管理员 token 至少 32 字符，
只放在私有文件，不写入命令行值、Git 或日志。数据库文件使用 0600；目录、备份及 Windows ACL
也需要部署方保护。HTTP 端点有大小限制，本版本尚无生产级采集限流、容量治理和高可用。

## 用户流程与层级

- 发布主任务后创建唯一主群；原文、验收要求与模型摘要面向同实验大厅公开。
- 申请绑定任务版本、目标群、负责人和条款哈希；发布者批准后使用加密项目邀请完成入群。
  客户端只对与自己已签名同意完全匹配的邀请自动回执。目录批准不等于成员清单已到达。
- 重复批准不重复建群；失败显示加入处理中，后台重试邀请和清单投递。
- 负责人只能从当前父群成员中指定子任务负责人；负责人接受后才创建自己的子群。
  子群邀请仍需被邀请者确认，不能通过大厅招募外部节点。
- 群成员、密钥与聊天互相独立。祖先负责人可查看分支结构和进度，但不会自动获得下级群消息。
  非直接监督者仅获得必要层级元数据，不看到子任务正文、提交或成员列表。
- 默认三层、最多八层；父关系创建后不可修改，故不能制造循环。任务正文/验收条件本版不可改写；
  若需求变化，应取消并重新发布，不能沿用旧申请同意。
- 普通成员退出父群会撤销其下游成员资格；有未关闭自有分支的负责人须先关闭分支。
- 子任务负责人向直接上级提交结果，由上级验收；主任务参与者提交给发布者。
  交付物只保存说明与可选引用，系统不执行附件、不自行判断结果质量。完成主任务前须处理下级任务。

## Harness 与摘要

通用接口：

| 接口 | 含义 |
| --- | --- |
| `GET/POST /v1/toy/profile` | 当前实验用户、采集状态／显式同意与昵称 |
| `GET /v1/tasks` | 同步当前用户可见任务 |
| `POST /v1/tasks` | title、original、acceptance、可选 max_depth、request_id |
| `GET /v1/tasks/{id}` | 任务、摘要、本人申请或负责人申请列表 |
| `POST /v1/tasks/{id}/applications` | terms_hash、proposal、request_id |
| `POST /v1/tasks/{id}/actions` | action 及动作字段、request_id |
| `POST /v1/tasks/{id}/subtasks` | owner_id、title、original、acceptance、request_id |
| `POST /v1/tasks/{id}/summary-retry` | 明确重试摘要 |
| `POST /v1/tasks/{id}/summary-result` | source_hash、version、结构化 content |
| `POST /v1/toy/trace` | 白名单 ui.view 或 harness.interaction 的可见交互记录 |

动作包括 apply、approve/reject（node_id）、withdraw、leave/remove、accept_subtask、
invite/accept_invite、submit（text/reference）、review（submission_id/decision/reason）、finish/cancel。
接口返回状态错误不会悄悄添加成员。调用方应提供稳定 request_id；业务动作记录到本地 outbox，
网络故障重试不重复执行。未入库签名超过五分钟需重新确认操作，旧请求不会被无限期重新签名。

MCP 提供 task.list、task.detail、task.action、task.summary.complete；现有 DSH 插件提供
sidecar_tasks、sidecar_task_detail、sidecar_task_publish、sidecar_task_action、
sidecar_task_subtask、sidecar_task_summary。写操作依旧要求 allowMutations=true。
可见 tool 输入/输出会通过 trace 接口记录；插件不能读取或收集 DSH 未提供的会话/隐藏推理。

发布后持久化摘要作业自动运行。每个输出绑定 source_hash 和 version；非结构化输出被拒绝，
失败显示明确状态，保留原文。固定结构：

```json
{
  "goal": "一句话目标",
  "todos": ["需要完成的事项"],
  "deliverables": ["交付物"],
  "constraints": ["未说明"],
  "questions": ["未说明"]
}
```

摘要只使用任务原文与验收要求；不能把任务文本当作可执行指令。三种接入方式：

- 发布者 Harness 主动调用 summary-result / sidecar_task_summary 回传生成的内容。
- summary.kind=http：本机已配置的独立摘要 Harness 提供 `POST <url>/summary`，请求带 original、
  acceptance 和固定 instruction，返回上述 JSON。服务端须禁用工具和业务写权限。
- summary.kind=dsh：必须提供专用 profile（不能用 default/headless）和 isolated_launcher 参数列表。
  Sidecar 在临时 cwd 用无 shell 参数调用，过滤环境、限制超时、不保存第三方 stderr。
  launcher 由部署方提供并验收：需限制文件读取、禁用工具/Sidecar 本机网络访问，只允许模型调用。
  未配置 launcher 时失败关闭，不使用普通 DSH 会话冒充隔离。

本机存在 dsh 可执行文件并不证明配置/凭证可用。本轮没有安装新的用户 profile，也没有验证真实
DSH 模型调用；默认演示不会产生模型费用。隔离 launcher 与真实凭证属于实际部署前置条件。

## Trace 内容与权限

Toy 用户确认后，应用内任务、信任、发送/接收、群消息、私聊、浏览事件、接入工具交互进入本地
研究 outbox，随后用节点签名请求上传独立 research.db。消息存储与相应内容 trace 同事务写入；
网络上传在后台进行，失败保留队列并显示积压，不影响已落库的消息。

不采集确认前的历史、未发送草稿、其他应用聊天、API token、私钥或隐藏推理。可见文本执行
敏感字段及常见密钥模式过滤，不能保证识别所有人为嵌入的秘密，实验者仍应避免发送真实秘密。
完整正文参与者都须确认；跨实验或未确认节点不能参与采集型通信。正式 standard 模式不上传内容。

事件记录 experiment_id、user_id/node_id、可选 actor_id、对象关联、观测类型、时间、参与者、
consent_id 与脱敏 payload。发送与接收是两种不同观测，按 event_id 去重而不是把两者误判成重复。
节点签名证明来源，不证明自报行为必然真实，也不替代研究标签核验。

上传成功后，本地 outbox 清除正文仅保留收据；原业务聊天库仍是用户自己的正常历史。
退出采集停止新增记录和未上传内容的上传，既有研究数据保留到管理员删除。新实验应使用新目录，
不能把旧 toy 身份/数据库静默切换进另一个实验。

管理员查询/导出：

```sh
.venv/bin/python scripts/research_admin.py --url https://research.example \
  --admin-token-file /PRIVATE/admin-token export --output /PRIVATE/exports/study.jsonl
```

删除某用户及其他记录内包含该用户的副本（只删除采集库，不删除业务聊天）：

```sh
.venv/bin/python scripts/research_admin.py --url https://research.example \
  --admin-token-file /PRIVATE/admin-token --node-id agent:USER_ID delete --confirm-delete
```

省略 node-id 将删除整个实验。删除保留身份 tombstone，防止重传复活；不自动清除已经导出的
离线文件或外部备份，这些由管理员负责。管理端无公共查询入口，只有持管理员 token 的调用者
可以访问；普通节点只能提交自己的签名记录。

## 验证入口与边界

```sh
.venv/bin/python -m unittest tests.test_toy_tasks -v
.venv/bin/python -m unittest discover -s tests -v
node --experimental-vm-modules scripts/check_dsh_toy_plugin.mjs
.venv/bin/python scripts/run_toy_process_demo.py --output runs/toy-process
# NODE_PATH 设置为本机 Playwright 包目录
node scripts/check_toy_ui.mjs runs/my-toy/workspace.json runs/toy-ui-check
```

四节点进程验收和 UI 测试均只作用于隔离数据。实际公网 TLS/NAT、真实用户知情同意和 DSH 隔离
配置需要额外验收；本轮不声明生产安全、金融支付或真实策略训练效果。
