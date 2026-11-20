# zmcl 0.1.3：AUR 发行与管理助手内存迭代

报告日期：2026-11-20（Asia/Taipei）。

**GitHub v0.1.3 与 AUR zmcl-bin 0.1.3-1 已公开，公开配方构建、正常系统升级和安装后原生窗口验收均已完成。完整 GUI 低于 10 MiB 的目标仍未达到。** 本轮降低了大备份列表查询的观测内存峰值，并修正了把运行时转发程序误记成 Python 的计量问题；没有证据表明整个 GUI 操作过程的峰值已经稳定下降。[发行回执](../validation/release-0.1.3-2026-11-20.json) · [AUR 安装回执](../validation/aur-install-0.1.3-2026-11-20.json) · [测量汇总](../measurements/2026-11-20-helper-gui-0.1.3.json)

## 安装与制品身份

~~~sh
yay -S zmcl-bin
# 或 paru -S zmcl-bin
zmcl --version
zmcl gui
~~~

也可直接使用已核验的公开 Git 配方：

~~~sh
git clone https://aur.archlinux.org/zmcl-bin.git
cd zmcl-bin
makepkg -si
~~~

| 项目 | 已核验值 |
| --- | --- |
| 应用源码 / v0.1.3 标签 | [9ef89bb92d46d324172d6ea8b13054c81434e178](https://github.com/LIghtJUNction/zmcl/commit/9ef89bb92d46d324172d6ea8b13054c81434e178) |
| GitHub 发行 | [v0.1.3 Linux development preview](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.3)，2026-11-20 19:28:50 公开 |
| Linux x86_64 主程序 | 1,473,752 字节；SHA-256 `02620a8ec4fac34e63fcbe483268149b2ed4032ac2d7bf42975ff6b19b72a1aa` |
| 官方 tar.gz | 679,517 字节；SHA-256 `b15024e99dd1275db15020ef26669f24aaad9026d948dd6683cacac5c7647e0e` |
| 展开内容 | 7 个常规文件，内容合计 1,527,273 字节；不含文件系统分配开销 |
| AUR master | `60c9ad8e1be8204c6a4e0024c83a4a5851ada0f5`，版本 0.1.3-1 |
| GitHub 中的 AUR 配方 | [7dc09fff213d659d99f98e61ebb2706654c0160b](https://github.com/LIghtJUNction/zmcl/commit/7dc09fff213d659d99f98e61ebb2706654c0160b) |
| 实际系统升级 | 0.1.2-1 → 0.1.3-1；升级前后均为 19 个受管文件、0 个改动 |
| 已安装程序 | `/usr/bin/zmcl` 的版本、大小和 SHA-256 与官方制品一致 |

先回下载草稿资产逐字节核对，再公开发行，随后匿名下载再次核对归档、校验文件、manifest 和 ELF。AUR 作者目录构建包为 674,505 字节，公开 HTTPS 新克隆构建包为 674,509 字节；二者的包容器摘要分别记录，不能互换。实际安装使用公开新克隆生成的包，SHA-256 为 `6c323e69cd82d51ebbb9b1524ba195f640ab0f04cf6c39aa88559a48f501cce2`。两份包的应用 ELF 都保持官方摘要。[发行与安装证据](../validation/aur-install-0.1.3-2026-11-20.json)

发布后首次 RPC、yay、paru 查询仍返回缓存中的 0.1.2-1；19:46:59 复验三者均返回 0.1.3-1。两个时点的结果均保留。namcap 的仅有提示是 curl、Python “可能不需要”；它们由程序按需调用，因此保留依赖。普通 makepkg 与本机升级通过，不等于所有 Arch 环境的干净 chroot 验证。[完整回读](../validation/aur-install-0.1.3-2026-11-20.json)

实际 ELF 要求 glibc ≥ 2.39、Fontconfig ≥ 2.17.0，直接链接 X11、FreeType、Fontconfig、libgcc 和 libc，没有直接 OpenGL 依赖。系统库、Python、curl、Java、游戏与缓存不包含在上述文件体积中。GUI 使用正式 zgpui 0.1.5 的 `native` + `linux-software`；核心继续保持 `no_std + alloc`。本轮没有修改上游依赖或另发 zui-native。[制品 manifest 与来源](../validation/release-0.1.3-2026-11-20.json) · [源码输入证明](../validation/helper-release-0.1.3-2026-11-20.json)

## 实现为什么会少用内存

第一批修改计量器。进程名可能是 `python3`，实际执行的却是 mise 转发程序。采样现在结合 PID、启动时刻、可执行映像名称及设备/inode，并在读取内存前后检查身份；观察到身份变化或读失败时保留未知，不填零。CPU 身份仍按 PID 与启动时刻连续计算。此项提高归属准确性，本身不减少应用开销。[6d47399](https://github.com/LIghtJUNction/zmcl/commit/6d47399a2d1d777ae2c6343ee81c21316c487b39)

第二批把 gzip、hashlib、ctypes、subprocess 等模块的导入移到真正需要它们的操作中。轻量查询无需先加载启动、解压或发布目录所需的全部模块。修改仍在既有文件校验、锁与恢复流程中运行；对导入失败的顺序进行了复查，恢复所需模块在开始修改文件前加载，停止服务所需模块在发送信号前加载。[af14e2c](https://github.com/LIghtJUNction/zmcl/commit/af14e2c0f9b3a39e5b5a8a70976d2d0721b4400f)

第三批缩短大对象的存活时间。备份列表原先可能在检查下一份归档时仍保留上一份归档的完整索引和 manifest。现在每份检查限定在一次函数调用内，返回小型摘要后即可释放完整对象；解析完成后也不再保留 manifest 原始字节。世界与备份的 JSON 直接写入输出流，避免再创建一份完整编码字符串。结果列表对象仍在内存中，尚未实现分页扫描；释放对象也不保证分配器立即把页面归还系统。[9ef89bb](https://github.com/LIghtJUNction/zmcl/commit/9ef89bb92d46d324172d6ea8b13054c81434e178)

新增字节级 CLI 测试覆盖 Unicode、有效和损坏归档，确认紧凑 JSON、转义策略、换行及 `data_verified:false` 语义保持一致。流式编码或写出若失败，stdout 可能已有部分内容；现有 Rust 调用者先检查退出码，成功后才解析 JSON，不能把“失败时输出必为空”当作保证。[最终门禁](../validation/helper-release-0.1.3-2026-11-20.json)

## 测量身份与条件

本轮同时保留三种用途的二进制身份：

| 用途 | 源码 / 二进制 SHA-256 |
| --- | --- |
| 既有 GUI 基线 | 官方 0.1.2 / e86b584；`71bf4017072703a91359ea84e2493db218d9962456ece2060d0f2e42b3500c32` |
| 列表优化的直接对照 | 已发布 af14e2c；`908f0582c07a7aeb8b452a410d45d0dd3bbb84e2e89a96993ff9ac6ed681b12f` |
| 本次最终制品 | 输入与公开 9ef89bb 逐文件一致；`02620a8ec4fac34e63fcbe483268149b2ed4032ac2d7bf42975ff6b19b72a1aa` |

编译先于 9ef89bb 的 GitHub 提交。证据记录实际构建基线 af14e2c、当时输入的 SHA-256、依赖 gitlink，以及这些输入与随后公开提交的逐文件比较，没有倒写构建时的 HEAD。较早按需导入原型另有 `638ac8a5…` 身份；在它之后修正过两处导入失败顺序，因此不能把原型的 GUI 数字标为精确 af14e2c 或最终 0.1.3。[来源证明](../validation/helper-release-0.1.3-2026-11-20.json) · [逐构建记录](../measurements/2026-11-20-helper-gui-0.1.3.json)

测量在共享 Linux x86_64 开发机进行，查询基准记录 Linux 7.3.4、glibc 2.44、Python 3.14.10；没有清空系统缓存。本维护者的构建和测试已结束后才启动测量，但未逐样本记录其他维护者的 CPU 或宿主内存压力，不能当作独占安静机器的结果。RSS 会重复计算进程间的共享映射；PSS 按共享比例分摊，所以其他进程也可能影响 PSS。[查询环境](../measurements/2026-11-20-helper-queries-0.1.3.json)

### 大备份列表：三对实际 CLI 查询

同一个临时根目录中创建 2000 个有效合成 NBT 世界，每个标题 160 个 ASCII 字节；另创建两份各含 10000 个 64 字节文件的 ZIP 备份及完整 manifest。夹具的文件摘要在计时前全部验证。前后程序交错 AB/BA 运行，每种查询三对，明确使用 `/usr/bin/python3`，没有启动 GUI、Minecraft 或 Java。计量包含实际 CLI 及其发现的子进程；结果逐轮进行规范 JSON 比较。[夹具、命令和逐轮数据](../measurements/2026-11-20-helper-queries-0.1.3.json)

以下为完整 CLI 进程树的**观测峰值**，单位 KiB；各列最大值可能发生在不同样本。

| 备份列表轮次 | 改动前 RSS | 改动后 RSS | 改动前 PSS | 改动后 PSS |
| --- | ---: | ---: | ---: | ---: |
| 1 | 45,468 | 35,312 | 35,030 | 24,819 |
| 2 | 45,500 | 35,368 | 35,033 | 24,824 |
| 3 | 45,692 | 35,352 | 35,099 | 24,805 |

三对中观测峰值都下降。按各组三轮最大观测值比较，RSS 为 45,692 → 35,368 KiB（约 −22.6%），PSS 为 35,099 → 24,824 KiB（约 −29.3%）。这是相对已经完成按需导入的 af14e2c 候选的列表优化收益，不能再与早期模块导入诊断的降幅相加。[逐轮记录](../measurements/2026-11-20-helper-queries-0.1.3.json)

世界列表的 RSS 观测峰值范围为前 28,616–30,660 KiB、后 28,788–28,876 KiB；第二对候选略高。因而没有“世界列表峰值稳定下降”的结论。12 次查询全部成功；同一种查询的前后规范 JSON 响应在各轮均相同。1193 个样本中有 73 个包含不可读的相关进程，每轮完整树中位数均为 `null`。5 ms 是目标间隔，最长实际间隔约 38.97 ms；采样峰值都是下界。计时受采样和共享主机扰动，不能把中位耗时当作吞吐保证。备份列表检查结构与 manifest，仍明确返回 `data_verified:false`；它不是每次列出时重新验证全部文件内容。[计量边界](../measurements/2026-11-20-helper-queries-0.1.3.json)

查询控制器、夹具构造及计时前的内容校验不计入被测 CLI。控制器独立快照从夹具构造前 RSS/PSS 24,852/15,080 KiB 增至构造后 32,752/22,946 KiB，最终为 32,564/22,721 KiB；这些是时点快照，不是控制器峰值。[观察器记录](../measurements/2026-11-20-helper-queries-0.1.3.json)

### 最终 GUI：主进程与相关进程树

最终二进制在私有 Xvfb 1280×900×24、1120×760 原生 CPU 窗口中，分别完成世界导入、导出回导、备份与确认恢复、资源页、服务器创建及 EULA 拒绝六组流程。启动及流程中的空闲等待也在观察窗内；这些中位数不是“空首页静置”值。[最终 GUI 数据](../measurements/2026-11-20-helper-gui-0.1.3.json)

单位 KiB；10 MiB = 10,240 KiB。

| 环境 | 主 RSS 中位数 / 峰值 | 主 PSS 中位数 / 峰值 | 完整树 RSS 峰值 | 完整树 PSS 峰值 |
| --- | ---: | ---: | ---: | ---: |
| 默认 PATH，包含 mise shim | 11,316 / 17,780 | 7,300 / 13,326 | 59,884 | 48,651 |
| 明确指定系统 Python | 11,020 / 17,040 | 6,783 / 12,528 | 37,948 | 19,306 |

默认环境观察到的 mise 单映像 RSS 最大值为 43,088 KiB，实际 Python 单映像为 21,660 KiB。二者均保留在应用树中；单映像最大值不能相加为同一时刻的树峰值。改用系统 Python 是环境条件变化，不能记为代码优化。当前 GUI 每次启动的 CLI 都有自己的解释器发现过程，进程内缓存不能跨不同 CLI 进程复用。[映像归属与原始摘要](../measurements/2026-11-20-helper-gui-0.1.3.json)

默认环境 2820 样本中有 116 个含缺读，系统 Python 环境 2767 样本中有 132 个含缺读；两组的完整树中位数保持 `null`、计量状态保持 `needs_review`。功能通过与计量完整性分别判断。已读样本的 Swap、SwapPss 为零，缺读部分仍未知。GUI 实际采样间隔中位数约 8.24 / 8.18 ms，并非持续保证 5 ms。[完整性记录](../measurements/2026-11-20-helper-gui-0.1.3.json)

显示服务单列：两环境 Xvfb RSS 中位数分别为 38,992 / 38,732 KiB，PSS 为 32,746 / 32,496 KiB；相对启动前的中位数增量分别为 RSS +924 / +652 KiB、PSS −149 / +284 KiB。负增量没有被裁成零，也不能解释为释放了同等专有内存。观察线程 CPU 分别为 9.83 / 9.31 秒；GUI 测试控制器的内存未独立采样，xdotool、ImageMagick 和夹具侧 CLI 也不属于部署后的应用树。没有通过增加常驻后台进程转移应用开销。[显示与观察成本](../measurements/2026-11-20-helper-gui-0.1.3.json)

本轮最终 GUI 的 RSS/PSS 峰值仍超过目标，且与早期基线相比没有稳定的全流程峰值下降证据。大备份查询的局部收益不能代替整个 GUI 的内存验收。

## 本地测试与安装后窗口验收

| 验收 | 结果 |
| --- | --- |
| Cargo | 78 项应用 + 19 项核心测试全部通过 |
| Rust 静态门禁 | fmt；workspace、all-targets、all-features、locked/offline 的 Clippy，`-D warnings` 通过 |
| 受影响 Python | 世界 17、备份 16、服务器 6、计量 12，共 51 项通过；最终环境为 Python 3.14 |
| 真实 CLI | 管理文件流程 5 项；生命周期 21 条命令，包含 3 次预期保护拒绝 |
| 候选原生 GUI | 默认环境与系统 Python 环境各 6 组流程通过 |
| 官方安装器 | 安装、版本、卸载、修改文件保护、内外前缀合成游戏数据保留通过 |
| AUR | 来源、makepkg、namcap、桌面入口、文件布局、公开新克隆构建及正常系统升级通过 |
| 已安装 GUI | 使用 `/usr/bin/zmcl` 再跑 6 组真实窗口和文件效果检查，全部通过 |

上述测试没有触发日常远端 CI。发布采用仓库既有本地打包流程和官方 GitHub/AUR 通道。[源码与测试门禁](../validation/helper-release-0.1.3-2026-11-20.json) · [安装器](../validation/release-0.1.3-2026-11-20.json) · [已安装 GUI](../validation/installed-gui-0.1.3-2026-11-20.json)

[AUR 安装后的备份恢复窗口（历史附件）](https://drive.google.com/file/d/1jMOYzLeo1prfhObfF_XA1OE852OJKUNt/view)

恢复后两份快照均可见，底部明确提示保留了恢复前快照。实际区域文件已恢复为原字节。截图没有观察到重叠或裁切。[窗口与文件验收](../validation/installed-gui-0.1.3-2026-11-20.json)

[AUR 安装后的服务器 EULA 拒绝窗口（历史附件）](https://drive.google.com/file/d/1jMOYzLeo1prfhObfF_XA1OE852OJKUNt/view)

服务器保持 stopped，Stop 按钮不可用，底部显示需要用户阅读并设置 EULA 的错误；没有创建服务器进程记录。[窗口与文件验收](../validation/installed-gui-0.1.3-2026-11-20.json)

## 可复测入口

先在相同工具链和依赖环境构建表中的前后源码，记录实际二进制 SHA-256，再把绝对路径交给脚本。脚本要求输出目录不存在或为空，自动生成并校验夹具，保留每次 stdout/stderr、样本和报告，结束后移除临时游戏根目录。

~~~sh
/usr/bin/python3 -B scripts/bench_management_queries.py \
  --before /absolute/path/to/af14-imports-zmcl \
  --after /absolute/path/to/zmcl-0.1.3 \
  --python /usr/bin/python3 --repeats 3 --timeout 360 \
  --output .test-evidence/query-recheck

ZMCL_PYTHON=/usr/bin/python3 /usr/bin/python3 src/gui/manage_smoke.py \
  --binary /usr/bin/zmcl \
  --output .test-evidence/installed-management-recheck \
  --memory-output .test-evidence/installed-management-recheck/memory.json
~~~

默认 PATH 条件另跑一组，清除 `ZMCL_PYTHON` 覆盖并记录实际解释器映像。跨机器重编译不保证相同二进制摘要；比较时应保留各自真实身份和环境。脚本源摘要、夹具摘要、原始采样文件摘要和每轮缺读数已收录在测量 JSON 中。[基准实现](../../scripts/bench_management_queries.py)

## 剩余缺口与下一步边界

完整 GUI 低于 10 MiB 仍未完成。GUI 绘制/初始化峰值与通用 Python 运行时的成本仍需继续处理；把解释器或绘制工作移到未计量服务中不构成达标。后续底层图形修改应由 Project Z 当前上游负责人协调，并在真实后端和窗口中重新验收。

本轮使用合成世界、备份和未运行的服务器夹具，没有新增真实 Microsoft 账号登录、在线资源下载、Minecraft/服务器启动或 Windows/macOS 应用实机验收。0.1.2 的 Windows GNU 跨编译和 zgpui 平台探针保留其原版本身份。完整 XMCL 覆盖继续以 **XMCL v0.73.1 / aeb52403945f017a17f4014cb6542f73fa47dd15** 为固定基线，差距仍列在 [FEATURES.md](../FEATURES.md)，没有因本次内存改动提升未验收能力的状态。

本轮公开提交依次为 [6d47399](https://github.com/LIghtJUNction/zmcl/commit/6d47399a2d1d777ae2c6343ee81c21316c487b39)、[af14e2c](https://github.com/LIghtJUNction/zmcl/commit/af14e2c0f9b3a39e5b5a8a70976d2d0721b4400f)、[9ef89bb](https://github.com/LIghtJUNction/zmcl/commit/9ef89bb92d46d324172d6ea8b13054c81434e178)、[7dc09ff](https://github.com/LIghtJUNction/zmcl/commit/7dc09fff213d659d99f98e61ebb2706654c0160b)。其他维护者的 0.1.2 完成项和 2287ca8 文档提交保持在历史中，旧发行资产和旧测量没有改标为 0.1.3。
