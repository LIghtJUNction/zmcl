# zgpui 固定源码同步验收

日期：2026-11-20。应用基线为 [`600e7779`](https://github.com/LIghtJUNction/zmcl/commit/600e7779fbbeee9d4a0e599a09f3e334cec42f2f)，包版本保持 `0.1.4`。本轮将 `vendor/zgpui` 从 `4aba3ba` 推进到 Project Z 统一来源 [`b4d5493`](https://github.com/LIghtJUNction/zgpui/commit/b4d5493e0cd985d00558c7be0b5726965ab5163a)。manifest 原本已经精确选择 zgpui `0.1.5`，所以 Cargo.toml 与 Cargo.lock 均无改动。[机器可读证据](../validation/dependency-source-sync-20261120.json)

## 验收安排

- 固定上游提交、源码树和其他依赖，核对实际变更。
- 运行本地 Rust 门禁与真实 Release 构建。
- 用独占 Xvfb 和空白应用目录检查原生软件窗口，再记录结论边界。

## 输入与结果

上游树为 `caf6d3c249c04f4547aa6556afb03c0c91da5e65`，包含共享 raster 提取与字体位图格式/步长修复；原生消费仍启用 `native`、`linux-software`。应用验证树为 `b220998ca50eecec5e998d3e52fea389b40f6c67`。在该树中，仅 `vendor/zgpui` 相对基线变化；其余应用实现、zconv、zclap 和 zi18n `b90984b` 保持原来源。本文及证据追加不改变该运行源码。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| `cargo fmt --all -- --check` | 通过 | JSON 的 `commands` |
| `cargo test --workspace --locked` | 92 项应用 + 22 项核心，共 114 项通过；0 失败、0 忽略 | `tests` 和实际测试结果行 |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | 通过 | `commands` |
| `cargo build --release --locked` | 通过 | `commands` 和 `binary` |
| 私有 Xvfb 软件窗口 | 可见窗口、1120×760 几何、空闲、强制 Expose、恢复空闲全部完成 | `gui` |
| 索引差异检查 | 通过；源码差异仅共享 gitlink | `validated_source_tree` |

实际测试二进制为 **1,505,192 字节**，SHA-256 `3a5a518c7bd293b81237efa345b19276bfa02361e458b02a2c67fa3bf1c58997`。GUI 脚本在采样前后核对这一二进制身份，并保存窗口截图摘要；使用新建测试目录和测试独占的 X11 服务。

第一次上游 fetch 遇到 TLS unexpected EOF，退出码为 128，编译尚未开始。恢复从本轮自有 ztty 隔离子模块读取同一不可变 Git 对象，再核对上述上游树；随后所有本地门禁通过。原始失败保留在开发机回执，公开记录仅保留脱敏摘要。

## 结论与限制

本轮支持“zmcl 当前 Linux 软件窗口能够消费统一的 zgpui 来源”这一结论。应用既有功能与性能实现没有改动，历史发行、AUR 安装和游戏验收记录保持原身份。

GUI 使用单轮、短时参数验证窗口与重绘路径，不据此声称内存或性能改善。本轮没有新增账号、游戏、服务器、系统安装、Windows/macOS 应用运行或发行验收；未修改的 Python 辅助代码没有重复执行完整套件。上游独立的字体格式和 sanitizer 门禁见 [zgpui PR #1](https://github.com/LIghtJUNction/zgpui/pull/1)，它与本应用窗口检查各自保留范围。

自审：贡献是统一不可变来源；表格给出每项结论的证据；真实本地构建和窗口运行支持消费判断；平台与性能范围明确；保持既有版本、特性与其他固定来源，未放松门禁。
