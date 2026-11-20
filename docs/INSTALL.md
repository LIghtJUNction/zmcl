# 安装 zmcl

## Arch Linux / AUR

[zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 已发布 **0.1.1-1 开发预览**，安装官方 Linux x86_64 预编译程序、桌面入口和图标。公开 AUR Git、三种官方 RPC 查询、yay 和 paru 已回读到该版本，实际安装与窗口记录见[发行验收](validation/aur-install-0.1.1-2026-11-20.json)。

~~~sh
yay -S zmcl-bin
# 或
paru -S zmcl-bin
~~~

不使用 AUR helper 时：

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

包管理器管理程序、桌面入口、图标、许可证和说明文件，不管理用户的 Minecraft 实例、存档和账号资料。此前0.1.0的隔离安装/重装/卸载及合成存档保护检查保留在[历史记录](validation/aur-install-2026-11-20.json)；本次0.1.1另外验证了开发机的正常升级及已安装窗口。

## 当前预览版的运行条件

| 条件 | 当前范围 |
| --- | --- |
| 平台 | Linux x86_64 |
| libc | glibc 2.39 或更新 |
| 字体库 | Fontconfig 2.17.0 或更新、FreeType |
| 原生窗口 | X11；Wayland 桌面需要 XWayland |
| 文件与下载任务 | 系统 curl、Python 3；按需启动，成本仍计入应用进程树 |
| Microsoft 凭据 | libsecret 提供的 secret-tool，以及桌面 Secret Service |
| Java | 系统 Java，或通过 zmcl 下载受管理的运行时 |
| Horizon 验签 | 可选 OpenSSL，需支持 Ed25519 |
| 字体 | 系统 TrueType/OpenType 字体 |

这些 ABI 下限来自已发布 ELF 的实际符号引用。AUR 配方声明相应依赖，附加功能组件列于 `optdepends`。完整版本、直接动态库、源码和文件身份保存在 `/usr/share/doc/zmcl/release.json`；本版直接 ELF 不依赖 OpenGL。源码在其他版本字体库上的兼容回退，不代表这个预编译包可忽略 Fontconfig 下限。

**完整 GUI 低于 10 MiB 的目标仍未完成。** 本版空库首页静置 RSS 中位数为9.55 MiB，但启动、重绘和管理操作会超过目标；功能和测量条件分别见 [FEATURES.md](FEATURES.md)、[PERFORMANCE.md](PERFORMANCE.md)。

## 其他 Linux 系统

从 [GitHub v0.1.1](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.1) 下载明确版本的归档与校验文件。当前 Linux x86_64 归档为664,850字节，SHA-256：

~~~text
c61a83ce918d7fe3d58a080bc41bc1c5f301c259396cc4a96a5acf81c3ee9f0b
~~~

校验并解包后，可使用其中的用户级安装器：

~~~sh
python3 scripts/install.py install
# 卸载这一用户级安装
python3 scripts/install.py uninstall
~~~

用户级安装默认写入 `~/.local`，AUR 安装使用 `/usr`，分别由各自的安装记录管理。已有用户级版本时，用 `command -v zmcl` 核对 shell 实际选择的程序。不满足预编译包条件时，可按 [README 的源码构建步骤](../README.md#构建与运行) 构建并在目标系统验证。

## 本次发行验收

AUR 源码为 `15bdd71d63db740fadf13edbe9d876f2fb0f1bc1`；GitHub 对应配方提交为 [d7485de](https://github.com/LIghtJUNction/zmcl/commit/d7485defab8e8fa398a6fd272bad3ffcc183a7d7)。公开 HTTPS 新克隆后，源码校验、makepkg、namcap、桌面入口和文件布局检查通过。随后正常执行 pacman 升级0.1.0-1→0.1.1-1；升级前后均为19个受管文件、0个被改动。没有进行全系统升级。

发布后的首次 RPC/helper 查询仍命中旧版本缓存；2026-11-20 16:30:32（Asia/Taipei）复验，current/legacy/search RPC、`yay -Si` 和 `paru -Si` 均返回0.1.1-1。两轮记录均保留。namcap 提示的 curl/Python 依赖通过外部命令实际使用，因此保留声明。这些检查不是所有 Arch 环境的干净 chroot 兼容性证明。

实际 `/usr/bin/zmcl` 为1,439,712字节，SHA-256 `a28998952b42d09f652ac95da47ddb15930cd556b6fa4e9b7e7ebbd23a4ac2e9`，与官方发行包完全一致。通过已安装的原生窗口创建1.21.1 Vanilla实例，CLI读回一致，管理页入口正常，退出后窗口清空；使用独立数据目录，未下载游戏、登录账号或启动服务器。见[窗口验收](validation/installed-gui-0.1.1-2026-11-20.json)。
