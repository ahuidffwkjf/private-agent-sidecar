# Sidecar Toy Desktop 0.3.2：四平台本地运行版

下载后安装/解压即可运行，内置 Python 后端，无需安装 Python、Node.js、Tailcat 或 DSH。

| 系统 | 下载 |
| --- | --- |
| Windows x64 | `Sidecar-Toy-0.3.2-win-x64.exe` 或 `.zip` |
| Linux x64 | `Sidecar-Toy-0.3.2-linux-x64.deb` 或 `.AppImage` |
| Mac Apple Silicon | `Sidecar-Toy-0.3.2-mac-arm64.dmg` 或 `.zip` |
| Mac Intel | `Sidecar-Toy-0.3.2-mac-x64.dmg` 或 `.zip` |

完整操作说明见附件 `Sidecar-Toy-Download-Guide.zh-CN.md`。启动后在菜单“演示用户”切换
Alice、Bob、Carol、Dave，分别同意实验采集后体验任务发布、申请、审批、项目群和分层子任务。

## 验收规则

本 Release 由四个平台原生 CI 全部通过后才发布，附各平台验证 JSON 和安装包 SHA-256。
检查涵盖冻结后端、空 PATH 运行、会话认证、用户切换、任务/群聊、重启后新消息和退出清理；
另外从 Mac ZIP/DMG、Windows ZIP/NSIS、Linux AppImage 提取文件/DEB 安装目录运行实际应用。
Linux 使用 Xvfb 图形环境，AppImage 的 FUSE 挂载行为不作为所有发行版都能运行的保证。

## 重要边界

- Windows 包没有商业发布者签名，Mac 为 ad-hoc 签名、未公证，系统可能提示或阻止首次启动。
  不要求用户关闭系统安全保护。正式无警告分发仍需配置代码签名与公证。
- 本次支持上述桌面架构，不包括 Windows/Linux ARM、Android、iOS。
- Toy 确认后采集本应用私聊/群聊及相关可见交互到本地研究库，只输入合成数据。
- 摘要为明确标记的演示抽取器，不是真实模型输出，不产生模型调用费用。
- 不含作者的账户、节点私钥、聊天数据库、研究数据库、API/Relay 凭证或实际部署地址。
- 单机演示默认不需要外网，实际多机组网仍需安全连接配置与单独验收。
