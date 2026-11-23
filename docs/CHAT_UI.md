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
