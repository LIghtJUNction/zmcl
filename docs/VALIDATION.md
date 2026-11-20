# zmcl 开发与验收报告

报告日期：2026-11-20（Asia/Taipei）。原始执行记录使用 UTC。
代码基线：[`033776287ab10aab90f46fd5b65309fecb9b8711`](https://github.com/LIghtJUNction/zmcl/commit/033776287ab10aab90f46fd5b65309fecb9b8711)。
产品范围：Linux x86_64 开发预览；XMCL 功能对照固定为 [v0.73.1](https://github.com/Voxelum/x-minecraft-launcher/releases/tag/v0.73.1)。

## 1. 本轮交付

zmcl 已成为可运行的 Project Z 原生 Minecraft 启动器，完成了实际游戏下载、Java 管理、模组与整合包管理、资源包与光影文件管理、存档与备份、实例回收恢复、本地服务器下载管理，以及 Horizon 官方启动组件下载。

**完整覆盖 XMCL 仍是未完成的目标。** 本报告把实现、测试夹具、真实安装、真实主菜单和未验收范围分开记录；不以一个小程序体积或几张截图代替功能覆盖结论。逐项状态见[功能对照](FEATURES.md)。

交付入口：

- [源代码](https://github.com/LIghtJUNction/zmcl)
- [项目网站](https://lightjunction.github.io/zmcl/)
- [开发预览分发包](https://github.com/LIghtJUNction/zmcl/releases)
- [完整性能方法与原始记录](PERFORMANCE.md)
- [架构及数据边界](ARCHITECTURE.md)

## 2. 体积与内存

最终 Linux GUI 可执行文件为 **1,412,176 字节，即 1.35 MiB**。SHA-256：

```text
8139bab8416ec9720bc4c9e11765944755f85bbe778b5cf404863bd6fb3ba30c
```

这只计算 zmcl 主程序。完整分发包另外记录；Java、Minecraft 资源、模组、Horizon 组件、系统图形库、curl、Python 和 OpenSSL 均不包含在这个数字中。

<!-- PERF_SUMMARY_START -->
| 完整启动器进程树：三轮汇总 | zmcl 0.1.0 | XMCL 0.73.1 |
| --- | ---: | ---: |
| PSS，MiB | 87.84 | 367.89 |
| RSS 直接求和，MiB | 106.18 | 679.93 |
| SwapPss，MiB | 0.00 | 101.99 |
| 逐样本 PSS + SwapPss 的中位数，MiB | 87.84 | 473.39 |
| 启动器进程数 | 1 | 7 |
| 约 10 秒窗口内的 CPU 时间中位数 | 0 个新增 tick | 0.70 秒 |
| 完整样本 | 3 × 11 | 3 × 11 |

**两批测试开始时的 MemAvailable 分别为 6,492,216 KiB（zmcl）和 1,894,756 KiB（XMCL）**；测量未随机化顺序，也未隔离其他工作。两款程序显示的是各自默认空首页，XMCL 还包含新闻轮播，不能称为完全相同的界面或功能负载。CPU 时钟频率为 100 Hz，未增加 tick 仅表示该有限窗口内未观察到计数增加。

数值来源：[zmcl 原始记录](measurements/2026-11-20-zmcl-0337762.json)、[XMCL 原始记录](measurements/2026-11-20-xmcl-0.73.1.json)和[逐样本派生汇总](measurements/2026-11-20-comparison.json)。
<!-- PERF_SUMMARY_END -->

测量使用固定版本 XMCL 0.73.1、同一脚本、独立空白用户目录和私有 X11 显示。每款程序运行三轮，每轮先暖机 20 秒，再取 11 个间隔约 1 秒的样本；统计完整进程树。工作机已有其他负载和交换活动，因此结果是这台机器、这些界面状态下的观测，不能当成所有设备的保证。

PSS 将共享页按使用者分摊，更适合比较完整应用；RSS 求和可能重复计算共享库。SwapPss 单独记录已经换出的页；PSS + SwapPss 先逐样本相加再取中位，它不是物理驻留内存。软件渲染、窗口内容和两款程序的功能覆盖差异均保留在报告中，尚无启动器硬件 GPU 路径的性能结论。

### 游戏期间不保留启动器

`zmcl gui --exit-after-launch` 在启动命令成功返回有效游戏 PID 后退出。真实 GUI 验收确认：启动器退出码为 0；Java 使用独立进程组，至少 10.162 秒的检查窗口内仍存活，之后继续存活至清理。此处验证的是进程生命周期，不声称这次单独的生命周期测试进入了主菜单。启动器退出后的常驻进程为 0；Java 和游戏的内存仍然存在。

命令行 `zmcl launch ...` 同样不要求后台常驻服务。该机制直接消除了游戏期间的启动器驻留成本；它不能降低 Minecraft 自身所需的堆和图形内存。

## 3. 已执行的验证

| 验证层次 | 结果 | 证据 |
| --- | --- | --- |
| Rust 应用与核心 | 58 + 19，共 77 项通过；fmt、Clippy 拒绝警告、release 构建通过 | [构建记录](validation/build-checks-2026-11-20.json) |
| Python 解包、备份、存档与进程辅助代码 | 6 组，共 62 项通过 | 同上 |
| 基准脚本回归 | 5 项通过，单独计数 | [性能方法](PERFORMANCE.md) |
| 模组与在线整合包 CLI | 16 次调用，含 3 次预期保护拒绝 | [内容管理](validation/content-cli-2026-11-20.json) |
| 资源包与光影 CLI | 20 次调用，含 3 次预期保护拒绝 | [资源管理](validation/resources-cli-2026-11-20.json) |
| 存档、备份与实例生命周期 CLI | 21 次调用，含 3 次预期保护拒绝 | [生命周期](validation/lifecycle-cli-2026-11-20.json) |
| 官方服务器下载与 EULA 门禁 CLI | 6 次调用，含 1 次预期保护拒绝 | [服务器](validation/server-cli-2026-11-20.json) |

这四组 CLI 共 **63 次调用，10 次预期拒绝**。在线整合包的 4 次调用包含在内容管理的 16 次中，没有重复相加。拒绝结果验证了具体保护条件，并非把命令错误统一计为成功。

较早的 CLI 报告没有保存执行文件哈希，其身份字段保留为 `null`，不从后来的提交推断。记录了哈希的验收分别标明对应构建。最终 77 项 Rust 验证针对上述代码基线完成。

### 实际游戏窗口

| Minecraft / loader | 已直接观察到的结果 | 模式 |
| --- | --- | --- |
| 1.21.1 / Vanilla | 欢迎及无障碍设置界面 | 显式离线身份 |
| 1.21.1 / Fabric 0.20.1 | 主菜单和 Mod Menu；已装 Mod Menu、Fabric API、Placeholder API | 显式离线身份 |
| 1.21.1 / Forge 52.1.20 | 主菜单 | 显式离线身份 |
| 1.21.1 / Quilt 0.30.2 | 主菜单 | 显式离线身份 |
| 26.3 / Vanilla / Java 25 | 主菜单 | 显式离线身份 |

[窗口记录和截图摘要](validation/game-menus-2026-11-20.json)保留每组实际结果。Fabric 界面显示的 56 个 mods 包含 Fabric API 的内部模块，不能据此声称下载了 56 个独立项目。尚未验收实际世界游玩、正版账号、Realms 或多人连接。

![通过 zmcl 启动的 Forge 1.21.1 主菜单](validation/images/forge-1.21.1-main-menu.jpg)

![通过 zmcl 启动的 Minecraft 26.3 主菜单](validation/images/vanilla-26.3-main-menu.jpg)

本轮还修复了两项真实兼容性问题：

1. Forge 官方处理器生成的本地库使用空下载 URL。现在仅对具有安全路径、可信摘要和明确非零大小的本地生成库接受这一表示；缺失或损坏时要求重新安装，不虚构网络地址、不跳过摘要验证。官方 Temurin 21 完成安装器执行后，实际进入 Forge 主菜单。
2. Quilt 的官方版本数组没有稳定版字段且未按版本排序。默认选择改为有界的语义版本比较，优先稳定发行；相应回归测试验证了无序列表、数字版本排序和 Fabric 的稳定标记。实际安装并启动的是 Quilt 0.30.2。

26.3 的一次测试继承了宿主 Wayland，随后 X11 测试中按标题查找又漏掉了 SDL 窗口。最终在私有 X11 测试环境中按游戏 PID 定位并直接观察主菜单。原始失败尝试保留，未将其伪装成成功的自动发现；产品本身没有被强制改成 X11。

## 4. 下载、内容与数据恢复

### Java 与版本安装

已手动安装并验证 Temurin Java 21。安装 Minecraft 26.3 时，验收根此前只有 Java 21，启动器按官方元数据识别到需要 Java 25，自动安装了 `jdk-25.0.5.1+2`，随后正确生成 Java 25 启动计划并进入主菜单。[Java 记录](validation/java-install-2026-11-20.json)保留安装前后状态与官方 SHA-256。

自动安装发生在 `install` 阶段；`launch` 只选择已有运行时。显式配置了不兼容 Java 时会报错，不替用户悄悄更改配置。

### Modrinth 模组与整合包

真实验收把 Mod Menu 从 **11.0.4 升到 11.0.5**，自动补齐 Fabric API 与 Placeholder API。仍被其他项目需要的依赖不能直接删除或禁用；手动文件保留，移除主项目也不擅自清理剩余依赖。

在线安装了 Simply Optimized Continued `2.1+1.21.1`：项目 `IpYIvkPB`、版本 `TAU7qd8y`，19 个客户端文件，共 **15,962,909 字节**，Minecraft 1.21.1 / Fabric 0.19.3。安装、状态与启动计划均通过；该整合包没有实际进入游戏。[整合包记录](validation/modrinth-pack-2026-11-20.json)可独立核对文件摘要。35.644 秒的安装耗时复用了已有游戏缓存，不作为冷安装速度结论。

`.mrpack` 下载和导入按 SHA-1 与大小验证，SHA-512 元数据被保留；导出会分块计算并写入 SHA-1 和 SHA-512。对验收包独立重算 SHA-512 的审计，不等于产品下载路径已经重算该摘要。依赖解析有预算上限，不是任意版本组合的完整回溯求解器。

### 资源包与光影

已真实下载资源包 No Pumpkin Blur（768 字节）与 Terder’s Potato Shader（24,189 字节），校验官方摘要、大小、ZIP 完整性及根目录结构。还用真实旧版资源包建立夹具，再通过 CLI 更新到新版本，确认旧字节保留在恢复历史中。

启用、禁用、更新、移除、历史恢复、冲突文件保护、受管理文件篡改保护均已验收。运行状态拒绝使用受控存活进程夹具验证。没有安装 Iris，也没有验收光影渲染；这些命令管理文件和归属记录，不替玩家修改游戏内资源选择。

### 缓存损坏修复与磁盘统计

选择官方 `com.mojang:blocklist:1.0.10` 库，把目标路径与内容缓存路径分别移除后写入 **两个长度仍为 964 字节的损坏副本**。这保证测试依靠内容摘要识别损坏，而不是只靠文件长度。

再次安装时明确重新下载 1 个文件。目标与缓存均恢复正确 SHA-1、大小和原始字节，重新共享同一 inode，且为只读。[修复报告](validation/cache-repair-2026-11-20.json)保留具体证明。

当时整个验收根的统计为：

| 指标 | 字节 / 数量 |
| --- | ---: |
| 文件路径数量 | 18,565 |
| 按路径累计的逻辑字节 | 3,899,100,332 |
| 按不同文件实体累计的已分配字节 | 2,278,838,272 |

已分配字节按不同 `(device, inode)` 的 `st_blocks × 512` 累加，包含块取整。这个数据集同时包括游戏、Java、Horizon、实例和保留的恢复现场；它不是启动器本体大小，也不是通用节省比例。

### 存档、备份与回收站

实际 CLI 使用明确标注的合成 NBT 夹具，验证目录与 ZIP 导入、导出、克隆的逐字节一致性；该 region 夹具不能在游戏中加载，因此不推断真实世界兼容性。

备份恢复核对每个文件的 SHA-256；恢复前快照保留了将被替换的内容。实例克隆的可写文件 inode 独立，修改克隆不影响源。回收与恢复保留内容，并拒绝覆盖已占用目标。未完成事务和仍在运行的实例会阻止相应变更；无法确认数据身份的旧恢复日志会保留现场。

## 5. Horizon 与本地服务器

### Horizon

已真实安装官方 **Horizon launcher 0.2.84 / Linux x86_64**：

- 下载归档：35,123,939 字节。
- SHA-256：`9580c1017fc0aefe5baeabfb0c513746ed262a7b5dd53104915f3cabfa9ab0f9`。
- 官方 Ed25519 发布签名、校验清单、GitHub 发行资产摘要均通过验证。
- 安装列表、组件版本和启动计划已检查。

[下载验收](validation/horizon-install-2026-11-20.json)针对的是官方启动组件。它仍负责自己的正版账号及 Minecraft 26.3 资源准备，本轮未完成 Horizon 登录或实际游戏流程。Java 版 Fabric/Forge/NeoForge 模组不能据此视作可在 Horizon 中运行。官方流程见 [Horizon 安装说明](https://playhorizon.dev/docs/install)。

### 服务器

已下载官方 Minecraft 1.21.1 server.jar，51,627,615 字节；独立重算 SHA-1 与官方元数据一致。创建、安装、列表、状态命令通过；保留 `eula=false` 时启动被明确拒绝，文件字节未变，未产生该测试服务器进程。

没有接受 EULA，也没有实际启动或停止运行中的 Minecraft 服务器。进程管理的其他验证使用本地受控夹具，不将其扩大为完整服务器运行验收。

## 6. Project Z 依赖和 UI 名称

| 用途 | 包 | 本轮源码 pin |
| --- | --- | --- |
| JSON 和协议解析 | zconv 0.4.0 | `0af1f7eb0766ff0ce9db5cee7c6184eff511a8c1` |
| 命令行解析 | zclap 0.1.4 | `4d87f3ae3743e81509d5ac5500909955667c860f` |
| 原生窗口、输入和绘制 | zui-native 0.1.0 | `5e37fcfb791eec9ac523fc19373e95d0a513112f` |

启动计划核心是 `no_std + alloc`，OS、网络和进程操作留在应用层。没有引入第三方 Rust crates；实际使用的系统程序与图形库仍属于依赖。

当前 `zui-native` 与 `zgpui` 是两个独立层。前者创建原生窗口并绘制控件，Rust 导入名为 `zui`；后者提供 `no_std` 场景、布局和 Renderer 契约，由宿主提供窗口与绘制能力。已向 Project Z 协调维护者核对，目前没有已发布的统一替代 API，因此没有通过只改包名来假装迁移。

zui 的正式源码 pin 已升级并通过本地构建、真实 GUI 和窗口属性验收：`WM_CLASS=zmcl/Zmcl`，UTF-8 标题正确，实际窗口和最小内容尺寸均为 1120×760。消费侧的版本、gitlink 和 Cargo.lock 已核对；路径依赖版本未变，所以 lock 文件无须伪造改动。聚合登记与闭环状态见[依赖传播记录](DEPENDENCIES.md)。

## 7. 仍未完成的范围

[完整对照表](FEATURES.md)是持续维护的功能依据。当前尚不能宣称完成：

- CurseForge、FTB、其他整合包格式及其他启动器的完整迁移。
- XMCL Together / P2P 联机、NAT 穿透与相关在线服务。
- Windows Bedrock 安装管理。
- Yggdrasil、Ely.by、LittleSkin 等其他账号体系，以及 OptiFine、LabyMod 等完整安装流程。
- 存档区块和蓝图编辑、创作者发布、完整主题、手柄、AI 等界面能力。
- 全部 CLI 能力的 GUI 控件覆盖、无障碍桥接和复杂文字排版。
- Windows/macOS 的原生构建与真实桌面验收。
- 实际正版账号全流程、Realms/多人、真实世界游玩、光影渲染、Horizon 游戏准备及真实服务器运行。
- 安装峰值资源占用、硬件 GPU 启动器性能、长期负载，以及恢复历史的自动清理。

上述未完成项保留为产品工作，不能由“主菜单可启动”“零第三方 Rust 依赖”或某个内存样本推导为已完成。

## 8. 复现与交付方式

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/zmcl.git
cd zmcl
cargo build --release --locked
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
./target/release/zmcl gui --exit-after-launch
```

固定工具链为 Rust 1.100.0。Linux GUI 需要 X11/XWayland、OpenGL、FreeType 和 Fontconfig。下载与部分辅助操作需要 curl、Python 3，Horizon 签名需要支持 Ed25519 的 OpenSSL；Java 按游戏版本管理。

手工 CLI 验收脚本位于 `src/resources/test_cli.py`、`scripts/test_lifecycle_cli.py`、`scripts/test_server_cli.py` 和 `scripts/test_cache_repair.py`。它们使用独立测试目录；缓存损坏脚本进一步限制根目录并检查没有相关 Java 进程。基准方法及精确参数由 `scripts/bench_linux.py` 和性能报告共同记录。

分发包由 `scripts/package.py` 生成，并附 SHA-256。用户级安装与卸载使用 `scripts/install.py`；卸载保留游戏数据及用户已修改的安装文件。发布包身份、安装测试和最终来源在[发布页面](https://github.com/LIghtJUNction/zmcl/releases)与性能报告中记录。

## 预编译包的运行范围

最终 Linux x86_64 主程序经 `readelf --version-info` 检查，最高要求的 glibc 版本符号是 `GLIBC_2.43`。因此本轮预编译包要求 glibc 2.43 或更新版本，并依赖发行清单列出的 X11/OpenGL/字体库以及按需系统程序。这个结论是直接 ELF 符号检查，不代表所有满足该版本的发行版均已实机验收；较旧系统可使用仓库的源码构建流程。

安装器还在带空格的私有目录中通过桌面入口静态规范检查和 GLib 参数解析；将生成入口的 `gui` 替换为 `--version` 后，`gio launch` 实际返回版本号。这项检查没有启动图形窗口或游戏。见[桌面入口验收](validation/desktop-entry-2026-11-20.json)。

## 最终分发包

发行包从干净的资料及打包器提交 [`a8f72474c4f5289ea881226299ae7706b8024b59`](https://github.com/LIghtJUNction/zmcl/commit/a8f72474c4f5289ea881226299ae7706b8024b59) 生成；实际应用二进制对应前述 `0337762`。后续网站和报告补充不改变该二进制。归档内的 `release.json` 分别记录这两种来源，以及运行环境和各文件摘要。

| 项目 | 最终结果 |
| --- | --- |
| 文件名 | `zmcl-0.1.0-linux-x86_64.tar.gz` |
| 压缩归档 | 648,849 B，约 633.64 KiB |
| 解压文件内容总大小 | 1,453,865 B，7 个常规文件 |
| 主程序 | 1,412,176 B |
| 归档 SHA-256 | `304f7a0e3c2ed5dd84ecc98093715793b3f5eb4cb484449b8342b619116d9ea5` |
| 打包器工作区修改 | `false` |

这里的解压大小是文件内容字节之和，不是文件系统分配量；未把系统图形库、curl/Python、Java、游戏文件或 Horizon 下载包装进启动器归档。最终包完成 5 个安装验收操作，包含 1 次预期的修改文件保护拒绝，模拟游戏数据均保留。源码中的 README/FEATURES 不变，只有归档副本的相对 Markdown 链接转换为公共仓库地址。

[完整打包与安装证据](validation/package-install-2026-11-20.json) · [v0.1.0 开发预览下载](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.0)
