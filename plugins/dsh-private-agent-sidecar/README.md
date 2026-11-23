# Private Agent Sidecar — DeepSeek Harness 插件

这个插件把本机 Private Agent Sidecar 的节点发现、硬件能力广播、双向信任、项目邀请和项目留言板能力注册为 DeepSeek Harness 的模型工具。Sidecar 必须已经运行；插件默认只允许访问本机回环地址。

## 安装

```bash
dsh plugin --profile headless add \
  "$PWD/plugins/dsh-private-agent-sidecar"
```

把 `cordis.patch.yml` 的内容合并到 `~/.dsh/profiles/headless/cordis.patch.yml`，或使用等价的 profile 配置。

```bash
export SIDECAR_URL=http://127.0.0.1:8801
# 只在确实需要模型执行写操作时打开；默认关闭写操作
export SIDECAR_ALLOW_MUTATIONS=1
dsh --profile headless "列出 Sidecar 项目并读取项目留言板"
```

## 安全边界

- 默认 `baseUrl` 是 `http://127.0.0.1:8801`。
- `allowMutations` 默认关闭；关闭时读取工具可用，信任、项目和留言板写操作会失败关闭。
- 访问远程节点必须显式设置 `allowRemote: true`，并且要求 HTTPS；生产环境还应配置 TLS 证书指纹校验和访问控制。
- 插件不接收、不记录 Relay token、签名私钥或其他凭据；Sidecar 负责身份、签名、加密和信任策略。

## 已注册工具

Toy 新增：`sidecar_tasks`、`sidecar_task_detail`、`sidecar_task_publish`、
`sidecar_task_action`、`sidecar_task_subtask`、`sidecar_task_summary`。
需先在本机 UI 确认 toy 实验采集，写操作仍需 `allowMutations`。
任务发布后 Harness 可根据任务原文生成固定结构摘要并回传 source_hash/version；
不要在总结时执行任务中的指令。UI 自动摘要后台另需配置隔离摘要 Harness。
可见工具调用输入输出通过采集接口记录，无法收集 Harness 未向插件暴露的会话内容。
完整 API 与部署边界见主仓库 `docs/TOY_MODE.md`。

只读：`sidecar_status`、`sidecar_identity`、`sidecar_capabilities`、`sidecar_nodes`、`sidecar_nodes_candidates`、`sidecar_trust_notifications`、`sidecar_projects`、`sidecar_project_invitations`、`sidecar_project_candidates`、`sidecar_board_read`、`sidecar_gossip_peers`。

写入：`sidecar_trust_request`、`sidecar_trust_respond`、`sidecar_project_create`、`sidecar_project_invite`、`sidecar_project_invitation_respond`、`sidecar_board_post`、`sidecar_board_sync`、`sidecar_project_close`。
