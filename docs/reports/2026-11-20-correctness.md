# zmcl 正确性迭代与验收记录

日期：2026-11-20（Asia/Taipei）  
源码范围：四批修复已推送至 `main`，累计源码提交 [`a5693c0`](https://github.com/LIghtJUNction/zmcl/commit/a5693c0290ac9cc4311ed5242d48405b84294cf0)。

| 修复批次 | 提交 |
|---|---|
| 官方版本名称 | [`e5ef3f4`](https://github.com/LIghtJUNction/zmcl/commit/e5ef3f48095080183e8876ea5260596b8a503129) |
| 世界存档往返 | [`02ebb5a`](https://github.com/LIghtJUNction/zmcl/commit/02ebb5af7425aa25f738c3af26c69c4c17bc97c3) |
| Forge／NeoForge 精确 profile | [`cde308c`](https://github.com/LIghtJUNction/zmcl/commit/cde308cd8c760552c94bfa340671d60fdf2c30f6) |
| 服务器启动失败清理 | [`a5693c0`](https://github.com/LIghtJUNction/zmcl/commit/a5693c0290ac9cc4311ed5242d48405b84294cf0) |

本轮修复四条实际使用路径：含空格的官方版本名称、世界存档导出后重新导入、Forge／NeoForge 安装结果选择、服务器启动失败后的子进程清理。全部改动已通过 Linux 本地构建、严格 Clippy 和相关回归测试；官方版本及安装器元数据也完成了摘要核验与解析。验证范围见文末；[机器可读验收记录](https://github.com/LIghtJUNction/zmcl/blob/main/docs/validation/correctness-2026-11-20.json)保存官方输入摘要、源码 blob、二进制摘要与检查结果。

## 1. 官方版本名称贯通创建、解析与启动计划

`1.14 Pre-Release 1` 等官方版本名称包含内部 ASCII 空格。此前不同入口使用不同校验规则，可能出现实例创建成功、后续解析或启动计划失败的情况。

现在以 `validate_version_id` 统一版本名称校验，并在版本元数据、继承关系、客户端 JAR 引用和启动计划中保持原始名称。它接受内部 ASCII 空格，继续拒绝首尾空格、结尾点、路径分隔符、控制字符、保留设备名称及超长输入。实例 ID 与分类标识继续使用原有严格规则。

证据：[校验及边界测试](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/crates/zmcl-core/src/path.rs)、[版本解析](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/crates/zmcl-core/src/model.rs)、[启动计划](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/crates/zmcl-core/src/plan.rs)、[实例入口](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/app.rs)。

## 2. 大型世界文件可以导出后重新导入

大型且重复内容较多的世界文件经过压缩后，可能超过导入器允许的压缩比，导致启动器拒绝自己刚导出的存档。导出器现对大于 16 MiB 的成员使用 ZIP `STORED`，直接保存成员内容；较小文件继续压缩。

这项调整让大文件的导出格式符合既有导入约束。压缩比、中央目录、总大小和成员数量限制继续生效，外部输入仍经过原有检查。真实文件往返测试验证了内容保持一致，相关世界管理测试共 22 项通过。高重复大文件的导出包会相应增大，这是这项格式选择的明确代价。

证据：[世界导入导出实现](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/worlds/manage.py)、[文件往返与限制回归](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/worlds/test_manage.py)。

## 3. Forge／NeoForge 安装结果按精确身份选择

安装器执行前，启动器从已完成摘要验证的 JAR 中读取元数据，锁定目标 profile 的 `id` 与 `inheritsFrom`。现代格式使用 `install_profile.json` 指定的 JSON 资源；旧格式读取内嵌 `versionInfo`。安装成功后只读取这一精确 ID 的文件，再核对其 ID 和 Minecraft 父版本。

这一流程消除了相似版本之间的误选，例如同时存在 `52.1.2` 与 `52.1.20` 时，后者不会被当作前者的安装结果。目标文件缺失、ID 不符或父版本不符都会明确失败。JSON 仍由现有 Rust 解析器验证，保留重复字段和解析预算检查。

JAR 读取辅助程序仅返回指定成员的原始字节，不进行解压落盘。它在分配 ZIP 成员表前检查目录预算，并拒绝 ZIP64 定位记录，防止 ZIP64 数值覆盖已经检查的普通 ZIP 记录。11 项 Python 与 4 项 Rust 回归覆盖了精确选择、现代及旧格式、路径限制、重复成员和 ZIP64 不一致元数据。

证据：[安装结果选择及 Rust 测试](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/runtime.rs)、[有界 JAR 读取](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/runtime/installer.py)、[真实 ZIP 回归](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/runtime/test_installer.py)。

## 4. 服务器启动失败时回收本次子进程

Java 子进程创建后，获取 `pidfd`、读取进程身份或保存运行记录仍可能失败。现在这些失败路径统一清理本次持有的子进程：先请求正常退出并等待，必要时强制结束并回收，避免启动失败后留下无人管理的进程。

清理失败会与原始启动错误一起报告。已有进程身份核对及 PID 复用保护保持有效，清理目标限定为本次创建并持有的子进程。12 项服务器辅助程序测试覆盖上述失败阶段、忽略正常退出信号、信号调用失败和清理超时。

证据：[进程管理实现](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/server/process.py)、[失败注入与回收测试](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/src/server/test_process.py)。

## 验证结果

下表分别记录测试数量与执行环境，Python 两个版本运行的是同一套 45 项测试。

| 检查 | 结果 | 环境与范围 |
|---|---|---|
| Rust 测试 | 105 项通过 | Linux；应用 83 项、核心库 22 项 |
| Python 辅助程序测试 | 45 项通过 | Python 3.14；世界 22、安装器 11、服务器 12 |
| Python 兼容运行 | 同样 45 项通过 | 独立 Python 3.12 环境 |
| Clippy | 通过，警告视为错误 | 本轮合并后的源码 |
| `cargo build` | 通过 | Linux 构建 |

官方 Mojang `1.14 Pre-Release 1` 元数据经 SHA-1 核验后完成 Linux、Windows、macOS 三组启动计划计算；摘要为 `76dd36e6b4fb2db5e474dd3b44cd96f3f498433d`。官方 Forge `1.21.1-52.1.20` 和 NeoForge `21.1.267` 安装器 JAR 也通过摘要核验及目标 profile 解析。

CLI 端到端 dry-run 已通过。相同的官方 JSON 输入与临时 fixture 下，已安装的 `zmcl 0.1.3` 创建实例成功、启动计划退出码为 1；候选二进制创建与启动计划均返回 0。候选输出保留完整 `--version` 值、单个 classpath 参数以及其中带空格的客户端 JAR 路径，共 34 个 classpath 成员。

验收使用真实 CLI 子进程与带空格的临时目录，只在临时元数据中设置合成的 `installed=true`。自有 Java fixture 只响应一次 `-version`；未下载游戏 JAR、未执行游戏、未创建启动记录。可复用脚本为 [test_version_names_cli.py](https://github.com/LIghtJUNction/zmcl/blob/a5693c0290ac9cc4311ed5242d48405b84294cf0/scripts/test_version_names_cli.py)。

### 复现命令

在包含上述四批修复、已初始化固定 submodule 的源码目录中运行：

```sh
cargo test --locked --workspace --all-targets
cargo clippy --locked --workspace --all-targets -- -D warnings
python3 -m unittest discover -s src/worlds -v
python3 -m unittest discover -s src/runtime -v
python3 -m unittest discover -s src/server -v
cargo build --locked
cargo run --locked -p zmcl-core --example manifest_inspect -- /path/to/official-profile.json
python3 scripts/test_version_names_cli.py target/debug/zmcl /path/to/official-profile.json
```

`official-profile.json` 使用上文指定 SHA-1 的 [Mojang 官方输入](https://piston-meta.mojang.com/v1/packages/76dd36e6b4fb2db5e474dd3b44cd96f3f498433d/1.14%20Pre-Release%201.json)。Python 版本与可执行程序按测试环境配置；这些命令不依赖 Microsoft 账号。

## 交付范围

本轮实际运行环境为 Linux。Windows、macOS 已完成版本解析与启动计划计算，其原生应用运行仍待实机验收。本轮未执行真实 Java 安装器、游戏登录或进入游戏，也未发布新的版本、安装包或 AUR 更新。本报告交付的是已验证并推送的源码修复；现有 0.1.3 安装包不包含它们。UI、zdistro 与后续发行由并行工作继续推进。
