# zmcl

基于 **Project Z** 的原生 Minecraft 启动器，目标是把启动器的常驻内存和安装体积压低，同时逐项补齐 XMCL 的功能。

当前开发预览已实现 Minecraft Java 版安装与启动、Modrinth 模组和整合包、资源包和光影包管理、Java 管理、存档与备份、实例回收站、本地服务器管理和 Horizon 官方启动器下载。**完整 XMCL 功能覆盖仍在开发中。** [功能记录](docs/FEATURES.md) 区分了已有代码、实际验证和仍缺少的能力，比较基线固定为 XMCL 0.73.1。

[项目网站](https://lightjunction.github.io/zmcl/) · [预览版下载](https://github.com/LIghtJUNction/zmcl/releases) · [实际验收](docs/VALIDATION.md) · [性能测量](docs/PERFORMANCE.md)

## 下载与安装

从 [GitHub Releases](https://github.com/LIghtJUNction/zmcl/releases) 选择已发布版本，并按发行页实际列出的平台资产下载。每个版本的文件摘要、运行条件和原生验收范围随发行记录提供。

| 使用环境 | 软件包 | 安装方式 |
| --- | --- | --- |
| Linux x86_64，用户级安装 | tar.gz | 解包后运行 Python 安装器，自行选择安装位置 |
| Debian 系 Linux x86_64 | DEB | 确认运行库满足要求后，交给 apt 管理 |
| Arch Linux x86_64 | AUR 的 zmcl-bin | 使用 yay 或 paru，以 AUR 当前配方版本为准 |
| Windows amd64 | ZIP | 查看对应发行的原生 Windows 验收记录与安装说明 |

Linux 预编译程序要求 **glibc 2.39+、Fontconfig 2.17.0+、FreeType 和 X11/XWayland**。文件与下载操作使用 curl、系统 CA 证书和 Python 3.8+；Java 与 Minecraft 单独安装。DEB 的具体依赖由二进制符号和发行元数据生成，安装前应核对目标系统的库版本。较旧的 Debian 或 Ubuntu 需要相应运行库，或在目标系统从源码构建。本项目暂未提供 macOS 下载包。

### Linux 便携包

校验并解包后，在包含 `scripts/install.py` 的包根目录运行：

~~~sh
python3 scripts/install.py install --prefix "$HOME/.local"
"$HOME/.local/bin/zmcl" gui
~~~

`--prefix` 接受含空格的路径，例如 `--prefix "$HOME/Applications/zmcl preview"`。安装完成后会显示实际版本、安装位置、直接启动命令和更新方法；如果 PATH 中的 `zmcl` 指向另一份程序，也会同时显示两处路径。使用完成提示中的完整路径即可启动本次安装。

从新版本解包目录再次运行安装器，并传入相同的 `--prefix`，即可升级这份安装。卸载也使用相同前缀：

~~~sh
python3 scripts/install.py uninstall --prefix "$HOME/.local"
~~~

安装器依据文件清单与摘要处理程序、图标、桌面入口和安装信息。用户修改过的文件、非受管文件以及 Minecraft 实例、存档和账号资料会保留。详细步骤见[安装说明](https://github.com/LIghtJUNction/zmcl/blob/main/docs/INSTALL.md)。

### Arch Linux

[AUR 包 zmcl-bin](https://aur.archlinux.org/packages/zmcl-bin) 提供预编译程序、桌面入口与图标：

~~~sh
yay -S zmcl-bin
# 或 paru -S zmcl-bin
~~~

AUR 与用户级安装分别由各自的安装记录管理。用 `zmcl --version` 核对 PATH 实际选中的版本；包管理器安装的副本继续通过同一包管理器更新。

### 查看当前安装与检查更新

**Settings** 展示当前程序版本、平台、位置和安装来源。点击 **Check for updates** 手动查询官方 GitHub 发行，包括开发预览，并检查是否有适合当前操作系统和架构的软件包。失败后可重试，也可打开或复制官方发行链接。当前构建比线上软件包更新时，会显示 **Newer build installed**。

确认版本后，按原安装方式更新：便携安装重新运行同前缀安装器，DEB 与 AUR 使用对应包管理器。缺少有效来源信息的程序会显示 **Unknown / source build**。也可从命令行检查：

~~~sh
zmcl update check --json
~~~

## 为什么体积可以小

启动计划由 Rust `no_std + alloc` 核心生成，JSON 使用 `zconv`，命令行使用 `zclap`，主线界面使用 Project Z 的 `zgpui::native`。Linux 默认采用 CPU 栅格化和 X11 提交，不创建 OpenGL 上下文。界面按事件重绘，空闲时等待事件。受管理文件依据上游提供的摘要和大小校验；不可变文件放进共享内容库，再通过硬链接或复制提供给实例。

重型操作使用按需退出的系统程序，未把整套浏览器运行时装进启动器。因此，“Rust 依赖都来自 Project Z”不等于没有系统依赖：curl、Python、图形库、Java 和可选 OpenSSL 仍需明确计算。游戏内存、Java 安装、游戏资源和 Horizon 官方发行包不属于 zmcl 本身的可执行文件。

已发布 v0.1.3 的 Linux GUI 主程序为 **1,473,752 字节（1.41 MiB）**。分发包、安装文件和内存使用分别测量，详见[性能报告](docs/PERFORMANCE.md)。GUI 的 `--exit-after-launch` 选项会在启动命令成功返回游戏 PID 后退出；已实际验证启动器退出后 Java 进程继续存活。命令行启动本身也无需保留常驻服务。

性能对照使用固定 XMCL 0.73.1、完整进程树、重复样本，并同时记录 RSS、PSS、SwapPss 和宿主内存压力。软件渲染结果不能替代硬件 GPU 实测，现阶段功能范围也不同。

主线已接入正式发布的 `zgpui` 0.1.5：实际窗口、输入和绘制调用来自 `zgpui::native`，启用 `native` 和 `linux-software`，不保留旧包别名。Library 的 Manage 入口提供世界、备份与资源管理，侧栏提供本地服务器管理。新建实例支持异步官方版本选择、自动避开重名、五种加载器和默认创建后安装。隐藏 Activity 不再为每段日志触发重绘，离开管理页会释放其列表内容与容量。九个页面标题已通过 `zi18n` 借用静态英文，完整多语言仍待后续。

0.1.2 迭代的限定测试中，空库重绘 CPU 时间中位数下降 **44.5%**；2000 个世界的两对交错测试中，返回 Library 后 GUI 的 PSS+SwapPss 下降 **23.0%**，RSS 下降 **14.5%**。两项来自不同冻结候选，不能相加，也不代表最终发行二进制在所有状态的结果。完整进程树、显示服务与换页边界见[本轮报告](docs/reports/2026-11-20-gui-iteration.md)。源码能力与当前 AUR 发行版本分别记录，详见[架构说明](docs/ARCHITECTURE.md)和[功能记录](docs/FEATURES.md)。

0.1.3 继续减少管理助手的临时分配。在相同系统 Python、两份各含 10000 个文件的备份夹具中，对比已经完成按需导入的上一候选，三轮完整 CLI 树的最大观测 RSS 为 45,692 → 35,368 KiB、PSS 为 35,099 → 24,824 KiB。最终 GUI 管理全程的完整树观测峰值仍为默认环境 RSS/PSS 59,884/48,651 KiB，明确指定系统 Python 时为 37,948/19,306 KiB；后者是环境条件变化。缺读、主进程、显示服务和观察成本均保留，**完整 GUI 低于 10 MiB 仍未完成**。[0.1.3 报告与安装证据](docs/reports/2026-11-20-helper-memory.md)

## 构建与运行

需要 Git、固定的 Rust 1.100.0 工具链及 C 编译环境。当前 Linux GUI 使用 X11/XWayland、FreeType 和 Fontconfig。Ubuntu/Debian 的开发包通常为 `libx11-dev libfreetype6-dev libfontconfig1-dev pkg-config`；系统发行版的运行时包名可能不同。

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/zmcl.git
cd zmcl
cargo build --release --locked
./target/release/zmcl gui --exit-after-launch
```

只有 CLI 时可用 `cargo build --release --locked --no-default-features`。启动器支持 `--root DIR` 或 `ZMCL_HOME` 选择数据目录；运行 `zmcl doctor` 查看系统程序与 Java 状态。文件助手要求 Python 3.8+；Windows 会尝试 `py -3`、`python`、`python3`，其他平台尝试 `python3`、`python`。`ZMCL_PYTHON` 可指定一个解释器可执行文件路径，含空格的路径作为单个参数处理。默认数据目录为 Linux 的 XDG data/zmcl、macOS 的 Application Support/zmcl、Windows 的 LocalAppData/zmcl。

已记录的真实游戏与桌面验收在 Linux x86_64 完成。预编译包的 glibc 和 Fontconfig 下限由实际 ELF 符号生成，见包内 release.json；源码支持的条件分支不代表预编译程序兼容同样的旧运行库。较旧发行版可在本机构建，并验证运行条件。符号要求不能替代完整发行版兼容性验收；Windows GNU 完整 GUI 已正常跨编译；zgpui 0.1.5 的 Windows/macOS 原生探针已在对应系统运行。zmcl 的原生 Windows 构建、安装、窗口和卸载由独立发行工作流记录实际结果；请查看对应版本的验收范围。macOS 应用流程与 Windows/macOS 的真实游戏启动仍待验收。

## 常用操作

以下示例中的 `zmcl` 可替换为 `./target/release/zmcl`。

```sh
zmcl versions
zmcl instance create demo --version 1.21.1 --loader fabric
zmcl instance set demo --memory 2048
zmcl install demo
zmcl mods search modmenu --version 1.21.1 --loader fabric
zmcl mods add demo modmenu
zmcl mods update demo
zmcl mods disable demo modmenu
zmcl mods enable demo modmenu
zmcl launch demo --offline Player
```

离线名称是显式选择的单人游戏身份。Microsoft 设备登录使用自己的已注册应用 Client ID 和操作系统凭据库：

```sh
zmcl auth login --client-id YOUR_REGISTERED_CLIENT_ID
zmcl account list
zmcl launch demo --account YOUR_PROFILE_UUID
```

真实正版账号的完整登录链路需要注册应用和用户登录，本轮未以真实账号验收。不会把刷新令牌退回明文文件保存。

Mod 更新会重新检查必需依赖与已声明冲突，完成下载后再提交。`mods remove` 拒绝移除仍被依赖的项目；`mods recover` 用带身份校验的事务记录恢复中断操作，无法确认归属时保留现场。

### Java、存档与实例

```sh
zmcl java list
zmcl java install 21
zmcl world list demo
zmcl world import demo /path/to/world "My World"
zmcl world export demo "My World" /path/to/world.zip
zmcl backup create demo before-upgrade
zmcl backup list demo
zmcl backup restore demo BACKUP_ID
zmcl instance clone demo demo-copy
zmcl install demo-copy
zmcl instance trash demo-copy
zmcl instance trash-list
zmcl instance restore TRASH_ID
```

安装游戏时会选择所需 Java 主版本；未找到且没有显式 `--java` 配置时，自动下载并验证官方 Temurin JRE。本轮已实际安装 Java 21，并为 Minecraft 26.3 自动补齐 Java 25。`launch` 仅使用已有运行时。

克隆会独立复制可变游戏文件，并要求重新执行 install 来关联共享运行文件。移入回收站不会删除共享内容，恢复不会覆盖同名实例。存档导入和 ZIP 导出拒绝覆盖已有目标，备份恢复会先保留恢复前快照。这些修改操作会检查运行状态和未完成事务；实例运行时不会提交配置或游戏文件变更。

### 资源包与光影包

```sh
zmcl resources search resourcepack "No Pumpkin Blur" --version 1.21.1
zmcl resources add demo resourcepack no-pumpkin-blur
zmcl resources search shader "Potato" --version 1.21.1
zmcl resources add demo shader terders-potato-shader
zmcl resources list demo
zmcl resources update demo
zmcl resources disable demo resourcepack no-pumpkin-blur
zmcl resources history demo
zmcl resources restore demo REVISION
```

资源文件校验摘要、大小与 ZIP 结构，使用独立锁文件记录归属。启用与禁用控制游戏能否发现文件，游戏内选择保持由玩家完成；光影所需的 Iris/OptiFine 等引擎要另行安装。更新和删除保留恢复历史，尚未提供历史自动清理策略。

### 整合包与本地服务器

```sh
zmcl pack search "Simply Optimized" --version 1.21.1
zmcl pack install sop-continued pack-demo --pack-version TAU7qd8y
zmcl pack import /path/to/pack.mrpack imported-demo
zmcl install imported-demo
zmcl pack export pack-demo /path/to/export.mrpack
zmcl server create local-test --version 1.21.1
zmcl server install local-test
zmcl server status local-test
zmcl server ping 127.0.0.1 25565
```

在线整合包安装会记录精确项目、版本和来源。`.mrpack` 支持 Vanilla、Fabric、Quilt、Forge、NeoForge 的 loader 字段；其下载校验 SHA-1 与大小，导出时计算 SHA-1 和 SHA-512。CurseForge/FTB 等其他格式仍未实现，详见功能对照。

本地服务器默认绑定回环地址，并保留 `eula=false`。运行服务器前由用户阅读并设置其 EULA；启动器不代替用户接受。Linux 的启动/停止会核对进程身份，其他平台的进程管理仍待实机验证。

### Horizon

```sh
zmcl horizon versions
zmcl horizon install
zmcl horizon list
zmcl horizon launch INSTALL_ID
```

这里管理的是 [Horizon 官方项目](https://github.com/horizon-rust/horizon) 的 **launcher 组件**，不是可脱离官方资源准备流程独立运行的裸 game 包。发行包核对官方 Ed25519 签名、SHA-256 和平台；安装版本并存，数据放在独立 provider 目录。官方启动器负责正版身份与 Minecraft 26.3 资源准备，Horizon 不能直接运行 Fabric/Forge/NeoForge 的 Java 模组。签名验证需要支持 Ed25519 的系统 OpenSSL。

## 文档与验证

- [架构和边界](docs/ARCHITECTURE.md)
- [XMCL 功能对照与验收记录](docs/FEATURES.md)
- [0.1.3 AUR、管理助手内存与安装后验收](docs/reports/2026-11-20-helper-memory.md)
- [0.1.2 GUI、跨平台与性能迭代](docs/reports/2026-11-20-gui-iteration.md)
- [实际安装、启动与恢复验收](docs/VALIDATION.md)
- [资源占用及测量方法](docs/PERFORMANCE.md)
- [维护与传播规则](AGENTS.md)
- [GitHub Pages 站点源码](site/README.md)

```sh
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
python3 -B src/content/test_archive.py
python3 -B src/java/test_extract.py
python3 -B src/server/test_process.py
python3 -B src/backup/test_archive.py
python3 -B src/worlds/test_manage.py
python3 -B src/horizon/test_archive.py
python3 -B scripts/test_bench_linux.py
```

发行端通过固定版本的 `zdistro` 和 `scripts/zdistro_package.py` 生成并校验软件包；使用方法见[分发说明](https://github.com/LIghtJUNction/zmcl/blob/main/docs/DISTRIBUTION.md)。`scripts/install.py` 提供用户级安装和清单校验卸载，保留游戏数据和用户修改过的安装文件。

本项目并非 Mojang/Microsoft 官方产品，也不是 XMCL 或 Horizon 的官方发行版。各上游项目和游戏资源保留各自许可。
