# 节点通信与多智能体

更新：2026-11-06。

## 身份与关系

- **节点 `agent_id`**：保留现有公钥派生身份，承担签名、加密、双向信任与项目成员权限。
- **Harness `harness_id`**：由节点身份确定的稳定本机标识，不随模型配置变化重新分配。
- **智能体 `actor_id`**：每次注册生成独立 ID，随名称、职责、所属节点持久保存；可同名但 ID 不同。
- 智能体公开档案由所属节点签名，随能力广播发布，不授予接收节点信任。
- 智能体定向私聊只能使用原有受信任直连。接收方先校验档案签名和节点归属，再检查接收 ID 是否存在。
- 收件箱按接收 ID 过滤；智能体并非独立密钥、OS 进程或本机权限边界，注册也不会启动一个模型进程。

## 界面操作

1. “我 → 本机智能体 → 注册智能体”，填写名称和职责，可重复注册多个。
2. “通讯录”核对节点公钥，发送信任请求；对端接受后建立双向信任。
3. 联系人资料中“发给智能体”，选择本机发送者及对端接收者，发送消息。
4. 接收方在“我 → 本机智能体 → 查看收件箱”查看消息。
5. Skills 页保留 23 项通信工具，可搜索、安装、查看 Manifest 和运行。修改状态的表单明确要求确认执行。

## 精简范围

删除旧 361 项微信产品目录及无关的朋友圈、视频号和小程序入口。当前只保留：

| 分类 | 数量 | 范围 |
| --- | ---: | --- |
| 节点与信任 | 8 | 身份、能力、发现、关系、通知、信任、回应、撤销 |
| 智能体通信 | 4 | 注册、列表、定向发送、收件箱 |
| 公开广播 | 2 | 查询、发布 |
| 项目协作 | 9 | 列表、创建、邀请、邀请通知、回应、读留言、发留言、同步、关闭 |

仅原生白名单工具可执行；安装的 Manifest 不能指定任意命令或 URL。
旧官方安装记录在节点启动时退出活跃目录并归档到 `retired-skills/`；用户自定义 Skill 保留。
旧文件名 `wechat_skills.py` 和旧内部函数保留为兼容导出，不再包含微信产品目录。

## 验收方式

- `tests/test_communication_skills.py`：逐个调用全部 23 个 Skill，真实本机 HTTP + 两节点生命周期；另测归档、未知工具、参数错误、未信任、签名冒用、目标不存在、去重和离线注册持久化。
- `scripts/check_skill_ui.mjs <isolated-url> <output> --isolated`：逐项详情、安装、Manifest、分类与搜索；四种尺寸滚动到最后一项；执行查询、注册两个智能体及刷新后身份保留。
- 测试只在隔离数据目录运行，不证明公网 NAT、真实 DSH 模型执行或微信账号自动化可用。

下一项优先工作：将真实两台机器上的 Harness 进程绑定到注册身份，验证带独立调用权限的持续收发；当前同一节点内共享本机管理权限。

## 交付记录

本次主要文件：

- `agent_sidecar/actors.py`、`communication_skills.py`、`wechat_skills.py`：节点担保智能体身份、原生工具白名单和旧导入兼容。
- `agent_sidecar/service.py`、`store.py`、`mcp.py`：注册、广播、定向消息校验、收件箱、旧目录归档及工具调度。
- `agent_sidecar/web/app.css`、`app.js`：滚动修复、精简入口、智能体管理和通信 Skill 执行表单。
- `tests/test_communication_skills.py`、`test_wechat_skills.py`、`test_chat_ui.py`、`test_agent_install.py`：协议、兼容性和安装包回归。
- `scripts/check_skill_ui.mjs`、`check_chat_ui.mjs`、`export_agent_skills.py`、`package_sidecar_installer.py`、`package_wechat_skills.py`：浏览器验收与新版导出。
- `README.md`、`docs/HARNESS_SPEC.md`、`docs/CHAT_UI.md`、`docs/IMPLEMENTATION_STATUS.md`、`docs/AGENT_INSTALL.md` 及 onboarding Skill/安装参考：同步使用方式与边界。

测试命令：

```sh
.venv/bin/python -m unittest tests.test_chat_ui tests.test_wechat_skills -v
.venv/bin/python -m unittest tests.test_communication_skills tests.test_wechat_skills -v
.venv/bin/python -m unittest discover -s tests -v
node --check agent_sidecar/web/app.js
node --check scripts/check_skill_ui.mjs
node --test desktop/tests/policy.test.cjs
# 先设置 PLAYWRIGHT_CHANNEL=chrome 和 NODE_PATH=<本机 Playwright 包目录>
node scripts/check_chat_ui.mjs <隔离预览地址> runs/communication-chat-ui-final
node scripts/check_skill_ui.mjs <隔离预览地址> runs/communication-skills-ui-final --isolated
.venv/bin/python scripts/run_federated_demo.py
.venv/bin/python scripts/run_federated_process_demo.py
.venv/bin/python -m agent_sidecar benchmark --nodes 8 --rounds 2 --output runs/communication-benchmark-20261106-n8
git diff --check
```

Benchmark：8 节点、2 轮、90 组，配置、逐轮数据和摘要保留在
`runs/communication-benchmark-20261106-n8/`。18 个无攻击组合准确率均约 0.9958；
这是模拟器回归，不是智能体权限或生产安全证明。最初的 4 节点配置因 Krum 要求至少 2f+3 个更新而失败，后改为 8 节点完成。

确定性 FL demo 完成 10 轮；独立进程 demo 完成 3 轮，每轮接受 3 个更新、无拒绝。
桌面策略测试 3 项通过。两节点通信定向测试和兼容性测试共 10 项通过。
最终完整回归 **135 项全部通过**，耗时 61.354 秒，日志为
`runs/communication-full-suite-verified.log`。此前一次全量运行中旧 DSH 模拟器测试
触发 2 秒超时；单项重跑和最终串行全量均通过，未调整超时阈值。
浏览器最终验收：聊天 9 组、Skills/智能体 8 组全部通过，无页面脚本错误；
23 个 Skill 的详情、安装和 Manifest 均逐项验证，全部原生动作由两节点 HTTP 生命周期测试覆盖。
日志位于 `runs/communication-*.log`；最终浏览器报告分别保存在上述两个 `*-final` 目录。

外部边界：未连接真实 DSH 模型进程；未做本次公网双机验收；本轮修改源码与本地预览，不声明既有安装包或服务器已经自动更新。
