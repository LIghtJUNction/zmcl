# Project Z 依赖与传播记录

日期：2026-11-20（Asia/Taipei）。本页描述主线源码；已发布 v0.1.0 与 AUR 0.1.0-1 的历史身份保持不变。

## 当前实际依赖

| 关系 | 精确版本 / 源码 | 本消费者证据 |
| --- | --- | --- |
| zmcl → zclap | 0.1.4 / `4d87f3ae3743e81509d5ac5500909955667c860f` | CLI 与本地集成检查 |
| zmcl-core → zconv | 0.4.0 / `0af1f7eb0766ff0ce9db5cee7c6184eff511a8c1` | 解析、启动计划与 no_std 目标检查 |
| zmcl → zgpui | 0.1.1 / `cb50f2fe554622fdc984ae7fec9bcd844f175d67` | 实际原生窗口、CPU 绘制、管理页文件往返 |

Cargo 依赖名和 Rust 导入统一为 `zgpui`，窗口入口为 `zgpui::native`。启用 `native`、`linux-software`；Linux 的 CPU 后端自行绘制并用 X11 提交，没有 OpenGL 上下文或常驻绘制进程。原 `vendor/zui` gitlink 和 `zui-native` 包名已从活动依赖中移除，`vendor/zgpui`、Cargo.toml、Cargo.lock 和实际导入一并更新。核心仍为 `no_std + alloc`。

## 已发布源码的核验

上游由 Project Z root 统一发布。[正式发布工作流](https://github.com/LIghtJUNction/zgpui/actions/runs/44685655367)对应上述源码。消费者另行核对 crates.io sparse 索引、下载包 SHA-256 和 `.cargo_vcs_info.json`：

- 版本 `0.1.1`，未撤回。
- `.crate` 为 57,957 字节，SHA-256 `75d0c780f8139a6d8db1b7c9d0de64c51c55b2feb4a6973f743380799df7fcea`。
- 发布包中的源码提交为 `cb50f2fe554622fdc984ae7fec9bcd844f175d67`；包含实际 Linux 原生和 CPU 栅格实现。

[本地消费验收](validation/zgpui-consumer-2026-11-20.json)记录独立构建身份、63 项应用测试、19 项核心测试、严格 Clippy、`x86_64-unknown-none` 核心检查、5 项真实文件回归和6项原生窗口操作。该候选基于 GUI 提交 [9206b836](https://github.com/LIghtJUNction/zmcl/commit/9206b8366324bdaf701c2890fcdafb2656f7d100)，不是已安装的新 AUR 版本，也不单独证明低于 10 MiB。

## 传播责任与状态

统一后的上游和其他实际消费者（zcall-gui、ztype，以及携带它们的 zcalld 与聚合图）由 Project Z root 协调。本会话负责 zmcl 的代码、gitlink、lock、打包和实际窗口验收。zmcl 在聚合图中的真实依赖应登记为：

```toml
[repositories.zmcl]
role = "application"
requires = ["zconv", "zclap", "zgpui"]
```

消费者提交完成后将精确 SHA 回传给协调者。上游发布成功与本应用通过不能替代其他消费者的门禁，也不能据此宣称 Project Z 的完整影响图已经闭环。聚合仓需要由其维护者发布并回读最终登记；本页不把内部协作回执作为已完成的聚合提交。

## v0.1.0 历史记录

v0.1.0 使用 zui-native 0.1.0 / `5e37fcfb791eec9ac523fc19373e95d0a513112f`，应用发行源码为 [0337762](https://github.com/LIghtJUNction/zmcl/commit/033776287ab10aab90f46fd5b65309fecb9b8711)。其 [77 项 Rust 检查和构建记录](validation/build-checks-2026-11-20.json)、[原生窗口与退出后 Java 生命周期](measurements/2026-11-20-gui-exit-and-window.json)、[原性能样本](PERFORMANCE.md)继续保留。旧记录描述旧二进制，不能因当前依赖统一而改写为 zgpui 的结果。
