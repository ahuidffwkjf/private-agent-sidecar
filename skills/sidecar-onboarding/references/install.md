# 让 Agent 安装 Sidecar、Tailcat 与操作 Skills

这是实际源码安装包，不是下载链接清单，也不是 Plugin。包内有 Sidecar 源码、
安装与启动程序、接入 Skill，以及此前 361 个独立类微信功能 Skill。
后者可以被 Agent 加载，但**功能目录不等于已经实现的执行器**；它们不会自动
操作真实微信，也不会解锁尚未实现的支付、音视频等能力。

## 给 Agent 的请求

把下面这段交给有终端、Git 和网络权限的 Agent：

> 从 https://github.com/ahuidffwkjf/private-agent-sidecar.git 的
> codex/agent-onboarding 分支获取安装版本，读取 skills/sidecar-onboarding/SKILL.md
> 和 docs/AGENT_INSTALL.md。安装 Sidecar、Tailcat 接入程序与随包的 361 个操作
> Skill。不要删除现有源码或节点数据，不要输出秘密。生成设备公钥申请交给管理员，
> 收到批准的 access.json 与可信 SHA-256 后继续配置、启动和验证。请报告真实状态，
> 不把安装成功当作联网成功，不把能力目录当作已实现功能。

分支是交付入口，不是稳定版本号；Agent 必须记录 `git rev-parse HEAD`。
生产使用应由管理员提供固定 commit 或已核验的发行包摘要。
若分支或上述文件不存在，报告“发布尚未就绪”，不要改用 main 的历史接口。

## 本机安装

支持 macOS 和 Linux，安装器需要 Python 3.10+、venv/pip。Windows 尚未验证。
不会自动 sudo、改防火墙、重启现有服务或覆盖系统 Python。
若依赖缺失，Agent 按检测到的操作系统请求安装权限，不假定所有 Linux 都用 apt。

在一个**不存在的**新目录 clone，不要先删除旧目录：

```sh
git clone --branch codex/agent-onboarding --single-branch https://github.com/ahuidffwkjf/private-agent-sidecar.git sidecar-onboarding-source
cd sidecar-onboarding-source
git rev-parse HEAD
python3 scripts/sidecar_setup.py doctor
python3 scripts/sidecar_setup.py install
```

默认安装位置：`~/.local/share/private-agent-sidecar`。可将 `--home /绝对路径/新目录`
放在子命令前做隔离安装。已有非托管目录会拒绝写入，同一版本重跑保持身份；
不同版本要求另选目录，未提供静默覆盖升级。

安装器复用已安装的 **Tailcat v0.7.0**，复制到其私有版本目录；可传
`install --tailcat /绝对路径/tailcat`。Linux amd64/arm64/armv7 没有 Tailcat 时可用
`install --download-tailcat`，下载官方固定版本并验证内置 SHA-256。
macOS 不提供该版本的 GitHub 二进制资产；由 Agent 检查现有/Homebrew 安装，
经用户允许准备 v0.7.0 后再安装，**不接受任意最新版本**。包不含 Python、
Tailcat 二进制或第三方依赖 wheel，需要网络安装依赖，不是完全离线安装包。

安装器实际完成：

- 私有版本目录、独立 venv、Sidecar 安装；保留原仓库和原环境。
- 本机生成 Tailcat 客户端私钥、Sidecar 身份；不拷贝别的设备身份。
- `enrollment.json`：只含客户端公钥、Sidecar 公共身份和源码摘要。
- `Open Sidecar.command`：macOS 双击入口；Linux 可在终端执行同一文件。
- 361 个操作 Skill 的 `SKILL.md` + JSON reference，以及 1 个接入 Skill。

安装成功状态为 `awaiting_approval`，不是 connected。不要把私钥、api-token、
TLS 私钥、Relay token 或整个安装目录交给管理员；只交 `enrollment.json`。

## 首次接入批准（管理员）

前提：管理员运行同一份源码，已按 `docs/TAILCAT_DEPLOYMENT.md` 部署
HTTPS Relay 与 Tailcat 服务端。旧 0.3/0.4 API-only 服务不能混用。
Relay 证书必须覆盖客户端访问的 **IP SAN 127.0.0.1**，不能关闭 TLS 验证。

1. 独立核验用户/设备申请，将申请中的 Tailcat 公钥加入服务端 allowlist，
   **保留已批准客户端**。由管理员确认并重新加载隧道配置。
2. 使用下面的脚本生成 0600 配置包，参数均为文件路径；真实秘密不放命令行：

