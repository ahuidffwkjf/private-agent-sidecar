# Sidecar 桌面版

独立窗口、Dock/任务栏图标、桌面三栏聊天界面和菜单。发行包包含 Electron、Python 后端及其依赖；无需用户安装 Python、Node.js 或手动启动浏览器和服务器。

当前打包基线仅保留 **23 项原生通信 Skill**：节点与信任 8 项、智能体通信 4 项、公开广播 2 项、项目协作 9 项。旧微信功能目录不再分发；已有旧官方目录在启动时归档，用户自定义 Skill 和聊天记录保留。源码安装器另带 1 项安装接入 Skill，它不属于这 23 项业务能力。

## 平台

| 系统 | 安装包 | 源码构建环境 |
|---|---|---|
| Windows x64 | `.exe` 安装器、`.zip` | Windows、Node.js 22、Python 3.12 |
| macOS Apple Silicon | `.dmg`、`.zip` | Apple Silicon Mac、Node.js 22、Python 3.12 |
| macOS Intel | `.dmg`、`.zip` | Intel Mac、Node.js 22、Python 3.12 |
| Linux x64 | `.AppImage`、`.deb` | Ubuntu 24.04、Node.js 22、Python 3.12 |

这些是构建目标，不代表已经在每个系统上验证。请以 GitHub Actions `Desktop packages` 对应系统的成功记录、原生窗口验收和产物为准。二进制包按系统和架构分别生成，不能把 Mac 的 Python 后端拷贝进 Windows 安装包。

## 下载后使用

1. 在仓库 Actions 中打开一次成功的 **Desktop packages** 运行，下载与你的系统匹配的产物。标签 `desktop-v*` 或手动运行工作流都会构建；当前不自动发布 GitHub Release。
2. 安装或解压后打开 Sidecar。Windows 安装器可创建桌面快捷方式；Mac 将应用放入“应用程序”；Linux 使用 AppImage 或安装 deb。
3. 默认打开本机工作区，首次启动创建身份。关闭全部窗口或退出应用会停止该应用启动的后端，聊天记录和身份不会被删除。
4. 菜单“文件 → 连接设置”可以改为连接已部署的 Sidecar 服务。远程服务必须使用有效 HTTPS；只有本机回环地址允许 HTTP。此处不是 Relay 地址，也不是腾讯微信账号登录。

默认本机工作区仅有自己的节点，不会自动连接其他用户。跨机器协作需要部署可达的 Sidecar/Relay、TLS 和节点信任；使用本仓库现有部署文档，不能把回环地址当公网地址。

## 从源码启动

Windows 可双击 `Start-Sidecar.cmd`，macOS 可运行 `Start-Sidecar.command`；Linux 可执行 `sh Start-Sidecar.command`。这些源码启动器会安装锁定的 npm 依赖，需要网络、Node.js 和 Python。它们不是已经包含全部依赖的二进制发行包；不想安装开发环境的用户应下载对应平台的安装包。

在仓库的 `desktop` 目录执行（PowerShell、cmd、bash、zsh 均可，需要 Node.js 22.12 或更高版本）：

```sh
npm ci
npm start
```

源码首次启动会在 `desktop/.venv` 建立专用环境，安装 `requirements-build.txt` 中的依赖，不修改系统 Python 或项目原有 `.venv`。支持 Python 3.10–3.14；CI 固定 Python 3.12。可用 `SIDECAR_BUILD_PYTHON` 指定解释器路径。

Electron 下载默认使用官方源。受网络限制时可设置 `ELECTRON_MIRROR` 指向可用镜像后执行 `npm run runtime:install`；安装器仍使用官方 npm 包内的 SHA-256 清单检查运行时。不要设置 `electron_use_remote_checksums` 或关闭校验。

## 打包

```sh
npm test
npm run pack
npm run smoke
npm run dist
```

- `pack` 生成本机可运行的应用目录。
- `smoke` 启动该目录内的真实应用，验证窗口隔离、发送、重启持久化、Skill 安装、连接设置以及退出后端清理。测试使用临时用户数据目录，不读写真实聊天。
- `dist` 生成本机系统安装包，位于 `desktop/release`。
- Mac 的 DMG 使用系统 `hdiutil` 生成并校验，避免另行下载 DMG 辅助工具；每组安装包生成 `SHA256SUMS`。
- Linux 构建需要 AppImage/deb 相关系统工具；CI 已配置。无显示环境用 `xvfb-run -a npm run smoke`。
- Linux 自动验收仅为所构建目录的 `chrome-sandbox` 设置 root 所有权和 `4755` 权限，不关闭 Chromium sandbox 或全局 AppArmor。发行用户遇到沙箱限制时优先安装 deb 或由管理员按发行版政策配置，不使用 `--no-sandbox`。
- 完整后端独立检查：`python scripts/check_backend.py backend-dist/SidecarBackend/SidecarBackend`（Windows 末尾加 `.exe`）。它从临时工作目录启动冻结程序，证明不依赖仓库当前目录或 Python 源码路径。

## 数据与权限

数据目录由 Electron 的系统用户目录 API 决定，通常为：

- macOS：`~/Library/Application Support/Sidecar`
- Windows：`%APPDATA%/Sidecar`
- Linux：`$XDG_CONFIG_HOME/Sidecar` 或 `~/.config/Sidecar`

菜单“打开数据目录”显示实际位置。`workspace/` 保存身份、数据库和已安装 Skill；`connection.json` 只保存连接模式/服务地址，不存密码；`logs/backend.log` 为本次启动的诊断日志。备份请在退出应用后复制整个 `workspace/`，其中包含身份私钥，不能发给他人或提交仓库。

主窗口无 Node.js 权限、开启 context isolation 和 sandbox，没有通用 native bridge。只有本地设置窗口有受限的连接设置 IPC。内置后端仅监听 `127.0.0.1`，应用会话使用 HttpOnly cookie，密钥经 stdin 传递，不进入 URL、命令行或日志。直连消息仍由现有签名/加密协议检查，桌面封装不绕过信任规则。

## 发行边界

- 默认产物尚未接入 Apple Developer 签名/公证或 Windows 代码签名。公开分发前应配置相应证书；Gatekeeper/SmartScreen 可能阻止或警告未签名包。不要要求用户全局关闭系统安全保护。
- 不提供自动更新；更新前退出旧程序，数据目录独立于安装位置。
- Linux ARM、Windows ARM 暂未列为自动构建目标。
- 桌面版读取随当前后端分发的 Skill 目录，不固定条目数量，也不因为打包就增加任何执行能力。具体能力与执行边界以对应版本的后端与 Skill 说明为准。
- 真实 DeepSeek/DSH 训练仍依赖外部 runtime、凭据和模型，本包不包含 PyTorch/模型权重。
