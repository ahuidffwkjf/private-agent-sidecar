# Sidecar Toy Desktop 0.3.1

**可以下载后直接运行的 macOS Apple Silicon 应用，内置 Python 和全部运行依赖。**

## 下载哪个文件

- 推荐：`Sidecar-Toy-0.3.1-mac-arm64.dmg`，打开并把 Sidecar Toy 拖入“应用程序”。
- 或下载 `Sidecar-Toy-0.3.1-mac-arm64.zip`，解压得到同一个 `.app`。
- 不需要安装 Python、Node.js、pip、Tailcat 或 DSH，也不需要连接香橙派。
- 仅适用于 M 系列 Mac；包声明 macOS 13+，实际测试平台为 Apple Silicon macOS 26.5。

## 开始使用

双击打开应用，阅读 Alice 的实验采集告知后发布任务。菜单“演示用户”可以切换
Alice、Bob、Carol、Dave（Command+1 至 Command+4）。切到 Bob 申请，再切回 Alice 批准。
四个身份独立，不能代真实参与者同意采集。完整说明见附件 `Sidecar-Toy-App-Guide.zh-CN.md`。

本地服务随应用启动；关闭最后窗口或退出应用后停止。重开保留身份、任务和消息。
数据与源码预览和旧 Sidecar 版本隔离，位于 `~/Library/Application Support/Sidecar Toy/`。

## 明确边界

- **本包为 ad-hoc 签名预览，没有 Apple Developer ID 签名/公证。**系统可能提示或阻止首次打开。
  不要求关闭 Gatekeeper、不移除系统隔离属性；无提示公开分发仍需开发者签名和公证。
- Toy 模式在用户确认后把本应用私聊、群聊和相关交互收集到本地研究数据库。
  只输入合成测试内容，不输入秘密。默认本地模式不向外部服务器上传。
- 摘要标注“演示摘要 · 非模型生成”，不是实际 DSH 输出，不产生模型费用。
- 不含发布者的账号、数据、密钥、token、模型权重或联邦学习训练依赖。
- 本次不发布未经验证的 Intel Mac、Windows 或 Linux 原生安装包。

## 修复与验收

- 内置四用户后台、用户菜单、自动进入任务大厅、会话认证和退出清理。
- 重启后由项目负责人签名更新临时端口，验证新群消息仍可投递，不新增成员或全局信任。
- 本地回环流量不经过系统代理，避免实验内容误走外部代理。
- 冻结后端在空 PATH、非源码目录下启动成功；实际 `.app` 完成任务发布、入群、群聊、重启和清理验收。
- 具体测试、校验和及签名状态见附件验证报告与 `SHA256SUMS`。
