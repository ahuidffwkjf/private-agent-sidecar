---
name: sidecar-onboarding
description: 从 GitHub 或完整安装包安装 Private Agent Sidecar，生成设备接入申请、导入管理员批准的 Tailcat 配置、启动节点并验证连接；同时加载随包的类微信操作 Skills。用于全新节点接入、重装验证和启动诊断，不用于绕过审批或操控真实微信。
---

# Sidecar 安装与接入

## 交付物与入口

官方项目：`https://github.com/ahuidffwkjf/private-agent-sidecar.git`。
先确认所选分支/版本包含 `scripts/sidecar_setup.py` 和 `docs/AGENT_INSTALL.md`，
再按 [安装手册](references/install.md) 执行。不存在时报告发布未就绪，
不要换用历史 0.3/0.4 的 WebSocket、Bearer 管理接口或猜参数。
可单独复制此 Skill 文件夹；手册随 Skill 提供，无须安装 Plugin。

## 操作顺序

1. 在新目录取得用户指定版本，记录实际 commit。不要删除已有工作区、虚拟环境、
   身份、证书或聊天数据。优先使用安装程序独立的 `--home`。
2. `doctor` 检查 Python、Tailcat、系统支持。macOS/Linux 为当前范围；
   Python/系统依赖缺少时让用户授权安装，不自行 sudo 或改系统安全设置。
3. `install` 创建私有虚拟环境和不可覆盖的版本目录，生成设备 Tailcat 密钥、
   Sidecar 身份、公钥接入申请和打开入口。`enrollment.json` 不含私钥。
4. 将接入申请交给授权管理员。等待批准；不得把安装成功说成已经接入。
   不在仓库、日志、聊天中输出 Tailcat 地址、Relay token、API token 或私钥。
5. 取得管理员通过可信渠道提供的 access.json 和 SHA-256；`configure` 要求摘要
   匹配且申请公钥属于本设备。随后运行 `open`，核实 TLS、注册和本节点身份。
6. `skills` 将接入 Skill 及 361 个独立操作 Skill 安装到用户指定的 Agent Skill 目录。
   遇到同名不同内容时拒绝覆盖。Agent 加载支持取决于宿主；不自动改宿主全局配置。
7. 验收报告区分 installed / awaiting_approval / configured / connected。
   只有 live TLS/API 与身份检查成功才算 connected；本轮失败保留数据并报告具体阶段。

## 执行边界

- 接入网络不等于信任其他节点；保留显式双向信任和项目授权。
- Tailcat Relay 转发不等于私聊直连。不要把本机 loopback 地址广播成跨机器可达地址。
- 361 个操作 Skill 是可加载说明及契约，不是 361 个已实现执行器；按各自边界操作。
- 不开启旧包的 disabled-by-default 执行策略，不请求泛化 shell runner 权限。
- 不关闭 TLS，不清空白名单，不将通用管理员凭据分发给所有设备。
- 只有 `open` 创建的子进程能被其结束操作停止；不杀端口上其他程序。
