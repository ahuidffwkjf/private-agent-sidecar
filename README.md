# Sidecar Toy 0.3.2 跨平台下载与使用

这是本地运行的实验应用，内置后端和运行依赖，不用另装 Python、Node.js 或 DSH。
Release 只有在四个原生平台均通过应用及下载产物验证后才发布。

## 选择下载文件

| 你的电脑 | 推荐文件 | 其他选择 |
| --- | --- | --- |
| Windows x64（常见 Intel/AMD 电脑） | `Sidecar-Toy-0.3.2-win-x64.exe` | 同名 `.zip`，完整解压后打开 `Sidecar Toy.exe` |
| Linux x64 | `Sidecar-Toy-0.3.2-linux-x64.deb`（Debian/Ubuntu） | `.AppImage` 用于兼容发行版 |
| Mac M 系列（Apple Silicon） | `Sidecar-Toy-0.3.2-mac-arm64.dmg` | 同名 `.zip` |
| Mac Intel | `Sidecar-Toy-0.3.2-mac-x64.dmg` | 同名 `.zip` |

不要把 mac-arm64 下载包用于 Windows 或 Intel Mac。本次不宣称支持 Windows ARM、Linux ARM、
Android 或 iOS；“跨平台”是上表四个目标，不等于任意操作系统/处理器都可运行。

### Windows

下载 EXE 安装程序后运行，按提示选择安装位置，打开 Sidecar Toy。
ZIP 版须完整解压到新目录，不能只抽出一个 EXE，也不能从压缩包内部直接运行。
本包没有商业代码签名，系统可能显示未识别发布者/SmartScreen 提示；不要关闭系统保护。
企业管控机器需要管理员按组织政策批准。Windows ARM 模拟运行不在验收范围内。

### macOS

打开对应架构的 DMG，将 `Sidecar Toy.app` 拖到 Applications 后打开；或者完整解压 ZIP。
应用声明最低 macOS 13，CI 在 macOS 15 对两种 CPU 分别验收，不能据此宣称每个旧系统均已实机测试。
目前为 ad-hoc 签名预览，**没有 Apple Developer ID 公证**，下载后的首次运行可能出现提示或被阻止。
不需要安装 Python，但这不等于跳过系统安全审核；不要全局关闭 Gatekeeper 或删除隔离标记。

### Linux

Debian/Ubuntu 用户优先使用 DEB，通过发行版软件安装器安装，系统包依赖由包管理器处理。
其他兼容 x64 发行版可尝试 AppImage：下载后在文件属性中允许执行，再打开。
AppImage 可能需要发行版提供的 FUSE 2 兼容组件；发行版图形库、glibc 和 Chromium sandbox
限制也可能影响启动。没有把不同发行版的系统库全部封装成通用 Linux 操作系统。

本版 Linux 包在 Ubuntu 22.04 x64 原生环境构建并运行验收。CI 验证 AppImage 提取出的实际
程序和 DEB 安装后的实际程序；这不等于已验证每种发行版的 FUSE 挂载器。
遇到 sandbox 配置错误时使用 DEB 或让管理员按系统政策配置，不使用 `--no-sandbox` 绕过保护。

## 首次使用

1. 启动后自动建立本机 Relay、研究采集服务和 Alice、Bob、Carol、Dave 四个独立用户。
2. 在“广播大厅”阅读采集告知，填写当前用户昵称并明确确认。
3. Alice 发布任务；从菜单“演示用户”切到 Bob，Bob 独立确认后申请任务。
4. 切回 Alice 批准，Bob 等待群就绪后进入项目群。
5. 用子任务继续分工，成员接受后进入独立子群，最后逐层提交和验收。

macOS 快捷键为 Command+1..4，Windows/Linux 为 Ctrl+1..4；Windows/Linux 隐藏菜单栏时可按 Alt
显示菜单。切换会重新载入用户页面，未发送输入不保证跨切换保留。

默认不需要联网，不自动连接发布者的香橙派或任何私有服务器。
摘要明确标注“演示摘要 · 非模型生成”，没有模型下载或费用；真实模型/远程接入仍需额外配置。

## 退出与数据

关闭最后窗口或退出应用会停止它拥有的本地服务；重新打开会恢复身份、任务、同意状态和消息。
菜单“文件 → 打开数据目录”显示真实数据位置，通常为：

- Windows：`%APPDATA%\Sidecar Toy`
- macOS：`~/Library/Application Support/Sidecar Toy`
- Linux：`$XDG_CONFIG_HOME/Sidecar Toy` 或 `~/.config/Sidecar Toy`

更新前退出旧程序，应用目录与数据目录分开；卸载程序不自动删除研究/聊天历史。
备份含私钥与正文，不要上传 GitHub。下载的安装包不含作者的数据或凭证。

**实验隐私：Toy 会在每位用户确认后，将本应用私聊、群聊和相关可见交互记录到本地研究数据库。**
请仅使用合成内容，不输入秘密；不采集其他应用聊天、未发送草稿或模型隐藏推理。
本地默认模式不向外部服务器上传；手动配置远程服务时另行确认权限和数据目的地。

## 发布验证与限制

下载页的 `SHA256SUMS` 可核对所有安装包，每个平台的 `validation-*.json` 记录源码提交、
包哈希、原生桌面检查与提取/安装后检查。打包完成但测试失败的产物不会自动作为 Release 发布。
已有版本保留；本版本不覆盖旧的源码包或 Mac 0.3.1。

所有平台仍是研究预览，而非经过安全审计、正式签名或所有硬件验收的商业产品。
用户数据目录权限和系统安全策略不能用安装包代替。后续优先补正式 Windows 签名与 Apple 公证。
