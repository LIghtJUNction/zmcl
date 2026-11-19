# zmcl

基于 **Project Z** 的原生 Minecraft 启动器，目标是把启动器的常驻内存和安装体积压低，同时逐项补齐 XMCL 的功能。

当前可以完成 Minecraft Java 版安装、Fabric 模组依赖安装和实际游戏启动；已加入 Java 管理、存档与备份、实例回收站、本地服务器管理和 Horizon 官方启动器下载。**完整 XMCL 功能覆盖仍在开发中。** [功能记录](docs/FEATURES.md) 区分了已有代码、实际验证和仍缺少的能力，比较基线固定为 XMCL 0.73.1。

## 为什么体积可以小

启动计划由 Rust `no_std + alloc` 核心生成，JSON 使用 `zconv`，命令行使用 `zclap`，界面使用 Project Z 的 `zui-native`。界面按事件重绘，空闲时等待事件。每项下载先核对预期摘要和大小；不可变文件放进共享内容库，再通过硬链接或复制提供给实例。

重型操作使用按需退出的系统程序，未把整套浏览器运行时装进启动器。因此，“Rust 依赖都来自 Project Z”不等于没有系统依赖：curl、Python、图形库、Java 和可选 OpenSSL 仍需明确计算。游戏内存、Java 安装、游戏资源和 Horizon 官方发行包不属于 zmcl 本身的可执行文件。

资源占用正在用真实程序、完整进程树和相同可见窗口条件测量。软件渲染结果不能替代硬件 GPU 实测，也不能用一个小可执行文件推导所有功能都只占同样的内存。

## 构建与运行

需要 Git、固定的 Rust 1.100.0 工具链及 C 编译环境。Linux GUI 使用 X11/XWayland、OpenGL、FreeType 和 Fontconfig。Ubuntu/Debian 的开发包通常为 `libx11-dev libgl-dev libfreetype6-dev libfontconfig1-dev pkg-config`；系统发行版的运行时包名可能不同。

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/zmcl.git
cd zmcl
cargo build --release --locked
./target/release/zmcl gui
```

只有 CLI 时可用 `cargo build --release --locked --no-default-features`。启动器支持 `--root DIR` 或 `ZMCL_HOME` 选择数据目录；运行 `zmcl doctor` 查看系统程序与 Java 状态。默认数据目录为 Linux 的 XDG data/zmcl、macOS 的 Application Support/zmcl、Windows 的 LocalAppData/zmcl。

本轮真实游戏与桌面验收在 Linux x86_64 完成。其他平台的实现分支和启动计划测试不等于对应系统已经通过真实桌面验收。

## 常用操作

以下示例中的 `zmcl` 可替换为 `./target/release/zmcl`。

```sh
zmcl versions
zmcl instance create demo --version 1.21.1 --loader fabric
zmcl instance set demo --memory 2048
zmcl install demo
zmcl mods search modmenu --version 1.21.1 --loader fabric
zmcl mods add demo modmenu
zmcl launch demo --offline Player
```

离线名称是显式选择的单人游戏身份。Microsoft 设备登录使用自己的已注册应用 Client ID 和操作系统凭据库：

```sh
zmcl auth login --client-id YOUR_REGISTERED_CLIENT_ID
zmcl account list
zmcl launch demo --account YOUR_PROFILE_UUID
```

真实正版账号的完整登录链路需要注册应用和用户登录，本轮未以真实账号验收。不会把刷新令牌退回明文文件保存。

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

克隆会独立复制可变游戏文件，并要求重新执行 install 来关联共享运行文件。移入回收站不会删除共享内容，恢复不会覆盖同名实例。存档导入和 ZIP 导出拒绝覆盖已有目标，备份恢复会先保留恢复前快照。修改运行中的实例会被拒绝。

### 整合包与本地服务器

```sh
zmcl pack import /path/to/pack.mrpack pack-demo
zmcl install pack-demo
zmcl pack export pack-demo /path/to/export.mrpack
zmcl server create local-test --version 1.21.1
zmcl server install local-test
zmcl server status local-test
zmcl server ping 127.0.0.1 25565
```

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
```

`scripts/package.py` 创建本地分发包，`scripts/install.py` 提供用户级安装和清单校验卸载。卸载不删除游戏数据，也不删除已被用户修改的安装文件。

本项目并非 Mojang/Microsoft 官方产品，也不是 XMCL 或 Horizon 的官方发行版。各上游项目和游戏资源保留各自许可。