```sh
python3 scripts/issue_sidecar_access.py \
  --request /安全目录/enrollment.json \
  --address-file /安全目录/tailcat-server-address \
  --token-file /安全目录/relay-token \
  --ca-file /安全目录/relay-ca.pem \
  --output /安全目录/access.json \
  --approved
```

输出只有状态和配置包 SHA-256。通过受保护通道交付 `access.json`，通过独立可信
渠道确认摘要。此包含隧道地址与 Relay token，**不是公开文件或可提交的邀请**。
`--approved` 是管理员声明，脚本不会自动修改线上白名单或证明批准已生效。
当前 Relay token 是部署级共享令牌；设备隔离依靠各自 Tailcat 密钥和白名单。
设备撤销需要管理员撤销对应 Tailcat 公钥；凭据泄露还需轮换 Relay token。

## 导入并打开

```sh
python3 scripts/sidecar_setup.py configure --access /安全目录/access.json --sha256 管理员核验的64位摘要
python3 scripts/sidecar_setup.py open --browser
```

不需要手填 Tailcat 地址、生成密钥或编辑 env。`configure` 核对设备公钥、
源码摘要、配置完整性、端口和 CA；仅配置成功尚不代表网络可达。
`open` 启动受控隧道子进程，验证 TLS 和 Relay 认证，然后启动 Sidecar，
核对本地 API 返回的 Agent ID，最后打开浏览器。所有管理端口只监听 loopback。

首次配置后，后续双击安装目录里的 **Open Sidecar.command** 即可。
该入口是前台会话，不是开机服务；终端保持运行，Ctrl+C 会只停止本次启动的进程。
启动最多等待有限时间，失败不会转用公网地址、HTTP 或不验证证书的路径。
端口已占用时不会杀别的程序；管理员可签发使用其他 local_port/node_port 的配置包。

```sh
python3 scripts/sidecar_setup.py status
```

状态含义：`not_installed` / `awaiting_approval` / `configured` /
`local_node_running`。最后一个只是本地身份 API 可达；完整网络验证以 open
刚启动时的 `connected` 为准，不能从旧 session.json 推断 Relay 仍在线。

## 加载操作 Skills

安装包内每个功能都有独立目录，例如：

```text
wechat-message-text/SKILL.md
wechat-message-text/references/manifest.json
wechat-group-create/SKILL.md
wechat-moments-post-photo/SKILL.md
sidecar-onboarding/SKILL.md
```

将它们安装到 Agent 支持的 Skill 搜索目录：

```sh
python3 scripts/sidecar_setup.py skills --target /你的Agent使用的skills目录
```

共 362 个目录，遇到同名不同内容会在写入前报错，不覆盖用户 Skill。
Agent 宿主如何扫描/刷新目录，以该宿主文档为准；安装器不擅自修改全局设置。
也可以只从安装目录 `releases/<版本>/agent-skills/` 取所需功能文件夹。

原始 Sidecar JSON 目录仍随产品提供，可在控制台安装。Agent 的 SKILL.md 是
加载说明；MCP 的 `skill.*` 调用仍遵循 disabled-by-default，二者不是同一种执行权限。
原目录写着“客户端与官方资料确认”的字段没有逐项附带证据，因此本包不据此宣称
361 项全部实测。功能在 Sidecar 不存在时，Agent 必须报告 not_implemented。

## 验收、重装与排错

验收顺序：源码版本确定 → install 成功 → 公钥申请获批 → configure 成功 →
open 的 TLS/API/身份检查成功 → 浏览器打开 → Skills 目录可被宿主发现。
连通不自动产生好友信任；双方明确授权后才测试消息。Relay 隧道不等于私聊直连，
不要误把 loopback 地址当成跨设备 direct endpoint。

- 证书错误：核对 CA、SAN 127.0.0.1、系统时间；不使用 `-k`。
- 连接超时：核对 Tailcat 服务、设备白名单和 Relay；不要打印隧道地址诊断。
- HTTP 401：管理员核对 Relay token，不打印 token。
- pip/依赖失败：安装失败保留独立版本目录，修复依赖后重试；不删除节点数据。
- 需要重新安装验证：用新的 `--home`，测试成功再经用户批准退役旧环境。
  备份需要停下相应节点或使用 SQLite backup，不直接删除仓库、identity 或数据库。

公开源码安装包用 `scripts/package_sidecar_installer.py` 生成，明确白名单选择源码，
排除 runtime、数据库、.venv、.git、私钥和用户配置。包内 SHA256SUMS 可检测传输损坏，
发布包的整体 SHA-256 仍应从可信发布渠道核对。
