# Project Z 依赖与传播记录

日期：2026-11-20（Asia/Taipei）。当前应用为 zmcl 0.1.2；旧版本的构建身份和发布/测量记录保留在历史章节。

## 当前：zmcl 0.1.2

应用源码固定于 [e86b584](https://github.com/LIghtJUNction/zmcl/commit/e86b5841532ef04cc74cea5585ae396150452408)。

| 关系 | 精确版本 / gitlink | 本消费者证据 |
| --- | --- | --- |
| zmcl → zclap | 0.1.4 / `4d87f3ae3743e81509d5ac5500909955667c860f` | CLI 与应用测试 |
| zmcl-core → zconv | 0.4.0 / `0af1f7eb0766ff0ce9db5cee7c6184eff511a8c1` | 解析与启动计划测试；核心仍为 no_std + alloc |
| zmcl → zgpui | 0.1.5 / `4aba3ba83815253fb7e4013baa8bcf894439d32d` | Linux 完整原生窗口、Windows GNU 完整 GUI 跨编译 |
| zmcl → zi18n | 0.1.0 / `b90984b48465a764bcb699ad2bb3251fa7020024` | 九个不变英文标题实际借用静态文本；纯 CLI 构建不启用该依赖 |

manifest、Cargo.lock、gitlink 与实际导入一致。[最终应用验收](validation/gui-iteration-0.1.2-2026-11-20.json)包含 97 项 Rust 测试、fmt、严格 Clippy、Linux release、Windows GNU release、四组创建流程和六项管理流程。跨编译和上游原生探针不能替代应用的 Windows/macOS 实机验收。

### zgpui 0.1.5 真实发布

[正式发行流程](https://github.com/LIghtJUNction/zgpui/actions/runs/44713398127)最终 12 个任务全部成功。消费者另行读取 registry/index、下载 `.crate` 并核验 VCS：0.1.5 未撤回，88,287 字节、38 个文件，SHA-256 `7a5597b9018e724bcab318e8e52de11f70e1686262692d32d84b1fce30044300`，tag 与 `.cargo_vcs_info.json` 均指向 `4aba3ba83815253fb7e4013baa8bcf894439d32d`。见[完整发布回执](validation/zgpui-0.1.5-release-2026-11-20.json)。

### 本轮传播范围与未完成项

截至 2026-11-20 18:08（Asia/Taipei）的 GitHub 只读核验，以下为各消费者实际 main 的状态；不能由子项目最新 main 推断套件已更新。后续维护者提交应另行追加，不能覆盖此观察时点。

| 消费者 | 当时 main | 实际 zgpui 消费 | 后续责任 / 阻塞 |
| --- | --- | --- | --- |
| zmcl | `e86b5841532ef04cc74cea5585ae396150452408` | 0.1.5 / `4aba3ba` | 本轮已完成源码接入与应用验收 |
| ztype | `34fa1b724dd59e5610bfc81dccf0191447344683` | 固定 0.1.2 / `5b268da` | Project Z 协调维护者接下一轮 manifest、lock、gitlink 与消费者验收 |
| zcall-gui | `92cbaedce3c378331d44bef568c04485bad74ee7` | 固定 0.1.4 / `7838c3c` | 原 GUI 维护者的 0.1.2 发行已冻结于 0.1.4；0.1.5 需后续独立更新 |
| zterm | `b5adc3dbc5ca5f28a3ea7e45650a45245be9dc2d` | manifest 范围可容纳 0.1.5，实际 lock 仍为 0.1.2 | Project Z 协调维护者更新 lock 并验证实际消费 |
| ztty | `76fa2cfd13b776c027d5a86fadafde8e035f88f2` | 直接固定 0.1.2，另经 zterm 传递消费 | 原维护者的 0.5.2 发行已冻结；需完成下一轮直接与传递更新 |
| oh-my-ztty | `8efa280a43229870a12098626a9e9143709bb231` | 固定 ztty `c4f8f48` → zterm `a2074ba` → zgpui 0.1.0 / `1d796d1` | 套件维护者需更新组件 gitlink 与 dependencies.lock/发行资产 |
| zcalld | `5cdfcaedab4a2d02b7d3b341528606c47a92e1bf` | companion GUI 固定 `e6ed24b` → zgpui 0.1.2；daemon 自身不链接 GUI | 原维护者需更新 `apps/zcall-gui`；不能按 GUI 仓库最新 main 宣称完成 |

本轮已提供可复用的真实发布提交 `4aba3ba`、精确 crate checksum、应用提交与限定性能证据。Project Z 聚合维护者拥有聚合树写入权，其他消费者由各自维护者继续接入；当前没有“全 Project Z 已升级至 0.1.5”的回执。[聚合依赖审计阶段](https://github.com/LIghtJUNction/project-z/commit/8c789ef69297ca344f667cbe1bcdf223d79babf2)登记了 zi18n，并核对真实依赖关系；聚合候选和 canonical main 的 pin 不混称。


### 聚合维护者后续确认

聚合维护者已接受本轮 0.1.5 发布回执并接回上游后续协调范围；本会话仅继续 zmcl 分发与安装验收。[聚合提交 b54f083](https://github.com/LIghtJUNction/project-z/commit/b54f083e2b7900c2f5c93ec93e26366526645095)已通过 GitHub 插件独立读回：它登记了 `zmcl → zi18n`，固定的是较早的 `2b1218f` 英文标题消费，`zgpui` 聚合 pin 仍为 0.1.2 / `5b268da`。该提交没有把最终 zmcl 0.1.2 / `e86b584` 或框架 0.1.5 全面传播。下一轮消费者源码、lock、gitlink 和套件资产更新由各原维护者及 Project Z 聚合维护者继续负责，现有冻结发行保持不变。

## zmcl 0.1.1 / zgpui 0.1.2 历史依赖

| 关系 | 精确版本 / 源码 | 本消费者证据 |
| --- | --- | --- |
| zmcl → zclap | 0.1.4 / `4d87f3ae3743e81509d5ac5500909955667c860f` | CLI 与本地集成检查 |
| zmcl-core → zconv | 0.4.0 / `0af1f7eb0766ff0ce9db5cee7c6184eff511a8c1` | 解析、启动计划与 no_std 目标检查 |
| zmcl → zgpui | 0.1.2 / `5b268da910639abd2fb8e581acb4f6eed50b7516` | 实际原生窗口、CPU 绘制、管理页文件往返与完整进程树测量 |

Cargo 依赖名、vendor gitlink 和 Rust 导入统一为 `zgpui`，实际窗口入口为 `zgpui::native`，启用 `native`、`linux-software`。Linux 在客户端用 CPU 绘制，通过 X11 提交，不创建 OpenGL 上下文或常驻绘制进程。原 `vendor/zui` 和 `zui-native` 不再是活动依赖。核心保持 `no_std + alloc`。

## zgpui 0.1.2 的发布核验

Project Z 协调者将本次上游整合和正式发布交给现有 zmcl 维护会话，完成后归还上游写入范围。[正式发布流程](https://github.com/LIghtJUNction/zgpui/actions/runs/44695292166)的 12 个任务全部成功，覆盖 Linux、macOS、Windows 核心和原生路径，以及两个 no_std 目标。

消费者另外读取 crates.io 索引并下载真实发行包：版本 `0.1.2` 未撤回；`.crate` 为 **68,613 字节、34 个文件**，SHA-256 为 `0c5cddf5d0ed86ccb9cf21e035585146cf5aff0e316301bc037684b3c51b1046`，与索引一致；`.cargo_vcs_info.json` 指向 `5b268da910639abd2fb8e581acb4f6eed50b7516`。见[发布证据](validation/zgpui-0.1.2-release-2026-11-20.json)。

这次改动释放字体匹配完成后的临时 Fontconfig 状态，复用已打开的 FreeType 字体；CPU 帧经 XPutImage/XFlush 提交后释放其驻留页，下一帧重新完整绘制。上游增加了字体与帧缓冲生命周期的 ASan/UBSan 检查，以及真实窗口像素、Resize/Expose、重复提交和分配失败恢复检查。绘制仍需要完整像素表面；这些实现本身不能证明整个应用在所有状态低于 10 MiB。

zmcl 0.1.1 的应用源码固定于 [35eb8a3](https://github.com/LIghtJUNction/zmcl/commit/35eb8a3242a6ba637266ba1480354138389c9cb0)。Linux 本机通过 85 项 Rust 测试、严格 Clippy、no_std 核心目标、13 项打包元数据测试、7 项基础测量测试、21 项安装器测试和5项管理文件往返。原生流程和内存边界分别记录于[验收报告](VALIDATION.md)和[性能报告](PERFORMANCE.md)。上游三平台发布门禁不等于 zmcl 已完成 Windows/macOS 桌面验收。

## zgpui 0.1.2 历史传播记录

zmcl 的 manifest、gitlink、lock、打包和窗口验收已由本会话完成。ztype 的 0.1.2 消费源码已由 Project Z 协调者接入 [main 9dd67a5](https://github.com/LIghtJUNction/ztype/commit/9dd67a54ac536b22ab0fe4bd008d6b5cdd95c369)，其源码树与通过13项Rust、10项core-only、14项CLI及11项真实原生窗口检查的候选完全相同。独立GitHub回读确认 [zcall-gui main e6ed24b](https://github.com/LIghtJUNction/zcall-gui/commit/e6ed24bdd5d7d193d76fc03a53d2bc1e10edec74) 的manifest、lock和gitlink共同指向0.1.2/5b；[zcalld main 7818b3f](https://github.com/LIghtJUNction/zcalld/commit/7818b3f37e660bc70e8afd3e13d0c3464b783a1d) 的 `apps/zcall-gui` 已固定该e6提交。它们的本地验收由原维护者负责，本会话没有重新运行或代签。

最终聚合仍由Project Z主协调者负责。收尾只读时，聚合工作树处于干净的 `79607a87d08937724aad371510bfe9b508ff1d85`；本会话尚未取得包含上述所有新消费者的最终聚合回执。保留这个边界，不能用已通知或单个应用通过代替聚合提交。后续0.1.3字体/调色板接口已有独占维护者，本会话不再修改上游活动树。

聚合图应登记 zmcl 的真实依赖：

```toml
[repositories.zmcl]
role = "application"
requires = ["zconv", "zclap", "zgpui"]
```

## 首次统一到 zgpui 0.1.1 的历史记录

第一次原生统一发布为 `0.1.1` / `cb50f2fe554622fdc984ae7fec9bcd844f175d67`，对应[原正式流程](https://github.com/LIghtJUNction/zgpui/actions/runs/44685655367)。真实 `.crate` 为57,957字节，SHA-256 `75d0c780f8139a6d8db1b7c9d0de64c51c55b2feb4a6973f743380799df7fcea`。

[首次消费验收](validation/zgpui-consumer-2026-11-20.json)基于 GUI 提交 [9206b836](https://github.com/LIghtJUNction/zmcl/commit/9206b8366324bdaf701c2890fcdafb2656f7d100)，记录63项应用测试、19项核心测试、严格 Clippy、no_std 检查、5项文件回归和6项窗口操作。该候选未作为独立 AUR 版本发布，数据不改标为0.1.2或最终低内存结果。

## v0.1.0 历史记录

v0.1.0 使用 zui-native 0.1.0 / `5e37fcfb791eec9ac523fc19373e95d0a513112f`，应用源码为 [0337762](https://github.com/LIghtJUNction/zmcl/commit/033776287ab10aab90f46fd5b65309fecb9b8711)。[77项Rust检查和构建记录](validation/build-checks-2026-11-20.json)、[原生窗口与退出后Java生命周期](measurements/2026-11-20-gui-exit-and-window.json)和原性能样本继续保留，描述的仍是旧二进制。
