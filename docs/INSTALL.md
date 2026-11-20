# 安装 zmcl

## Arch Linux / AUR

[zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 当前为 **0.1.3-1 开发预览**，安装官方 Linux x86_64 预编译程序、桌面入口和图标。公开 AUR master 为 `60c9ad8e1be8204c6a4e0024c83a4a5851ada0f5`。

**公共 HTTPS 新克隆构建、正常系统升级和安装后六组原生 GUI 流程均已通过。** 本机已从 0.1.2-1 升级到 0.1.3-1，19 个受管文件、0 个改动，已安装程序版本、大小和摘要与官方包一致。首次 RPC、yay、paru 查询曾命中 0.1.2-1 缓存；2026-11-20 **19:46:59（Asia/Taipei）**复验均为 0.1.3-1。[完整回执](validation/aur-install-0.1.3-2026-11-20.json)

~~~sh
yay -S zmcl-bin
# 或
paru -S zmcl-bin
~~~

按已核验的公开 Git 配方安装：

~~~sh
git clone https://aur.archlinux.org/zmcl-bin.git
cd zmcl-bin
makepkg -si
~~~

安装后在应用菜单打开 **zmcl**，或运行 `zmcl gui`。用 `zmcl --version` 核对版本，`zmcl doctor --json` 检查运行条件。更新与卸载由包管理器管理：

~~~sh
yay -Syu
sudo pacman -R zmcl-bin
~~~

包管理器管理程序、桌面入口、图标、许可证和说明文件，不管理用户的 Minecraft 实例、存档和账号资料。0.1.0 的隔离安装、重装、卸载及合成存档保护检查保留在[历史记录](validation/aur-install-2026-11-20.json)；0.1.1 的升级及已安装窗口验收见文末历史记录。

0.1.3 保留了 0.1.2 的创建界面：筛选官方发行版、自动建议不重复的实例名，以及“创建并安装”或“仅创建”。本版使用 zgpui 0.1.5；功能、固定源码和平台验收边界见[0.1.3 报告](reports/2026-11-20-helper-memory.md)和[0.1.2 创建流程记录](reports/2026-11-20-gui-iteration.md)。

## 当前预览版的运行条件

| 条件 | 当前范围 |
| --- | --- |
| 平台 | Linux x86_64 |
| libc | glibc 2.39 或更新 |
| 字体库 | Fontconfig 2.17.0 或更新、FreeType |
| 原生窗口 | X11；Wayland 桌面需要 XWayland |
| 文件与下载任务 | 系统 curl、Python 3.8 或更新；按需启动，成本仍计入应用进程树 |
| Microsoft 凭据 | libsecret 提供的 secret-tool，以及桌面 Secret Service |
| Java | 系统 Java，或通过 zmcl 下载受管理的运行时 |
| Horizon 验签 | 可选 OpenSSL，需支持 Ed25519 |
| 字体 | 系统 TrueType/OpenType 字体 |

这些 ABI 下限来自已发布 ELF 的实际符号引用。AUR 配方声明相应依赖，附加功能组件列于 `optdepends`。完整版本、直接动态库、源码和文件身份保存在 `/usr/share/doc/zmcl/release.json`；本版直接 ELF 不依赖 OpenGL。源码在其他版本字体库上的兼容回退，不代表这个预编译包可忽略 Fontconfig 下限。

**完整 GUI 低于 10 MiB 的目标仍未完成。** 最终 0.1.3 管理流程的主进程 RSS 中位数为默认环境 11,316 KiB、明确指定系统 Python 时 11,020 KiB；完整相关树观测峰值 RSS/PSS 分别为 59,884/48,651 与 37,948/19,306 KiB。两种环境不能当成代码优化对照。大备份列表查询的局部下降、缺读、显示服务与观察成本见[本轮报告](reports/2026-11-20-helper-memory.md)和[性能记录](PERFORMANCE.md)。原 0.1.2 与 0.1.1 测量保留历史身份。

## 其他 Linux 系统

从 [GitHub v0.1.3](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.3) 下载 [Linux x86_64 归档](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.3/zmcl-0.1.3-linux-x86_64.tar.gz)与 [SHA-256 校验文件](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.3/zmcl-0.1.3-linux-x86_64.tar.gz.sha256)。归档为 **679,517 字节**，SHA-256：

~~~text
b15024e99dd1275db15020ef26669f24aaad9026d948dd6683cacac5c7647e0e
~~~

主程序为 **1,473,752 字节**，SHA-256：

~~~text
02620a8ec4fac34e63fcbe483268149b2ed4032ac2d7bf42975ff6b19b72a1aa
~~~

应用源码固定为 [9ef89bb](https://github.com/LIghtJUNction/zmcl/commit/9ef89bb92d46d324172d6ea8b13054c81434e178)。发行包支持 Linux x86_64。0.1.2 的 Windows GNU 跨编译和 zgpui 原生平台探针保留其原身份；本轮没有新增 0.1.3 Windows/macOS 应用的构建、安装或窗口/游戏运行验收。

校验并解包后，可使用其中的用户级安装器：

~~~sh
python3 scripts/install.py install
# 卸载这一用户级安装
python3 scripts/install.py uninstall
~~~

用户级安装默认写入 `~/.local`，AUR 安装使用 `/usr`，分别由各自的安装记录管理。已有用户级版本时，用 `command -v zmcl` 核对 shell 实际选择的程序。不满足预编译包条件时，可按 [README 的源码构建步骤](../README.md#构建与运行) 构建并在目标系统验证。

## 本次 0.1.3 发行验收

[官方发行回执](validation/release-0.1.3-2026-11-20.json)记录草稿回下载、匿名公开下载、标签和 manifest 核验，以及用户级安装/卸载和修改文件保护。[AUR 回执](validation/aur-install-0.1.3-2026-11-20.json)记录作者包与公开新克隆包的各自身份。实际升级所用的新克隆包为 674,509 字节，SHA-256 `6c323e69cd82d51ebbb9b1524ba195f640ab0f04cf6c39aa88559a48f501cce2`。

[安装后窗口验收](validation/installed-gui-0.1.3-2026-11-20.json)使用 /usr/bin/zmcl 完成六组真实窗口与文件效果检查，包括确认恢复时保留快照及服务器 EULA 拒绝。没有新增真实账号、在线游戏下载或开服验收。namcap 提示的 curl/Python 依赖由外部命令实际使用，继续保留；这些本机检查不是所有 Arch 环境的干净 chroot 证明。

## 历史 0.1.2 发行验收

[AUR 与安装回执](validation/aur-install-0.1.2-2026-11-20.json)记录公开配方 `16b6882`、独立来源校验、准备包和公共新克隆包的各自身份。公共新克隆重试包为674,238字节，SHA-256 `17f8b87cd028fe1204088de52474054f4c0f5d47f3d07e999ea9ee4b44821867`。首轮因宿主磁盘空间不足解包失败，清理后使用已验证的同一公开归档重试通过，失败日志保留。

通过 pacman 正常升级0.1.1-1→0.1.2-1，安装后19个受管文件、0个改动，实际 `/usr/bin/zmcl` 摘要与上文一致。已安装程序通过版本选择、失败安装保留、仅创建、重名保护和账号页引导的四组[原生窗口夹具](validation/installed-gui-0.1.2-2026-11-20.json)。没有新增真实账号登录、在线游戏下载或游戏启动验收，也不据此宣称所有 Arch 环境或 Windows/macOS 应用已通过。

## 历史 0.1.1 发行验收

AUR 源码为 `15bdd71d63db740fadf13edbe9d876f2fb0f1bc1`；GitHub 对应配方提交为 [d7485de](https://github.com/LIghtJUNction/zmcl/commit/d7485defab8e8fa398a6fd272bad3ffcc183a7d7)。公开 HTTPS 新克隆后，源码校验、makepkg、namcap、桌面入口和文件布局检查通过。随后正常执行 pacman 升级0.1.0-1→0.1.1-1；升级前后均为19个受管文件、0个被改动。没有进行全系统升级。

发布后的首次 RPC/helper 查询仍命中旧版本缓存；2026-11-20 16:30:32（Asia/Taipei）复验，current/legacy/search RPC、`yay -Si` 和 `paru -Si` 均返回0.1.1-1。两轮记录均保留。namcap 提示的 curl/Python 依赖通过外部命令实际使用，因此保留声明。这些检查不是所有 Arch 环境的干净 chroot 兼容性证明。

0.1.1 当时安装的 `/usr/bin/zmcl` 为1,439,712字节，SHA-256 `a28998952b42d09f652ac95da47ddb15930cd556b6fa4e9b7e7ebbd23a4ac2e9`，与官方发行包完全一致。通过已安装的原生窗口创建1.21.1 Vanilla实例，CLI读回一致，管理页入口正常，退出后窗口清空；使用独立数据目录，未下载游戏、登录账号或启动服务器。见[窗口验收](validation/installed-gui-0.1.1-2026-11-20.json)。
