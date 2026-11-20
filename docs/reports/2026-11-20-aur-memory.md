# zmcl 0.1.1：AUR 发行、原生 GUI 与内存验收

日期：2026-11-20（Asia/Taipei）。本报告对应实际发布的 Linux x86_64 开发预览，应用源码固定于 `35eb8a3242a6ba637266ba1480354138389c9cb0`。

**AUR 可安装发行已经完成，开发机已正常升级并通过安装后原生窗口操作。GUI 全状态低于10 MiB仍未完成：本次空库首页静置RSS为9.55 MiB，重绘、启动和管理任务会超过目标。** 下文保留主进程、相关进程树、换页、显示服务和采样缺口，避免把一个空闲数字解释为整个应用的保证。

## 1. 安装入口与发行身份

- [AUR zmcl-bin 0.1.1-1](https://aur.archlinux.org/packages/zmcl-bin)
- [GitHub v0.1.1 Linux x86_64 预览包](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.1)
- [项目网站](https://lightjunction.github.io/zmcl/)
- [完整安装说明](https://github.com/LIghtJUNction/zmcl/blob/main/docs/INSTALL.md)

```sh
yay -S zmcl-bin
# 或
paru -S zmcl-bin

zmcl --version
zmcl gui
```

不使用helper时，可从公开AUR仓库克隆并执行 `makepkg -si`。包管理器管理 `/usr` 下的程序、桌面入口、图标和文档；用户的实例、存档与账号数据在应用数据目录中。若系统同时存在用户级安装，用 `command -v zmcl` 核对实际选择的程序。

| 对象 | 实际身份 |
| --- | --- |
| 应用源码 / tag | `35eb8a3242a6ba637266ba1480354138389c9cb0` / `v0.1.1` |
| zgpui | 正式0.1.2 / `5b268da910639abd2fb8e581acb4f6eed50b7516` |
| 官方归档 | 664,850字节；SHA-256 `c61a83ce918d7fe3d58a080bc41bc1c5f301c259396cc4a96a5acf81c3ee9f0b` |
| 主程序 | 1,439,712字节；SHA-256 `a28998952b42d09f652ac95da47ddb15930cd556b6fa4e9b7e7ebbd23a4ac2e9` |
| 归档展开内容 | 7个常规文件，内容合计1,490,322字节；不含文件系统分配和归档头 |
| AUR master | `15bdd71d63db740fadf13edbe9d876f2fb0f1bc1`，parent `e1fd82532d8b8a8b9548386fda9fd6305fd232da` |
| 预编译运行条件 | Linux x86_64，glibc≥2.39，Fontconfig≥2.17.0，FreeType、X11/XWayland |

以上体积不包含系统动态库、curl/Python、Java、Minecraft、mods、用户数据或构建目录。ABI下限来自已发布ELF的实际符号引用，不是所有Linux发行版的兼容性保证。源码的字体库回退逻辑不能改变已编译文件的符号要求。[发行与安装证据](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/release-0.1.1-2026-11-20.json)

官方发行包由固定源码生成，打包工作树未改动；草稿资产下载回读逐字节一致后公开，再从未认证的公开HTTPS地址独立下载核对。正式资产没有为文档更新而被替换。

## 2. AUR 和已安装窗口的实际验收

从公开HTTPS重新克隆AUR的15bdd提交，完成来源校验、`.SRCINFO`重新生成一致性、makepkg、namcap、桌面入口、安装布局和动态库解析检查。随后正常执行包升级0.1.0-1→0.1.1-1；升级前后 `pacman -Qkk` 都为19个受管文件、0个被改动。没有执行全系统升级。namcap报告curl和Python可能未使用，但程序确实按需执行这两个外部工具，因此依赖声明保留。

发布后第一轮RPC和两个helper仍显示旧版本，记录没有删除。2026-11-20 16:30:32，current/legacy/search三种官方RPC、`yay -Si`和`paru -Si`均回读到0.1.1-1及新依赖。[AUR公开证据](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/aur-install-0.1.1-2026-11-20.json)

对实际 `/usr/bin/zmcl` 另做了原生窗口检查：在独立数据目录打开1120×760窗口，点击新建实例，输入 `aur-0-1-1-check`、版本1.21.1和Vanilla，观察成功通知；CLI的list/show读回完全一致。随后进入管理页，确认Worlds、Backups、Resources入口，点击Quit并检查窗口列表为空，最后停止本轮私有显示。没有下载游戏、登录账号或启动服务器。

![AUR安装后的原生窗口成功创建实例](https://raw.githubusercontent.com/LIghtJUNction/zmcl/main/docs/validation/images/zmcl-0.1.1-aur-installed.jpg)

用户此前反馈“用不了”的具体失败步骤尚未提供。这次结果证明记录的安装和窗口路径可用，不能直接宣称已复现并修复那个尚未定位的问题。[安装后窗口记录](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/installed-gui-0.1.1-2026-11-20.json)

## 3. 原生 GUI 新增了什么

| 界面 | 已接入的操作 | 本轮实测边界 |
| --- | --- | --- |
| 世界 | 目录/ZIP导入、ZIP导出、复制目录路径 | 合成存档导入、导出和回导往返；不是实际游玩测试 |
| 备份 | 列表、创建、确认恢复，恢复前保留快照 | 文件内容恢复并保留pre-restore快照；真实窗口通知已核对 |
| 资源 | 已管理/未托管资源展示，按Modrinth项目ID或slug添加、更新、启停、移除和恢复 | 本轮窗口覆盖未托管资源保留；在线GUI全链路仍有缺口 |
| 服务器 | 创建、安装、启停、刷新状态 | 本轮验证未接受EULA时的拒绝；没有在这组夹具中启动真实服务器 |

这些操作沿用现有CLI和文件保护逻辑，GUI只在用户操作时启动任务，任务完成后退出；没有增加常驻后台服务。异步任务、字面量参数传递和过期列表保护避免界面状态把用户操作应用到错误实例。按需子进程的内存仍在后面的相关树中统计。

完整功能表继续固定XMCL **v0.73.1** / `aeb52403945f017a17f4014cb6542f73fa47dd15`。世界克隆/详细信息、完整资源搜索与历史恢复、服务器配置/探测、部分设置、无障碍及跨平台桌面验收等仍需继续补齐。[功能矩阵](https://github.com/LIghtJUNction/zmcl/blob/main/docs/FEATURES.md)

## 4. 主进程与完整相关进程树的内存

单位为KiB，10 MiB=10,240 KiB。静置值先取每轮11个样本的中位数，再取三轮中位数。峰值是采样观察到的下界，各列分别取最大值；对多个进程的顺序读取不是原子快照。

| 场景 | 主RSS | 主PSS | 相关树RSS | 相关树PSS |
| --- | ---: | ---: | ---: | ---: |
| 空库首页静置中位数 | 9,784 | 4,647 | 9,784 | 4,647 |
| 空库静置样本最大值 | 9,864 | 5,085 | 9,864 | 5,085 |
| 连续重绘观察峰值 | 13,192 | 8,256 | 13,192 | 8,256 |
| 管理测试启动阶段峰值 | 16,972 | 9,989 | 16,972 | 9,989 |
| 六项管理动作，排除启动 | 13,892 | 9,306 | 60,860 | 49,269 |

RSS统计实际驻留的页；把多个进程RSS相加可能重复计算共享页。PSS将共享页按使用者分摊，因此两者一起报告。空库闲置和重绘阶段只有一个应用进程，管理阶段最多同时三个。管理全程主RSS中位数是10,424 KiB，也高于阈值。

最大的完整相关树样本出现在备份恢复阶段：GUI的RSS/PSS为10,420/5,260，GUI启动的CLI为5,256/1,454，CLI启动的Python为45,184/42,555；同一次完整读取相加得到60,860/49,269，即59.43 MiB RSS和48.11 MiB PSS。没有把不同时间的独立峰值叠加，也没有把Python排除来宣称达标。

### 条件和不能省略的成本

测量于2026-11-20 15:48:13–15:50:49运行，Linux x86_64、1120×760窗口、私有Xvfb、zgpui原生CPU后端。三轮各预热20秒，采11个一秒间隔的静置样本；再在6秒内以60 Hz请求360次Expose，事件可能合并，最后观察恢复静置。没有清空系统缓存，没有启动Minecraft或Java。

- 三轮活动样本完整。管理流程共2,938条样本，129条包含已退出或不可读的相关进程；记录保留为 `needs_review`，完整树中位数和完整CPU总量为 `null`，未知值不当作零。
- 第二轮恢复静置发生换页：应用PSS阶段中位数3,301 KiB，SwapPss 1,336 KiB，逐样本相加后中位数4,637 KiB；Xvfb SwapPss 8,432 KiB。驻留下降不能当作优化收益。
- Xvfb启动前RSS为37,620–37,900 KiB，应用静置时RSS增量1,248–1,512 KiB、PSS增量1,061–1,269 KiB。显示服务的绝对值及before/during差值单列，不把已有桌面成本算作零。
- 33个宿主快照都观察到外部编译任务；MemAvailable最低2,009,508 KiB，内存PSI avg10最大some=4.34/full=3.87。没有将全机压力归因给zmcl。
- 三轮6秒重绘的GUI CPU为3.38/3.21/2.68秒，Xvfb为0.60/0.56/0.46秒，测试控制器约3.18/3.11/2.82秒。管理观察窗约30.94秒，主CPU10.04秒，树共同身份增量观察下界10.96秒；观察线程自身约15.55秒CPU。
- 测试端xdotool、ImageMagick、夹具CLI及测试回导CLI不属于部署后的应用，范围单列；现有脚本未独立采样这些驱动的RSS/PSS。应用自己启动的CLI/Python计入相关树。

三轮进程的生命周期VmHWM为17,204/16,540/15,832 KiB，管理进程17,064 KiB；不能只看静置样本忽略启动。最后核对32个已观察应用/显示进程身份均已退出。所有关键数值和缺读标记已由第二位维护者从原始数据独立重算。[完整方法](https://github.com/LIghtJUNction/zmcl/blob/main/docs/PERFORMANCE.md) · [可公开汇总](https://github.com/LIghtJUNction/zmcl/blob/main/docs/measurements/2026-11-20-zmcl-0.1.1-summary.json)

## 5. 为什么占用下降，下一步应改哪里

本版实际接入 `zgpui::native` 的CPU窗口后端。它不创建OpenGL上下文，因而无需为这个简单界面加载原来那条软件OpenGL渲染路径。字体匹配完成后释放临时Fontconfig状态，并复用打开的FreeType字体。X11完成帧复制后释放客户端像素页，下一帧再完整绘制；没有把绘制移到新后台进程。

重绘仍需要完整像素表面：1120×760×4就是3,404,800字节。启动时的字体初始化、绘制期间的像素/文字缓存和访问管理页面后的常驻状态都还需要优化。操作峰值中Python占树PSS约86.4%，因此只继续削减空首页无法解决文件操作期成本。后续应在保留路径校验、恢复快照和错误处理的前提下，逐步替换高驻留文件辅助路径；不能靠隐藏辅助进程来完成目标。

旧版0.1.0与XMCL的比较保留原始源码、窗口尺寸、图形环境和换页数据，没有把它们改标为本次0.1.1结果，也没有据此给出所有设备、所有场景下的固定性能倍数。

## 6. 本地门禁与正式流程

| 检查 | 结果 |
| --- | --- |
| Rust应用与核心 | 66+19，共85项通过 |
| 格式、严格Clippy | `cargo fmt --all -- --check`；workspace/all-targets/all-features Clippy拒绝警告，通过 |
| 核心no_std | `x86_64-unknown-none`目标检查通过，仍为no_std+alloc |
| 受影响Python | 打包元数据13、测量7、安装器21、GUI管理文件往返5项通过 |
| 原生GUI | 6项合成夹具流程、3轮活动测量、安装后实例创建/CLI回读/退出通过 |
| 用户级分发安装 | 安装、版本执行、卸载、改动文件保护拒绝及模拟游戏数据保留通过 |
| AUR | 来源、构建、布局、host升级、公开Git/RPC/helper回读通过 |
| zgpui正式发行 | 12个正式发布任务成功，包含三平台原生/core和两个no_std目标 |

日常验收在本地执行，没有触发远端日常测试/CodeQL来代替这些结果。上游正式发行流程和官方Pages发布各自按用途执行。上游三平台通过不代表zmcl已经完成Windows/macOS桌面验收。旧版真实游戏菜单、loader和离线身份记录继续保留在[验证文档](https://github.com/LIghtJUNction/zmcl/blob/main/docs/VALIDATION.md)，本次没有冒充重新运行。

## 7. 上游协调与分批提交

按照统一命名要求，原zui-native独立发布已停止，冻结CPU改动和验证得到保留。随后Project Z主协调者将zgpui整合/0.1.2正式发布明确交给现有维护会话，完成后已归还上游范围；活动依赖、gitlink、lock和Rust导入统一为 **zgpui**，不是只改包名而不接窗口。核心仍保持no_std+alloc，zgpui默认仍没有Cargo外部依赖。

| 范围 | 提交 / 状态 |
| --- | --- |
| GUI管理入口 | [`9206b836`](https://github.com/LIghtJUNction/zmcl/commit/9206b8366324bdaf701c2890fcdafb2656f7d100) |
| 实际统一原生后端 | [`fcc214bf`](https://github.com/LIghtJUNction/zmcl/commit/fcc214bfb01072607b4ca17527dcc8c87ec1d224) |
| 字体ABI打包修正 | [`ce70fef`](https://github.com/LIghtJUNction/zmcl/commit/ce70fef498cb0a5e1bab530cddc80e2238252a40) |
| 最终0.1.1应用与测量脚本 | [`35eb8a3`](https://github.com/LIghtJUNction/zmcl/commit/35eb8a3242a6ba637266ba1480354138389c9cb0) |
| 已发布AUR配方同步 | [`d7485de`](https://github.com/LIghtJUNction/zmcl/commit/d7485defab8e8fa398a6fd272bad3ffcc183a7d7) |
| 发行、内存与安装证据 | [`36fb2bb`](https://github.com/LIghtJUNction/zmcl/commit/36fb2bb79d4cc0a77a16905d729ad18db68b7d18) |
| 网站新版发布 | [587feab](https://github.com/LIghtJUNction/zmcl/commit/587feab8ffd01e9c627419c8057fcc5fbd2e6894) |
| zgpui 0.1.2 | [`5b268da`](https://github.com/LIghtJUNction/zgpui/commit/5b268da910639abd2fb8e581acb4f6eed50b7516)，[正式流程](https://github.com/LIghtJUNction/zgpui/actions/runs/44695292166) |

以上GitHub批次通过插件提交、推进main并回读远端，再逐文件同步本地，未覆盖原有 `.playwright-mcp/`。开发保持main为唯一分支；临时隔离候选使用detached工作树。开发连接换代时只复用现有维护终端，没有派生CLI模型、新模型或递归接力。

消费者传播中，ztype已接入main `9dd67a54ac536b22ab0fe4bd008d6b5cdd95c369`；GitHub独立回读zcall-gui main `e6ed24bdd5d7d193d76fc03a53d2bc1e10edec74` 的manifest/lock/gitlink一致消费0.1.2；zcalld main `7818b3f37e660bc70e8afd3e13d0c3464b783a1d` 已固定该GUI提交。最终聚合仍由Project Z主协调者独占，本会话尚未获得覆盖全部新消费者的最终聚合回执，不以“已通知”代替闭环。后续0.1.3字体/调色板API已有原维护者负责，本轮不覆盖其活动树。[依赖传播记录](https://github.com/LIghtJUNction/zmcl/blob/main/docs/DEPENDENCIES.md)

## 8. 网站与视觉验收

网站继续使用简洁文档布局、系统字体和本地资源，按安装、首次使用、现有能力、真实窗口、测量和剩余工作组织内容。下载、版本、ABI和内存边界均更新到实际发行；旧XMCL比较仍保留历史身份。展示的备份页面是原始1120×760窗口截图，恢复成功通知和pre-restore快照都可见。

Google Chrome 158.0.8127.36在1440×1000、390×844和320×760三个桌面浏览器视口完成检查：没有页面横向溢出，9次实际复制逐字一致，18次目录导航通过；data.json实际导航、剪贴板不可用回退、禁用JavaScript后的正文、原生折叠区和导航也通过。保存18张截图，并人工查看了桌面、320宽资源表、390宽禁JS首页及原始GUI图。窄屏是视口模拟，不能当作实体手机或完整无障碍认证。

15条外部HTTPS链接全部返回200。HTML页面没有脚本/控制台错误；Chrome查看裸JSON时额外探测favicon.ico得到404，已单列保留。独立Chrome、HTTP服务和临时配置已清理。[网站QA记录](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/site-0.1.1-2026-11-20.json)

官方Pages流程：[44705666617](https://github.com/LIghtJUNction/zmcl/actions/runs/44705666617)，最终状态 **success**。2026-11-20 16:52:48，直接GET线上常规URL，首页HTML、data.json、CSS、复制脚本、原始PNG和SVG共6项全部HTTP 200，下载字节与验收源码逐一一致，未使用查询参数绕开缓存。[线上回读证据](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/site-0.1.1-live-2026-11-20.json)

## 9. 仍然存在的缺口

全状态与完整相关进程树低于10 MiB仍是首要未完成目标。还没有硬件GPU条件下的同口径数据、Windows/macOS实际应用窗口验收、大型整合包与长时间负载数据，也没有本轮真实Microsoft登录、Realms或多人连接验收。完整XMCL功能覆盖仍在逐项推进，不能以这次AUR可安装或六个GUI流程通过替代。

当前发行包内文档固定于发行源码，主线文档继续修正和补充；实际依赖与产物身份以包内release.json和上述哈希为准。全部新测量和旧版证据按版本分别保存。后续优化应继续在同样公开的口径下复测主进程、辅助进程、显示与观察器成本，并保留失败和不完整样本。
