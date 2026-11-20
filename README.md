# zmcl

基于 **Project Z** 的原生 Minecraft 启动器，目标是把启动器的常驻内存和安装体积压低，同时逐项补齐 XMCL 的功能。

当前开发预览已实现 Minecraft Java 版安装与启动、Modrinth 模组和整合包、资源包和光影包管理、Java 管理、存档与备份、实例回收站、本地服务器管理和 Horizon 官方启动器下载。**完整 XMCL 功能覆盖仍在开发中。** [功能记录](docs/FEATURES.md) 区分了已有代码、实际验证和仍缺少的能力，比较基线固定为 XMCL 0.73.1。

[项目网站](https://lightjunction.github.io/zmcl/) · [预览版下载](https://github.com/LIghtJUNction/zmcl/releases) · [实际验收](docs/VALIDATION.md) · [性能测量](docs/PERFORMANCE.md)

## 为什么体积可以小

启动计划由 Rust `no_std + alloc` 核心生成，JSON 使用 `zconv`，命令行使用 `zclap`，界面使用 Project Z 的 `zui-native`。界面按事件重绘，空闲时等待事件。受管理文件依据上游提供的摘要和大小校验；不可变文件放进共享内容库，再通过硬链接或复制提供给实例。

重型操作使用按需退出的系统程序，未把整套浏览器运行时装进启动器。因此，“Rust 依赖都来自 Project Z”不等于没有系统依赖：curl、Python、图形库、Java 和可选 OpenSSL 仍需明确计算。游戏内存、Java 安装、游戏资源和 Horizon 官方发行包不属于 zmcl 本身的可执行文件。

本轮 Linux GUI 主程序为 **1,412,176 字节（1.35 MiB）**。分发包、安装文件和内存使用分别测量，详见[性能报告](docs/PERFORMANCE.md)。GUI 的 `--exit-after-launch` 选项会在启动命令成功返回游戏 PID 后退出；已实际验证启动器退出后 Java 进程继续存活。命令行启动本身也无需保留常驻服务。

性能对照使用固定 XMCL 0.73.1、完整进程树、重复样本，并同时记录 RSS、PSS、SwapPss 和宿主内存压力。软件渲染结果不能替代硬件 GPU 实测，现阶段功能范围也不同。

目前 Cargo 包是 `zui-native`，Rust 导入名是 `zui`；`zgpui` 是另一个提供场景、布局和 Renderer 契约的 Project Z 库，当前未接入。库名与实际依赖关系见[架构说明](docs/ARCHITECTURE.md)。

## 构建与运行

需要 Git、固定的 Rust 1.100.0 工具链及 C 编译环境。Linux GUI 使用 X11/XWayland、OpenGL、FreeType 和 Fontconfig。Ubuntu/Debian 的开发包通常为 `libx11-dev libgl-dev libfreetype6-dev libfontconfig1-dev pkg-config`；系统发行版的运行时包名可能不同。

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/zmcl.git
cd zmcl
cargo build --release --locked
./target/release/zmcl gui --exit-after-launch
```

只有 CLI 时可用 `cargo build --release --locked --no-default-features`。启动器支持 `--root DIR` 或 `ZMCL_HOME` 选择数据目录；运行 `zmcl doctor` 查看系统程序与 Java 状态。默认数据目录为 Linux 的 XDG data/zmcl、macOS 的 Application Support/zmcl、Windows 的 LocalAppData/zmcl。

本轮真实游戏与桌面验收在 Linux x86_64 完成。当前预编译包的主程序引用到 `GLIBC_2.43`，因此需要 **glibc 2.43 或更新版本**及上述系统库；较旧发行版请按前面的步骤在本机从源码构建。这个符号要求只描述主程序的 ABI 下限，不能替代完整发行版兼容性验收。其他平台的实现分支和启动计划测试不等于对应系统已经通过真实桌面验收。

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

`scripts/package.py` 创建本地分发包，`scripts/install.py` 提供用户级安装和清单校验卸载。卸载不删除游戏数据，也不删除已被用户修改的安装文件。

本项目并非 Mojang/Microsoft 官方产品，也不是 XMCL 或 Horizon 的官方发行版。各上游项目和游戏资源保留各自许可。
