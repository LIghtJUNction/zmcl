# 安装 zmcl

## Arch Linux / AUR

[zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 当前为 **0.1.2-1 开发预览**，安装官方 Linux x86_64 预编译程序、桌面入口和图标。AUR 配方已发布，源码提交为 `16b688237f8c54b47085a16279535db8f6c5c553`。

发布后的首次 RPC、yay 和 paru 查询返回了缓存中的 **0.1.1-1**。2026-11-20 **18:44（Asia/Taipei）**复验，current/legacy RPC、yay 和 paru 均返回 **0.1.2-1**，与公开 Git 配方一致。也可使用下方的 Git 克隆与 makepkg 入口。

**0.1.2 的公共新克隆构建、正常升级和安装后窗口验收均已通过。** 开发机已安装 0.1.2-1，实际程序版本、大小和摘要与官方包一致。旧版安装回执继续保留原始身份。

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

0.1.2 的创建界面支持筛选官方发行版、自动建议不重复的实例名，以及“创建并安装”或“仅创建”。本版使用 zgpui 0.1.5；功能、固定源码和平台验收边界见[本轮报告](reports/2026-11-20-gui-iteration.md)。

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

**完整 GUI 低于 10 MiB 的目标仍未完成。** 本轮两组不同的固定预发行候选，分别观察到完整窗口重绘 CPU 时间中位数下降 44.53%，以及 2,000 个合成世界返回首页时 GUI PSS＋SwapPss 下降 23.05%。这些数值不是 0.1.2 最终发行二进制的新测量，也不代表操作进程树峰值下降。原 0.1.1 空库首页静置 RSS 约 9.55 MiB 的记录继续保留历史身份；详见[本轮报告](reports/2026-11-20-gui-iteration.md)与[完整测量记录](PERFORMANCE.md)。

## 其他 Linux 系统

从 [GitHub v0.1.2](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.2) 下载 [Linux x86_64 归档](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.2/zmcl-0.1.2-linux-x86_64.tar.gz)与 [SHA-256 校验文件](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.2/zmcl-0.1.2-linux-x86_64.tar.gz.sha256)。归档为 **679,358 字节**，SHA-256：

~~~text
5cb7945cb5888d1ec02d84e55a4b91576161ddb6fe6c3a6be2644c8f058fb3c4
~~~

其中的主程序为 **1,473,144 字节**，SHA-256：

~~~text
71bf4017072703a91359ea84e2493db218d9962456ece2060d0f2e42b3500c32
~~~

应用源码固定为 [e86b584](https://github.com/LIghtJUNction/zmcl/commit/e86b5841532ef04cc74cea5585ae396150452408)。发行包仅支持 Linux x86_64；Windows 完整 GUI 交叉构建已通过，应用原生运行仍待验收，macOS 应用构建和桌面流程也仍待验收。框架在其他平台的窗口探针通过，不等同于启动器应用的完整运行验收。

校验并解包后，可使用其中的用户级安装器：

~~~sh
python3 scripts/install.py install
# 卸载这一用户级安装
python3 scripts/install.py uninstall
~~~

用户级安装默认写入 `~/.local`，AUR 安装使用 `/usr`，分别由各自的安装记录管理。已有用户级版本时，用 `command -v zmcl` 核对 shell 实际选择的程序。不满足预编译包条件时，可按 [README 的源码构建步骤](../README.md#构建与运行) 构建并在目标系统验证。

## 本次 0.1.2 发行验收

[AUR 与安装回执](validation/aur-install-0.1.2-2026-11-20.json)记录公开配方 `16b6882`、独立来源校验、准备包和公共新克隆包的各自身份。公共新克隆重试包为674,238字节，SHA-256 `17f8b87cd028fe1204088de52474054f4c0f5d47f3d07e999ea9ee4b44821867`。首轮因宿主磁盘空间不足解包失败，清理后使用已验证的同一公开归档重试通过，失败日志保留。

通过 pacman 正常升级0.1.1-1→0.1.2-1，安装后19个受管文件、0个改动，实际 `/usr/bin/zmcl` 摘要与上文一致。已安装程序通过版本选择、失败安装保留、仅创建、重名保护和账号页引导的四组[原生窗口夹具](validation/installed-gui-0.1.2-2026-11-20.json)。没有新增真实账号登录、在线游戏下载或游戏启动验收，也不据此宣称所有 Arch 环境或 Windows/macOS 应用已通过。

## 历史 0.1.1 发行验收

AUR 源码为 `15bdd71d63db740fadf13edbe9d876f2fb0f1bc1`；GitHub 对应配方提交为 [d7485de](https://github.com/LIghtJUNction/zmcl/commit/d7485defab8e8fa398a6fd272bad3ffcc183a7d7)。公开 HTTPS 新克隆后，源码校验、makepkg、namcap、桌面入口和文件布局检查通过。随后正常执行 pacman 升级0.1.0-1→0.1.1-1；升级前后均为19个受管文件、0个被改动。没有进行全系统升级。

发布后的首次 RPC/helper 查询仍命中旧版本缓存；2026-11-20 16:30:32（Asia/Taipei）复验，current/legacy/search RPC、`yay -Si` 和 `paru -Si` 均返回0.1.1-1。两轮记录均保留。namcap 提示的 curl/Python 依赖通过外部命令实际使用，因此保留声明。这些检查不是所有 Arch 环境的干净 chroot 兼容性证明。

0.1.1 当时安装的 `/usr/bin/zmcl` 为1,439,712字节，SHA-256 `a28998952b42d09f652ac95da47ddb15930cd556b6fa4e9b7e7ebbd23a4ac2e9`，与官方发行包完全一致。通过已安装的原生窗口创建1.21.1 Vanilla实例，CLI读回一致，管理页入口正常，退出后窗口清空；使用独立数据目录，未下载游戏、登录账号或启动服务器。见[窗口验收](validation/installed-gui-0.1.1-2026-11-20.json)。
