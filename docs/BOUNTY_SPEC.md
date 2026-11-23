# 悬赏与多层 Relay 协作协议

版本：`sidecar-bounty/1`，2026-11-18。此文是新架构的实现规格。

## 1. 角色与边界

- **发布者**：任何入网节点均可广播任务、奖励单位和额度、验收条件、截止时间及最大层数。
- **业务 Relay**：主动申请承接悬赏，获得发布者报价并确认后，负责分解子任务、选择节点、分配奖励、验收结果。参与节点可在父契约允许时主动升为下一层业务 Relay。
- **Node**：自主决定是否参与、接受哪份报价、执行任务及提交结果；不会因收到广播或邀请而自动加入。
- **传输 Relay**：现有 HTTP/HTTPS 服务，只承载公共签名事件。它不做业务分工、不训练策略、不代替节点授权。

同一节点可以同时激活 Relay 和 Node 决策模块。业务角色按具体契约记录，不是机器的永久身份。
业务组织结构可以多层；**当前传输层仍使用单个 Relay 的持久化事件流**，不宣称已实现多跳网络转发或无中心共识。

## 2. 生命周期

```text
发布者 A 广播任务和总奖励
  B 自愿申请 -> A 向 B 报价 -> B 核对公钥并接受为 Relay
    B 向 C 发出子任务/奖励/信任请求 -> C 接受为 Node
      C 自愿升为 Relay（须已有转委派权限）
        C 向 D 发出子任务/奖励/信任请求 -> D 接受为 Node
```

申请不等于获奖或接单，发出报价不等于对方已信任。父方签名的 offer 加上子方签名的 accept
才形成该任务契约的双边同意。Offer 绑定子节点公钥；响应需再次确认父节点公钥。
公钥应通过独立渠道核对，不能把目录查询视作对节点行为的担保。

这种信任**只授权该契约的任务操作**。不修改全局 allowlist，不隐式加入旧 Owner 项目，
不让 A 自动信任 C/D，也不赋予整个子树读取私聊或项目数据的权限。
原有节点级信任申请 API 仍可用于独立的长期信任；私密消息仍走现有加密及信任检查。
新契约不复用旧项目的“Owner 签名成员自动信任”规则。

## 3. 奖励与拓扑约束

- Reward 是正整数，最大 `10^12`；单位为显式字符串，例如 credits。禁止浮点数、布尔值和负数。
- 发布者全部根报价总额不超过悬赏总额；每个 Relay 的直接下游报价总额不超过它自己的契约额度。
- 待响应报价也预留额度，防止并发超额承诺。拒绝或撤销未接受报价后可释放预留。
- 已接受契约即使后来撤销，也不会自动释放额度，以免再次承诺已经答应他人的奖励。
- 支持多根组织者和多分支。同一悬赏中一个节点最多接受一个父契约，已接受后本版不支持换父。
- 父链不能包含子节点，避免循环；发布时限制最大深度 `1..8`，第一层组织者为深度 1。
- 子任务必须由仍有效、处于 accepted 状态且允许转委派的 Relay 发出。
- 父契约撤销、悬赏关闭或到期后，整个下游分支不再有效，但历史记录保留。

Reward 是**公开承诺**，不是冻结资金、可兑现余额或支付凭证。当前没有托管、结算、退款、
罚则、身份经济担保或争议仲裁。`payment_status` 固定为 `not_implemented`。

## 4. 提交与验收

子节点通过 `submit` 提交 `sha256:<64个小写十六进制字符>` 的结果引用，不把私密文件正文放进广播。
父节点通过 `review` 接受或拒绝当前提交；拒绝后允许重新提交，旧 submission ID 不能验收新版本。
上级不能越过直接父节点验收孙节点。Relay 提交自身结果前必须先处理所有尚未结束的下游工作。
本版验证的是契约和引用格式，**不会自动下载、执行、校验语义质量或证明交付物真实存在**。
被验收的结果保留为 completed，并不代表付款完成。

## 5. 公共事件与同步

事件沿用 `agent-broadcast/1` 的签名信封，频道为 `sidecar.bounty.v1`，正文：

```json
{"schema":"sidecar-bounty/1","action":"apply","data":{"bounty_id":"...","proposal":"..."}}
```

任务描述、奖励、邀请、申请、角色、验收条件和结果哈希均为**明文公共控制数据**，包括 Relay 在内的
入网节点都可读取。禁止放入凭证、私密数据或完整敏感交付物。网络生产部署仍必须使用 TLS。

传输服务端新增 `POST /v1/bounty-events` 和 `GET /v1/bounty-events?after=<seq>&limit=100`。
发布需已注册且签名有效；接口沿用 Relay token 和限流。单条最多 32 KiB，时间窗口校验拒绝过期、
明显未来或超过 5 分钟的未入库事件。完全相同的 event ID 和内容可重试；同 ID 不同内容拒绝。
事件流和公开广播在同一事务中写入，事件顺序由 AUTOINCREMENT seq 给出。

每个 Sidecar 自行验证签名、双边授权、预算和状态转移，不信任事件内容为可执行指令。
签名有效但非法的业务操作会记录为 rejected，不修改树；缺目录/网络故障不推进本地 cursor。
历史事件按入库时间重放，保留超时后的审计记录。事件 ID、cursor、outbox 均持久化到本地 SQLite。
每页最多 500 条，一次同步最多 20 页；未追平时禁止新业务操作。

