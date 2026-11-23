# 微信式聊天界面

初版交付：2026-11-05；通信聚焦更新：2026-11-06。

## 入口与行为

- `/`、`/ui`：桌面三栏聊天界面，手机端底部导航与独立会话页。
- 聊天：公共广场、当前项目讨论、双向信任节点私聊；草稿按会话保留在当前页面内存中，刷新页面不保留。
- 通讯录：节点资料、签名公钥确认、信任申请、项目邀请与撤销信任。
- 发现：仅保留公开广播信息流，移除与节点通信无关的入口。
- Skills：23 个原生通信工具，支持搜索、分类、安装、查看 Manifest 和运行。
- 我：节点身份、注册多个本机智能体、智能体收件箱、联系人统计、项目统计、设置。
- `/ui/admin`：保留旧的完整控制台与批量 Skill 安装。
- `/ui/trust`：保留原信任管理页。

此界面不是腾讯微信客户端，不登录或控制真实微信账号。项目群聊沿用签名项目留言板与成员邀请，不等同微信个人群；公共动态沿用 Relay 公开广播，不实现朋友圈好友可见性或点赞评论。

## 退出项目

- 普通项目讨论：在聊天右上角“详情”中选择“退出项目”；发起人显示“结束并退出项目”。必须再次确认，取消或接口失败不会移除会话或丢失草稿。
- 操作沿用 `POST /v1/projects/<project_id>/close`。确认成功后返回公共广场，并从活跃会话列表移除该项目；本机历史记录保留，项目临时信任撤销，单独建立的全局好友信任不变。
- 普通成员的操作只关闭本机项目视图，不通知发起人移除成员，也不修改远端签名成员清单。确认框明确显示这一边界，不能把它解释为已完成全网成员退群或密钥轮换。
- 发起人关闭时，按现有后端流程尝试通知成员；界面不把本机关闭成功宣称为所有离线节点已经同步完成。
- 任务关联的项目继续跳转到任务详情：成员使用“退出任务”，负责人按现有“取消任务”或“结束任务”流程处理，不以普通项目关闭接口绕过子任务与成员权限检查。

此改动仅增加已有生命周期接口的界面入口与回归验证，不改变传输、信任协议或拓扑，Benchmark 不适用。下一项生命周期工作是独立设计并验证普通项目的签名成员退出通知与 Owner 清单确认，而不是把本机退出误报为远端成员已撤销。

### 2026-11-23 退出入口验证

- 本次修改：`agent_sidecar/web/app.js`、`tests/test_direct_routing.py`、`scripts/check_chat_ui.mjs`、本文件；不修改后端协议，不操作用户已有项目。
- 修改前：`tests.test_chat_ui` 与 `tests.test_sidecar.ProjectScopedTrustTests` 共 7 项通过；增加本机退出 API 回归后，定向测试 8 项通过。
- `.venv/bin/python -m unittest discover -s tests -v`：163 项通过，日志 `runs/project-exit-full-tests.log`。
- `node --check agent_sidecar/web/app.js`、`node --check scripts/check_chat_ui.mjs`、`git diff --check` 通过。
- `PLAYWRIGHT_CHANNEL=chrome NODE_PATH=<bundled node_modules> node scripts/check_chat_ui.mjs http://127.0.0.1:63218/ui runs/project-exit-ui`：13 组通过，无页面脚本错误。真实隔离节点验证发起人关闭；独立浏览器模拟普通成员的取消、失败、重试、其他会话草稿及好友保留。
- 已检查 1440px、390px、320px 的确认框截图，未发现横向溢出。结果及截图：`runs/project-exit-ui/`。
- 外部阻塞：无本次界面交付阻塞；未重新打包已安装的桌面应用。源码服务重新加载页面即可取得新静态资源，旧安装包需要重新构建后更新。

## 本次文件

- `agent_sidecar/web/`：HTML、CSS、交互代码、本地 Lucide 图标与许可证。
- `agent_sidecar/service.py`：聊天首页、固定白名单静态资源、旧控制台入口。
- `pyproject.toml`、`scripts/sidecar_setup.py`：wheel 与安装器中的静态资源。
- `tests/test_chat_ui.py`：页面路由、MIME 类型、路径穿越拒绝。
- `tests/test_federated.py`：旧控制台断言迁至 `/ui/admin`，保留信任协议验证。
- `tests/test_agent_install.py`：安装器静态资源完整性验证。
- `scripts/start_chat_preview.py`：启动隔离回环地址预览，记录 PID 和日志。
- `scripts/check_chat_ui.mjs`：真实本地接口、模拟失败场景与响应式浏览器验收。
- `README.md`、本文件：入口、运行方式与交付记录。

## 验证

- 修改前：`.venv/bin/python -m unittest tests.test_wechat_skills -v`，5 项通过。
- 修改后：`.venv/bin/python -m unittest discover -s tests -v`，123 项通过；网络用例需本机端口权限。
- `node --check agent_sidecar/web/app.js` 与 `git diff --check` 通过。
- 浏览器：`PLAYWRIGHT_CHANNEL=chrome NODE_PATH=<bundled node_modules> node scripts/check_chat_ui.mjs <isolated-preview-url> runs/chat-ui-check-final`，9 组通过，无页面脚本错误。
- 浏览器覆盖实际公开发送、项目创建与发送、单项安装；失败与过期响应使用独立浏览器上下文注入，不修改用户节点数据。桌面 1440px、手机 390px/320px 均截图检查。
- 验收记录和截图：`runs/chat-ui-check-final/summary.json`、同目录 PNG。
- `.venv/bin/python -m pip wheel . --no-build-isolation --no-deps --wheel-dir runs/chat-ui-wheel` 构建通过；已检查 wheel 包含全部 5 个静态资产。
- Benchmark：不适用。本次不修改聚合、信任协议或通信拓扑；未据此新增安全性或联邦训练完成声明。

## 剩余边界

真实微信执行器、朋友圈存储与权限、音视频/附件服务、支付和第三方生态没有在此次界面改造中实现。当前节点没有其他在线成员时，不会伪造联系人或聊天记录。

下一项最高优先任务：将真实两台机器的 Harness 进程绑定到智能体身份，验证独立授权后的持续收发，不扩展朋友圈或支付等无关能力。

## 2026-11-06 回归

- 修复 Grid 隐式行被长列表撑高：限制主网格行和列表容器高度，搜索、筛选和连接状态固定，列表独立滚动。
- 初始复现：361 项列表高度 24945px，800px 窗口内 scrollTop 始终为 0。
- 精简后逐项检查全部 23 个 Skill 的详情、安装、Manifest 与重载持久化；原生查询可从界面调用。
- 1280x800、1440x600、390x844、320x568 均用滚轮到达最后一项并打开详情；新增两个智能体及收件箱入口通过。
- 浏览器结果：`runs/communication-skills-ui-20261106/summary.json`（8 组、无脚本错误），聊天回归 `runs/communication-chat-ui-20261106/summary.json`（9 组、无脚本错误）。
- API 生命周期：`tests/test_communication_skills.py` 对两个真实本机 HTTP 节点执行全部 23 项；涵盖信任、4 个智能体定向通信、项目邀请、留言与撤销。
- 详细边界见 `docs/COMMUNICATION_SKILLS.md`；不把浏览器/API 验收宣称为真实模型运行或公网部署验收。
- 最终回归：135 项全部通过，日志 `runs/communication-full-suite-verified.log`；最终浏览器报告为 `runs/communication-chat-ui-final/summary.json` 和 `runs/communication-skills-ui-final/summary.json`。
