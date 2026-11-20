# 安装 zmcl

**0.1.4 开发预览提供 Linux x86_64 的 tar.gz、DEB，以及 Windows amd64 的 ZIP。** 按下表选择一种安装方式。macOS 暂无发行包；Java 和 Minecraft 内容由启动器另行安装。

## 选择下载

| 系统与用途 | 官方下载 | 文件大小 |
| --- | --- | ---: |
| Linux x86_64，安装到自己的目录 | [tar.gz 用户安装包](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.4/zmcl-0.1.4-linux-x86_64.tar.gz) | 700,244 字节 |
| 满足运行库要求的 Debian 系统，交给 apt 管理 | [DEB 软件包](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.4/zmcl-0.1.4-linux-x86_64.deb) | 571,108 字节 |
| Windows amd64，安装到自己的目录 | [ZIP 用户安装包](https://github.com/LIghtJUNction/zmcl/releases/download/v0.1.4/zmcl-0.1.4-windows-amd64.zip) | 670,411 字节 |

发行页为 [GitHub v0.1.4](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.4)，本次发行的源码固定为 [25c243a](https://github.com/LIghtJUNction/zmcl/commit/25c243a593b187b0f0835e740665e1fce96d3621)。三个下载包的 SHA-256 如下；校验通过后再解包或安装。

~~~text
8265e672ef7914362c1038b71ed382d18efafe49668d7791005713faa5f580f1  zmcl-0.1.4-linux-x86_64.tar.gz
c19afcdd4e567ffcb580f60fbe5812d6915c12b484e9751518c3f0b790c5b7b8  zmcl-0.1.4-linux-x86_64.deb
48bd7dc7ce0eb799fed225678b6a6d9c4ed518f7ee96482b2c5c1f0f516271cf  zmcl-0.1.4-windows-amd64.zip
~~~

AUR [zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 已公开 **0.1.4-1** 配方，源码为 `a769068256b4aa0803ab08b7f0691a8a4024a464`。Arch 用户可用 `paru -S zmcl-bin` 或 `yay -S zmcl-bin` 安装，再用 `zmcl --version` 核对实际程序。此处已确认公开配方；公开新克隆构建、本机升级与已安装界面验收已通过，真实官方更新查询返回当前 0.1.4。0.1.3 及更早版本的安装与验证记录保留在文末历史区。

## 先检查运行条件

| 条件 | Linux x86_64 预编译包 |
| --- | --- |
| libc | glibc 2.39 或更新 |
| 字体库与字体 | Fontconfig 2.17.0 或更新、FreeType、系统 TrueType/OpenType 字体 |
| 窗口系统 | X11；Wayland 桌面需要 XWayland |
| 下载与文件任务 | curl、CA 证书、Python 3.8 或更新 |
| Microsoft 凭据 | 可选的 `libsecret-tools`／`secret-tool` 与桌面 Secret Service |
| Horizon 验签 | 可选 OpenSSL，需支持 Ed25519 |

Linux 默认使用 CPU 绘制，预编译程序没有直接 OpenGL 依赖。glibc 与 Fontconfig 下限来自实际 ELF 符号引用；它们同样适用于 DEB。**DEB 已在 Debian forky/sid 容器验收，不能据此视为兼容任意 Debian 稳定版或 Ubuntu 版本。** 不满足依赖时，请使用适合目标系统的源码构建，不要强制忽略包依赖。

Windows ZIP 需要 amd64 系统和 Python 3.8 或更新；下面使用 Python launcher 的 `py -3`。若该命令不可用，先检查已安装 Python 的版本，再用相应的 `python` 命令替代。Windows 原生构建、安装和窗口验收在 build 26100 的 runner 上通过，尚未覆盖所有 Windows 版本，也未在本轮验证 Windows 的真实游戏或账号流程。

## Linux：安装到自己的目录

下载 tar.gz 后，在其所在目录核对摘要并解包：

~~~sh
printf '%s  %s\n' \
  '8265e672ef7914362c1038b71ed382d18efafe49668d7791005713faa5f580f1' \
  'zmcl-0.1.4-linux-x86_64.tar.gz' | sha256sum -c -
tar -xzf zmcl-0.1.4-linux-x86_64.tar.gz
cd zmcl-0.1.4-linux-x86_64
python3 scripts/install.py install --prefix "$HOME/.local"
"$HOME/.local/bin/zmcl" --version
"$HOME/.local/bin/zmcl" gui
~~~

默认前缀也是 `~/.local`。想使用另一目录时，将安装、启动、更新和卸载命令中的前缀保持一致；有空格的目录应始终加引号：

~~~sh
python3 scripts/install.py install --prefix "$HOME/Applications/zmcl portable"
"$HOME/Applications/zmcl portable/bin/zmcl" gui
~~~

安装器完成后会显示版本、安装位置、直接启动命令和更新方式。它不会改写 PATH 或 shell 配置。直接使用安装器给出的完整路径，即可打开本次安装的程序。

## Debian：使用 apt 管理

下载 DEB 后先校验，再让 apt 检查并安装实际依赖：

~~~sh
printf '%s  %s\n' \
  'c19afcdd4e567ffcb580f60fbe5812d6915c12b484e9751518c3f0b790c5b7b8' \
  'zmcl-0.1.4-linux-x86_64.deb' | sha256sum -c -
dpkg-deb -f ./zmcl-0.1.4-linux-x86_64.deb Package Version Architecture Depends
sudo apt install --no-install-recommends ./zmcl-0.1.4-linux-x86_64.deb
/usr/bin/zmcl --version
/usr/bin/zmcl gui
~~~

`ca-certificates` 是显式依赖。验收使用原本没有 CA 证书的最小 Debian 镜像，以上不安装推荐包的方式仍会自动安装必需证书；随后严格验证 GitHub HTTPS 和真实手动更新查询均通过。实际安装结果与边界见[0.1.4 发行报告](reports/2026-11-20-zdistro.md)。

今后下载新版 DEB，校验后用相同的 `apt install ./文件名.deb` 升级。卸载使用：

~~~sh
sudo apt remove zmcl
~~~

apt 管理系统程序和附带文件，用户的实例、存档、下载缓存和账号资料不属于该软件包的文件清单。

## Windows：安装到自己的目录

在 PowerShell 中进入 ZIP 所在目录。先将结果与上面的 Windows SHA-256 核对，再解包和安装：

~~~powershell
Get-FileHash .\zmcl-0.1.4-windows-amd64.zip -Algorithm SHA256
Expand-Archive .\zmcl-0.1.4-windows-amd64.zip -DestinationPath .
Set-Location .\zmcl-0.1.4-windows-amd64
py -3 scripts/install.py install --prefix "$env:LOCALAPPDATA\Programs\zmcl"
& "$env:LOCALAPPDATA\Programs\zmcl\bin\zmcl.exe" --version
& "$env:LOCALAPPDATA\Programs\zmcl\bin\zmcl.exe" gui
~~~

安装器会保留 `bin\zmcl.exe` 的扩展名，完整路径可直接运行。自定义目录同样需要加引号。ZIP 安装器不修改系统 PATH；不要依靠另一份同名程序来判断本次安装是否成功。

## 命令打开了旧版本时

安装器会检查当前 PATH。如果 PATH 先找到另一份 `zmcl`，完成提示会同时列出那份程序和本次安装程序的位置，并给出本次程序的直接启动命令。它不会删除另一份安装。

Linux 可用 `command -v zmcl`，PowerShell 可用 `Get-Command zmcl -All` 查看命令选择。之后用完整路径运行 `--version`，并在 GUI 的 **Settings 页顶部版本信息卡片**核对当前版本、可执行文件位置和安装来源。若选择了不同前缀，更新时也应指向那个前缀。

## 手动检查和安装更新

打开 **Settings → Check for updates**，启动器才会查询官方 GitHub 发行信息。页面会区分有新版本、已经最新，以及本机版本比线上版本新；网络失败可以重试。找到更新后可打开官方发行页；打开失败时保留结果与链接提示。

检查更新不会替换正在运行的程序。关闭 zmcl 后，按安装来源完成更新：

- tar.gz 或 ZIP：下载并校验新版，在新版解包目录运行安装器，使用与原来相同的 `--prefix`。
- DEB：用 apt 安装新版 DEB。
- AUR：确认公开配方版本后，按包管理器流程更新。

安装器依据受管文件清单和摘要检查旧文件。如果程序、图标、桌面文件或安装信息文件已被修改，会拒绝覆盖并说明冲突；先备份和处理该文件，再重试。不要通过删除安装清单来绕过保护。正常升级不清除已有实例、存档、Java、下载缓存或账号资料。

## 用户安装的卸载与信息文件

保留解包目录中的安装器，使用安装时的同一前缀卸载：

~~~sh
python3 scripts/install.py uninstall --prefix "$HOME/.local"
~~~

~~~powershell
py -3 scripts/install.py uninstall --prefix "$env:LOCALAPPDATA\Programs\zmcl"
~~~

卸载只删除这次安装清单中摘要仍匹配的受管文件。修改过的受管文件、非受管文件以及游戏数据会保留；目录内有其他文件时不会整目录删除。

`<prefix>/share/zmcl/installation.json` 记录版本、便携安装渠道、前缀、可执行文件位置和官方发行页；`distribution.json` 说明 zdistro 的打包格式与身份。`install.json` 继续使用旧版兼容的严格列表格式，记录每个受管文件的路径和摘要，新安装信息文件也纳入保护。GUI 会校验来源信息；无法验证来源时显示未知／源码构建，不凭目录名猜测安装来源。旧合法格式仍可安装，无法验证版本的旧包会明确显示未知。

## 0.1.4 验收范围

[发行报告](reports/2026-11-20-zdistro.md)与[结构化回执](validation/zdistro-release-0.1.4.json)记录三个资产的身份、安装和更新流程。Linux 在 Debian forky/sid 容器中通过实际 0.1.3 → 0.1.4 升级、重装、卸载；用户安装通过带空格前缀、PATH 被旧程序遮蔽、九组原生 GUI 夹具和数据保留检查。正式发布前的一次真实更新查询正确识别本机 0.1.4 高于线上 0.1.3，查询前后二进制未改变。

[Windows 原生 workflow 44739704182](https://github.com/LIghtJUNction/zmcl/actions/runs/44739704182)在 build 26100 runner 上通过 MSVC 构建、ZIP 包、私有目录安装、`--version`、Win32 窗口、五项受管文件与卸载数据保留检查。两平台的窗口及文件夹具不等同于真实账号登录、游戏下载或实际游戏游玩。

| 主程序 | 文件大小 | SHA-256 |
| --- | ---: | --- |
| Linux ELF | 1,504,616 字节 | `586968d15c5187c0223d5223294ddcba585789d93934e6a37a3c7e1c516a3615` |
| Windows PE | 1,382,912 字节 | `33164565b43bf2f9db63da8e61db2a211318ca2fa682fd29ef988e910612910f` |

文件大小不含系统库、Java、游戏和缓存，也不是运行内存。完整 GUI 低于 10 MiB 的目标仍未完成；既有测量保留原版本与场景，见[性能记录](PERFORMANCE.md)。

## 历史发行与验收记录

<details>
<summary>0.1.3 及更早版本：原始下载、AUR 安装与验收记录</summary>

以下保留当时的说明与原始版本；其中“当前”“本次”均指该历史发行。

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

</details>