`serve` 和桌面后端每 5 秒被动拉取新事件；普通内嵌 `Sidecar` 对象需调用 `bounties.sync()` 或 `start()`。
被动同步**不会自动申请、接单、激活模型、给予长期信任或转出奖励**。
应用调用方需提供 request_id。断线重试复用同一签名事件，同 request_id 不允许换内容；
queued 不是对方同意。若结果未知，先重试原请求，其他新动作会被阻止。未入库事件超过签名发送窗口
标记 expired，可用新 request_id 重新明确操作；已入库而 ACK 丢失则从事件流恢复状态。

本版保留完整历史，不提供清理协议或快照压缩。不能把公开日志保留当作无限规模方案。
更换/重建传输 Relay 导致 feed_id 变化时失败关闭，需显式迁移；没有自动合并多个 Relay 的历史。
单 Relay 可拒绝服务、审查或对不同接收者呈现不同顺序；本版**不提供拜占庭日志共识或真实资金防双花证明**。

## 6. 本机 API

本机接口保持现有管理权限边界，不能对不可信远端暴露。桌面模式继续要求会话认证。

| 方法 | 路径 | 内容 |
| --- | --- | --- |
| GET | `/v1/bounties` | 本地已验证的悬赏、申请、报价树和发件箱摘要 |
| POST | `/v1/bounties/sync` | 拉取并验证事件，不接单 |
| POST | `/v1/bounties/actions` | 显式动作，下表列出字段 |
| GET | `/v1/decisions` | 两个模型的激活、特征、版本、参数、样本数 |
| POST | `/v1/decisions/roles` | `{"relay":true,"node":true}` |
| POST | `/v1/decisions/examples` | `role`、`features`、`target` |
| POST | `/v1/decisions/train` | `role`，可选 `epochs`、`learning_rate` |
| POST | `/v1/decisions/suggest` | `role`、候选列表 `candidates` |

所有业务动作使用 `{"action":"...","data":{...},"request_id":"调用方唯一ID"}`：

| action | data 必需字段 |
| --- | --- |
| publish | title, task, reward, unit, acceptance, deadline, max_depth |
| apply | bounty_id, proposal |
| offer | bounty_id, parent_id, child_id, child_key, task, reward, acceptance, allow_relay |
| respond | offer_id, decision (`accept/reject`), parent_key, role (`relay/node`) |
| promote | offer_id |
| revoke | offer_id, reason |
| submit | offer_id, artifact_hash |
| review | offer_id, submission_id, decision (`accept/reject`), reason |
| close | bounty_id, reason |

根 offer 的 parent_id 是空字符串，其他 offer 的 parent_id 是上游已接受报价的 event_id。
发布动作的 event_id 就是 bounty_id；报价动作的 event_id 就是 offer_id。
`deadline` 为 Unix 秒时间戳，最长 30 天。当前 root 组织者必须先 apply，接单角色必须为 relay。
普通成员可以先以 node 接受，然后通过 promote 自愿升级，前提是 allow_relay 已获父方同意。

新增 MCP 工具：`bounty.list`、`bounty.sync`、`bounty.act`、`decision.status`、
`decision.roles`、`decision.suggest`、`decision.record`、`decision.train`。
它们独立于原来的 23 个通信 Skill，不修改旧 Skill 目录的安装状态和含义。

## 7. 双决策模块

独立参数、数据、版本和激活开关，默认都关闭。训练某一头不修改另一头，也不自动激活它。

| 模块 | 目标方向 | 当前输入特征（都需归一化至 0..1） |
| --- | --- | --- |
| Relay | 组织候选节点、兼顾覆盖、可靠性、负载与成本 | capability_match, reliability, coverage, cost, load, risk |
| Node | 评估自身任务收益、成本、风险与学习价值 | reward, success_probability, learning_value, cost, duration, risk |

当前实现为可独立训练的本地线性效用基线，不是预训练大模型，也不是已实现的强化学习策略。
`record` 接收本机显式提供的 target（-1..1），`train` 最少需两个样本，使用确定性批量梯度下降拟合。
输出训练集 MSE，不声称样本外效果。每头最多 10000 个样本，每次最多 500 epoch。
真实反馈来源、标签定义、成本归一化、奖励设计和独立验证集仍需确定，不能把合成样本当真实收益验证。

`suggest` 返回候选排名，若最高分不大于零则 recommended_id 为空。建议不会自动发布子任务、
花费奖励、授予信任或调用外部模型。Relay 的候选排名可辅助分工，但**没有自动生成任务分解计划**。
预算、签名、权限、期限和拓扑检查始终由协议层强制执行，模型分数不能越权。
本地管理 API 尚未提供每个进程的独立凭据；不要把角色开关当成进程安全隔离。

## 8. 验证与下一步

```sh
.venv/bin/python -m unittest tests.test_bounties -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_bounty_demo.py --output runs/bounty-demo
```

本版交付后端 API、MCP、持久化和可运行演示；现有聊天页仍显示通用公开广播，尚无专用悬赏看板。
联邦学习包保持归档，本轮没有恢复 FL 开发或调用真实 DeepSeek。
下一项优先工作：确定并采集真实任务的收益/成本和分工质量反馈，建立双策略模块独立评测集。
