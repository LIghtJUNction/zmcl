# 安装 zmcl

## Arch Linux / AUR

[zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 已发布，当前版本为 **0.1.0-1 开发预览**。它安装 GitHub 官方 Linux x86_64 预编译程序，包含桌面入口与图标。

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

安装后在应用菜单中打开 **zmcl**，或执行 `zmcl gui`。`zmcl --version` 核对版本；`zmcl doctor --json` 检查运行条件。更新与卸载由包管理器管理：

~~~sh
yay -Syu
sudo pacman -R zmcl-bin
~~~

包管理器管理程序、桌面入口、图标、许可证和说明文件。安装、重装与卸载不清除用户的 Minecraft 实例、存档或账号资料；本轮使用独立 pacman 根目录和合成存档验证了这一点。证据见 [AUR 安装记录](validation/aur-install-2026-11-20.json)。

## 当前预览版的运行条件

| 条件 | 当前范围 |
| --- | --- |
| 平台 | Linux x86_64 |
| libc | glibc 2.43 或更新 |
| 原生窗口 | X11；Wayland 桌面需要 XWayland |
| 文件与下载任务 | 系统 curl、Python 3；按需启动后退出 |
| Microsoft 凭据 | libsecret 提供的 secret-tool，以及桌面 Secret Service |
| Java | 可使用系统 Java，或通过 zmcl 下载受管理的运行时 |
| Horizon 验签 | 可选 OpenSSL，需支持 Ed25519 |
| 字体 | 系统 TrueType/OpenType 字体 |

AUR 配方声明常规运行依赖，附加功能组件列于 `optdepends`。二进制的直接动态库依赖、glibc 下限和应用源码身份记录在包内 `/usr/share/doc/zmcl/release.json`。

**AUR 上的 0.1.0 仍是此前的开发预览，GUI 低于 10 MiB 是尚未完成的目标。** 可安装的发行渠道不代表功能已经覆盖 XMCL，或内存优化已经完成。能力范围见 [FEATURES.md](FEATURES.md)，内存条件见 [PERFORMANCE.md](PERFORMANCE.md)。

## 其他 Linux 系统

从 [GitHub v0.1.0](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.0) 下载明确版本的归档与校验文件。解包后可使用其中的用户级安装器：

~~~sh
python3 scripts/install.py install
# 卸载这一用户级安装
python3 scripts/install.py uninstall
~~~

用户级安装默认写入 `~/.local`，AUR 安装使用 `/usr`，分别由各自的安装记录管理。已有用户级版本时，使用 `command -v zmcl` 核对 shell 实际选择的程序。不满足当前预编译包 glibc 条件时，可按 [README 的源码构建步骤](../README.md#构建与运行) 构建，并在目标系统验证。

## 本次发行验收

2026-11-20，AUR 源码 `e1fd82532d8b8a8b9548386fda9fd6305fd232da` 与 GitHub 配方提交 `0a7dda0a582f00334a61a1c236cb9f50ca9a64ed` 逐文件匹配。公开 HTTPS 新克隆之后，源码校验、makepkg、namcap、安装布局执行和独立 pacman 往返均通过；AUR RPC、yay -Si、paru -Si 回读为 0.1.0-1。

独立 pacman 根目录使用 fakeroot，并为该空根跳过依赖检查；开发机上的 makepkg 已检查依赖。这是限定环境的安装验收，不能替代所有 Arch 环境的干净 chroot 测试。namcap 的 curl/Python 静态提示已核对：程序通过外部命令使用它们，因此保留声明。
