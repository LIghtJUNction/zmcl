# Project Z 依赖与传播记录

日期：2026-11-20（Asia/Taipei）。

## 消费者已完成的部分

zmcl 已将原生 UI 子模块从 `86bb5eadd88d113eddceafcfda7b8a62f707c570` 更新为 zui-native 0.1.0 的正式源码 `5e37fcfb791eec9ac523fc19373e95d0a513112f`。该变化随消费者提交 [0850a642](https://github.com/LIghtJUNction/zmcl/commit/0850a64203b51eb207c1f0318d7e459f97734cb0) 推送，之后的修复继续保留此 pin。

| 关系 | 版本 / 来源 | 已完成检查 |
| --- | --- | --- |
| zmcl → zclap | 0.1.4 / `4d87f3ae3743e81509d5ac5500909955667c860f` | 精确版本、子模块、CLI 与集成测试 |
| zmcl-core → zconv | 0.4.0 / `0af1f7eb0766ff0ce9db5cee7c6184eff511a8c1` | 精确版本、子模块、解析和启动计划测试 |
| zmcl → zui-native | 0.1.0 / `5e37fcfb791eec9ac523fc19373e95d0a513112f` | 精确版本、子模块、构建、真实 GUI 与窗口属性 |
| 项目聚合 → zmcl | 等待聚合维护者发布正式登记提交 | 见下面的边界与接手记录 |

Cargo 使用路径依赖，因此源码身份由 Git 子模块 gitlink 固定；Cargo.lock 记录的包版本没有改变。已检查 lock 与实际依赖一致，没有为了制造“更新记录”而改写无变化的 lock。

当前应用代码冻结于 [033776287ab10aab90f46fd5b65309fecb9b8711](https://github.com/LIghtJUNction/zmcl/commit/033776287ab10aab90f46fd5b65309fecb9b8711)，最终 77 项 Rust 测试、严格 Clippy 与 release 构建通过。[构建证据](validation/build-checks-2026-11-20.json)记录版本、pin 和可执行文件摘要。

本次 zui 改动影响的实际窗口行为也已检查：

- `WM_CLASS` 为 `zmcl / Zmcl`。
- `_NET_WM_NAME` 为 UTF-8 的 `zmcl — Minecraft Launcher`。
- 最小内容尺寸与实际窗口均为 1120×760，符合调用者参数。
- GUI 使用真实本地构建完成启动、交互和退出后 Java 继续运行的验收。

[GUI 证据与性能记录](PERFORMANCE.md)保留具体测量身份；不能将 Windows/macOS 源码分支当成已通过对应系统的实机验收。

## 聚合登记的状态与接手者

**消费者升级已经完成；整个 Project Z 聚合传播暂不能宣称全部闭环。**

已向正在维护聚合仓的 **Project Z root 协调会话** 提交直接依赖关系与不可变消费者提交。该接手者已回执确认实际采用并核验 `0850a642` 与正式 zui `5e37fcf`，并负责统一登记 graph/gitlink，避免多个维护者修改同一活跃聚合树。

截至本报告的公开仓库检查，[project-z main 的 9d72d19c](https://github.com/LIghtJUNction/project-z/commit/9d72d19ce1e9e0a6f79f48c0fbc12abc77b2e7fd) 尚无 `zmcl` 条目。现有协调回执不能替代已发布的聚合提交。具体剩余边界是：聚合维护者需要将登记、最终消费者 pin、生成目录和完整图检查落到一个可回读的公开提交。

消费者提供的登记关系为：

```toml
[repositories.zmcl]
role = "application"
requires = ["zconv", "zclap", "zui"]
```

曾在隔离副本中完成登记演练与 `impact --repositories zui` 查询，zmcl 能进入反向依赖结果；该副本的全局检查同时遇到已有旧包名引用。未改写其他维护者的活跃树、未放宽全局检查，也未把这次局部演练当成生态全量闭环。

接手者应在其完整最新图中执行同步、解析、检查与影响分析，收集全部直接和间接消费者的实际验收及已推送提交。可复用的本消费者源码与证据均已列出，不依赖私人工作目录。后续正式聚合提交回读成功后，再更新此状态。

## zui-native 与 zgpui

用户提出了二者的命名关系。已同时核对两个正式仓库与 Project Z root 回执：

- [zui-native](https://github.com/LIghtJUNction/zui) 是使用 Rust std 和 C 桥接的原生窗口、输入与 OpenGL 控件库，Rust 导入名为 `zui`。
- [zgpui](https://github.com/LIghtJUNction/zgpui) 是独立的 `no_std + alloc` 场景、布局和 Renderer 契约库，由宿主提供窗口和绘制能力。

目前不存在可直接替换的已发布统一 API 或兼容迁移源码。因此本轮保持真实可编译的 zui-native 依赖，没有仅修改包名或文档品牌。若后续形成 zgpui 公共入口与平台后端适配层，需要按同一传播流程更新消费者并取得实际跨平台窗口证据，之后才能移除旧依赖。
