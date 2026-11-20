# 性能测量与可复核记录

本页现有三轮表格为 **v0.1.0 冻结基线**。主线已接入统一 zgpui 的 Linux CPU 后端；新版本最终内存结果需要独立构建身份、主进程/完整进程树及显示服务器样本，不能沿用旧二进制测量。低于 10 MiB 仍是待完成目标。当前依赖与实际窗口验收见[依赖记录](DEPENDENCIES.md)。

本次记录对应 zmcl 源码 [0337762](https://github.com/LIghtJUNction/zmcl/commit/033776287ab10aab90f46fd5b65309fecb9b8711) 和 XMCL 官方 0.73.1 Linux x64 发行版。2026 年 11 月 20 日，在同一台 Linux 开发机上，使用同一份 schema 4 脚本，各运行三轮默认空首页。zmcl 的主程序为 **1,412,176 bytes**；空闲 PSS 的三轮汇总值为 **89,946 KiB（87.84 MiB）**，XMCL 为 **376,722 KiB（367.89 MiB）**。[zmcl 原始样本](measurements/2026-11-20-zmcl-0337762.json) · [XMCL 原始样本](measurements/2026-11-20-xmcl-0.73.1.json)

**这些数值只描述本次软件渲染环境中的默认首页。** 两款应用开始测量时，主机可用内存分别为：XMCL **1,894,756 KiB**，zmcl **6,492,216 KiB**。XMCL 样本存在明显换页，两个默认首页的内容也不同。以下表格同时保留驻留页、换出页和合计值，不据此推导相同功能下、所有机器上的性能倍数。zmcl 仍在补齐功能，当前范围见[功能矩阵](FEATURES.md)。

## 最终结果

内存单位为 KiB。每轮先取 11 个完整样本的中位数，再取三轮中位数；下表不是单次最低值。

| 指标 | zmcl 0.1.0 / 0337762 | XMCL 0.73.1 |
| --- | ---: | ---: |
| PSS：按份额计入共享驻留页 | 89,946 | 376,722 |
| SwapPss：换出页的相应份额 | 0 | 104,439 |
| 逐样本 PSS+SwapPss 后的中位数 | 89,946 | 484,747 |
| RSS：进程驻留页直接求和 | 108,728 | 696,252 |
| Swap：进程换出页直接求和 | 0 | 132,516 |
| 完整样本 / 轮数 | 33 / 3 | 33 / 3 |
| 观察到的启动器进程数 | 1 | 7 |
| CPU 时间中位数（秒） | 0.00 | 0.70 |
| CPU 观察时间范围（秒） | 10.047–10.080 | 10.146–10.247 |
| 单核占用率中位数 | 0.00% | 6.90% |

**PSS+SwapPss 是“驻留和换出页面的合计，非实际物理 RAM”。** 它是在每个采样点内先相加，然后取轮内与轮间中位数；不能直接相加表格中已取中位数的两行。这里的合计也不代表总分配量、峰值或系统整体内存使用。[派生汇总与计算口径](measurements/2026-11-20-comparison.json)

zmcl 三轮观察窗内的 CPU ticks 都没有增加。内核 CPU 计数频率为 100 Hz，每 tick 为 10 ms，因此这里的 0.00 秒表示有限观察窗内未观察到计数增加，不表示任意时段都没有 CPU 消耗。CPU 单核占用率由 CPU 时间除以各轮实际经过时间计算；不是全机 24 个逻辑 CPU 的总百分比。

### 各轮数据与范围

| 应用 / 轮次 | PSS 中位数 | SwapPss 中位数 | 逐样本合计中位数 | RSS 中位数 | CPU 秒 / 实际观察秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| zmcl 1 | 89,946 | 0 | 89,946 | 108,728 | 0.00 / 10.080 |
| zmcl 2 | 89,826 | 0 | 89,826 | 108,600 | 0.00 / 10.077 |
| zmcl 3 | 90,300 | 0 | 90,300 | 109,100 | 0.00 / 10.047 |
| xmcl 1 | 376,722 | 104,439 | 481,459 | 696,252 | 0.76 / 10.247 |
| xmcl 2 | 366,925 | 117,641 | 484,747 | 692,344 | 0.70 / 10.146 |
| xmcl 3 | 490,434 | 0 | 490,434 | 852,600 | 0.61 / 10.177 |

六轮均读取到 11 个完整内存样本，采样点上的 PID/starttime 身份集合均稳定，没有将缺失样本当作零。XMCL 三轮的驻留页与换出页差异较大，合计值比仅看 PSS 更便于识别换页影响；这些变化仍受共享主机状态与应用内容影响。

| 应用 / 指标 | 三轮中位数范围（KiB） | 全部 33 个样本范围（KiB） |
| --- | ---: | ---: |
| zmcl / PSS | 89,826–90,300 | 89,816–90,300 |
| zmcl / SwapPss | 0–0 | 0–0 |
| zmcl / PSS+SwapPss | 89,826–90,300 | 89,816–90,300 |
| xmcl / PSS | 366,925–490,434 | 340,145–496,167 |
| xmcl / SwapPss | 0–117,641 | 0–117,850 |
| xmcl / PSS+SwapPss | 481,459–490,434 | 424,310–496,167 |

### 程序文件与发行包

文件体积按逻辑字节数统计。ELF、下载包、展开后的常规文件合计是不同口径；不把一个项目的单个可执行文件与另一个项目的完整发行包直接计算比例。

| 项目 | zmcl | XMCL 0.73.1 |
| --- | ---: | ---: |
| 主程序 ELF（bytes） | 1,412,176 | 224,836,856 |
| 压缩发行包（bytes） | 648,849（tar.gz） | 85,934,576（tar.xz） |
| 展开后的常规文件合计（bytes） | 1,453,865 | 293,657,164 |
| 展开后的常规文件数 | 7 | 23 |

zmcl 的 [v0.1.0 Linux x86_64 开发预览](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.0) 包含七个常规文件：主程序、README、功能文档、许可证、图标、安装器和 release.json。648,849 bytes 是最终 tar.gz 的实际大小；1,453,865 bytes 是七个文件的内容字节之和，不包括归档头或文件系统分配。压缩方法及两款产品包含的内容不同，本页不由发行包体积推导等功能节省比例。[清单与安装验收](validation/package-install-2026-11-20.json)

**当前预编译包要求 Linux x86_64、glibc 2.43 或更新。** 2.43 是此 ELF 直接引用的最高 GLIBC 符号版本，不是所有 Linux 发行版都兼容的证明；系统图形、字体库和外部工具还有自己的要求。其它环境可按[源码构建说明](../README.md)构建，并自行验证运行依赖。该分发包的内容与安装验收只覆盖 Linux x86_64。

zmcl 是动态链接的原生程序；系统图形、字体与其它动态库不包含在 ELF 数字中。上表也不包含 Java、Minecraft、mods、账号配置、下载缓存、构建目录或实例数据。展开文件合计没有按设备号与 inode 去重，不等于实际物理磁盘块占用。[XMCL 官方包记录](measurements/2026-11-19-xmcl-package.json)

### 窗口出现与首帧

同一脚本以约 250 ms 间隔轮询 X11 窗口，记录“窗口被发现”的时间。zmcl 三轮为 292.377、296.835、296.322 ms；XMCL 为 1,621.568、3,334.413、1,066.178 ms。各自中位数为 **296.322 ms** 与 **1,621.568 ms**。这是轮询发现窗口的时间，受到调度和轮询粒度影响，不是首个可交互画面时间，也不能视为严格冷启动成绩。

zmcl 另有进程内首帧标记，本次为 277、271、298 ms。它从 GUI 入口计到第一帧提交，起点与外部窗口检测不同；XMCL 没有对应标记，因此不对这一数值作跨应用比较。操作系统缓存没有清空，顺序运行可能影响时间。

## 环境与测试协议

| 条件 | 本次设置 |
| --- | --- |
| 操作系统 | Omarchy / Linux 7.3.4-arch1-1，x86_64 |
| CPU | Intel Core i9-12900HX，24 个逻辑 CPU |
| 物理内存 | 16,090,532 KiB，约 15.35 GiB |
| 显示 | 独立 Xvfb，1280×800×24；应用窗口 1024×720 |
| 图形环境 | LIBGL_ALWAYS_SOFTWARE=1；X server 报告 llvmpipe（LLVM 23.1.1，256 bits） |
| 其它驱动覆盖 | LP_NUM_THREADS、GALLIUM_DRIVER、MESA_LOADER_DRIVER_OVERRIDE、MESA_GL_VERSION_OVERRIDE 均未设置 |
| 桌面会话变量 | XDG_SESSION_TYPE=x11、SDL_VIDEO_DRIVER=x11，移除 WAYLAND_DISPLAY |
| 场景 | 每轮全新应用数据和缓存目录，未登录、无实例、默认首页 |
| 暖机 / 采样 | 窗口出现后等待 20 秒；11 个样本，间隔设为 1 秒 |
| 顺序 | XMCL 连续三轮，然后 zmcl 连续三轮；没有随机化或交错 |
| 主机背景与缓存 | 共享开发机，未做系统级隔离；没有清空操作系统缓存 |
| 进程范围 | 启动器进程树、会话和此前观察到的存活子进程；排除 Xvfb、测试控制器、Java 与 Minecraft |

X server 的 renderer 是测试显示服务的记录。相同 Xvfb 和环境变量不保证两款应用内部使用相同图形后端；该记录不能单独证明 XMCL 的 ANGLE 后端。本次 zmcl 自身的首帧记录也报告 llvmpipe。硬件 GPU 路径未测得，结果不能推广到硬件加速、Windows 或 macOS。

应用开始测量时记录的主机状态如下。这里是两批测试的开始状态，不是每个样本同步采集的系统状态。

| 主机内存状态（KiB） | XMCL 测试开始 | zmcl 测试开始 |
| --- | ---: | ---: |
| MemAvailable | 1,894,756 | 6,492,216 |
| SwapTotal | 47,535,828 | 47,535,828 |
| SwapFree | 29,582,628 | 29,239,648 |
| SwapTotal−SwapFree | 17,953,200 | 18,296,180 |

两批测试开始时分别已有约 **17.12 GiB** 和 **17.45 GiB** 交换空间被使用。这一差异以及可用内存变化，限制了对结果作因果解释的强度。这里只公布描述性样本，不给出置信区间或普遍优化结论。

### 内存与 CPU 采样方式

RSS 表示驻留页；PSS 对共享页按共享进程数分摊。SwapPss 记录换出页的相应份额。多进程 RSS 直接相加会重复计入共享页，所以资源比较同时展示 PSS、SwapPss、RSS 和进程数。[Linux 内核 /proc 说明](https://docs.kernel.org/filesystems/proc.html)

脚本读取每个观察到的进程的 smaps_rollup。读取失败的样本会保留失败标记，不参与完整内存样本的中位数。CPU 身份由 PID 和 starttime 组成，累计 utime+stime 增量。只有采样点身份集合稳定、读取成功且计数没有回退，才发布完整 CPU 值；否则返回 null，并单独保留共同存活进程的观察值。两个采样点之间产生并退出的短命进程可能漏记，因此本协议不代替安装、下载或游戏运行阶段的持续跟踪。

每轮开始和结束都校验可执行文件 SHA-256。最终脚本仅选择 100–199 号显示，跳过任何已有锁或 socket，确认 Xvfb 锁中的 PID 属于本轮进程后才使用；每轮使用新的空授权文件，不读取现有桌面授权，不修改已有显示的访问策略。本次六轮均使用释放后可复用的 :110。

## 构建身份与原始证据

| 对象 | 身份 |
| --- | --- |
| zmcl 版本 / 源码 | 0.1.0 / 033776287ab10aab90f46fd5b65309fecb9b8711 |
| zmcl ELF SHA-256 | 8139bab8416ec9720bc4c9e11765944755f85bbe778b5cf404863bd6fb3ba30c |
| zmcl tar.gz SHA-256 | 304f7a0e3c2ed5dd84ecc98093715793b3f5eb4cb484449b8342b619116d9ea5 |
| 干净打包提交 | a8f72474c4f5289ea881226299ae7706b8024b59；working_tree_modified=false |
| zmcl 原生 UI 依赖 | zui-native，zui commit 5e37fcfb791eec9ac523fc19373e95d0a513112f |
| XMCL 官方发行版 | [v0.73.1](https://github.com/Voxelum/x-minecraft-launcher/releases/tag/v0.73.1) |
| XMCL ELF SHA-256 | 34c11d2cbb5bf342cc701a10d205b2b34db37adf1d5d68c73174aeccf69fa7cd |
| XMCL tar.xz SHA-256 | b2baa582131f53c7c017124c8e8e6d801576794c2ac77e422105259dff6b4461 |
| 采样脚本 SHA-256 | e1f6171edeabf67c0664545df696805ca4565ee59d5801e19f366b5b2f5fbaa2 |

原始 schema 4 JSON 以测量输出的字节原样公开。文件中保留每个采样点的内存、进程身份与 CPU ticks，未发布私人路径、账号数据或完整启动日志。

| 文件 | SHA-256 / 用途 |
| --- | --- |
| [zmcl 原始记录](measurements/2026-11-20-zmcl-0337762.json) | f24e3421ff01122c56778350767d5d47a5c3374647450ddba39a2afa78b37f95 |
| [XMCL 原始记录](measurements/2026-11-20-xmcl-0.73.1.json) | d4e8c12f0ffcd7b317e9f285ca6717c200bce9ec312ad4864f37903629647973 |
| [最终汇总 JSON](measurements/2026-11-20-comparison.json) | 包含逐样本合计后的范围、轮内中位数和轮间中位数 |
| [最终分发包与安装验收](validation/package-install-2026-11-20.json) | 4e374decd2c396a0af9f2585440110c0eeeb52de8f882c764c1efb6dd99ac01d |
| [zmcl 默认首页](measurements/2026-11-20-zmcl-empty.jpg) | 本次第一轮 1024×720 实际截图 |
| [XMCL 默认首页](measurements/2026-11-20-xmcl-empty.jpg) | 本次第一轮 1024×720 实际截图 |

[2026-11-19 的 XMCL schema 3 记录](measurements/2026-11-19-xmcl-0.73.1.json) 是历史方法试运行。早期 GUI 烟测、早期单轮 XMCL 数值、其它源码对应的体积与内存均不参与本页最终对照。GUI 退出生命周期的功能验收单独保存在 [2026-11-20 原生窗口与生命周期记录](measurements/2026-11-20-gui-exit-and-window.json)，该功能测试的限核条件不用于性能表。

## 复现

先准备已经校验的两个应用。脚本需要 Linux /proc、Python 3、Xvfb、xdotool、glxinfo 和 ImageMagick import。下例只展示测试入口；XMCL 路径替换为解包后的官方 ELF，输出路径选用新的空目录。

~~~sh
nice -n 10 python3 scripts/test_bench_linux.py

nice -n 10 python3 scripts/bench_linux.py \
  --app xmcl --binary /path/to/xmcl \
  --version 0.73.1 --revision v0.73.1 \
  --output .test-evidence/reproduce/xmcl \
  --trials 3 --warmup 20 --samples 11 --interval 1

nice -n 10 python3 scripts/bench_linux.py \
  --app zmcl --binary target/release/zmcl \
  --version 0.1.0 \
  --revision 033776287ab10aab90f46fd5b65309fecb9b8711 \
  --output .test-evidence/reproduce/zmcl \
  --trials 3 --warmup 20 --samples 11 --interval 1
~~~

五项脚本回归验证了已有子进程的后代遍历、稳定集合的 CPU 增量、进程进入与退出、PID 复用、读取不完整时的判断。它们验证采样代码的关键边界，不消除共享主机与图形后端的实验限制。正式原始记录共 66 个完整样本，未在结果中挑选轮次。

## 游戏启动后减少常驻进程

普通 CLI 的 launch 子命令在启动游戏并返回 PID 后退出。GUI 用户也可以显式启用：

~~~sh
zmcl gui --exit-after-launch
~~~

该选项只在游戏 launch 任务成功、输出包含有效正 PID 后让 GUI 正常退出；安装、账号任务、错误、取消和不包含真实 PID 的输出不会触发退出。游戏在独立进程组中继续运行，日志写入实例日志目录。实际功能验收观察到 GUI 退出码为 0，游戏 Java 在其退出 10.162 秒后仍存活；该次记录没有把“Java 存活”表述为“已到达主菜单”。[生命周期证据](measurements/2026-11-20-gui-exit-and-window.json)

本页所有 GUI 基准均使用默认保持打开的状态。退出后的启动器零常驻进程不与仍打开的 XMCL 作比较。

## 尚未覆盖的性能问题

当前没有发布硬件 GPU 数据、Windows/macOS 数据、安装和解压峰值、相同大型整合包的运行结果，或按设备号与 inode 去重的物理游戏存储对比。三轮默认空首页也不能代替长期空闲、网络内容变化、批量实例和账号同步场景。后续每增加一组场景，都应固定输入、单独记录源码与产物、保留完整样本，并给两款应用使用相同的计数口径。
