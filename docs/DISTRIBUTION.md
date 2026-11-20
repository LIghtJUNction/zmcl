# 用 zdistro 分发 zmcl

zmcl 的发行端使用固定版本的 zdistro 创建归档和系统软件包。最终 tar.gz、ZIP 和 DEB 的文件由 zdistro 生成；zmcl 的包装脚本负责核对输入二进制、源码身份、运行库要求和输出文件。zdistro 不进入启动器的运行时，也不承担应用更新服务。

## 固定工具与环境

- zdistro：v0.1.0，源码提交 `6a54a137fc697d372b54ef85105315f2ce522b3d`。
- [官方 zipapp](https://github.com/LIghtJUNction/zdistro/releases/download/v0.1.0/zdistro-0.1.0.pyz) SHA-256：`a30a4580254a6510c2019e3e7a684c31d4e435b69f32f33d3813ef6264544908`。
- 发行端需要 Python 3.11 或更新、Git；Linux 需要 readelf，DEB 另需 dpkg-deb。
- 用户端的安装器与文件助手仍要求 Python 3.8 或更新。构建工具的版本要求不等于用户端要求。

包装脚本必须显式接收 `--zdistro`，执行前核对固定摘要，不会自动下载或执行其他版本。

## 生成发行包

先在最终源码提交上完成本地 Cargo 测试、严格 Clippy、修改过的 Python 测试及实际使用流程，再构建原生程序：

~~~sh
cargo build --release --locked
./target/release/zmcl --version
git rev-parse HEAD
sha256sum target/release/zmcl
~~~

记录完整提交与二进制摘要。Linux x86_64 示例：

~~~sh
python3 scripts/zdistro_package.py \
  --binary target/release/zmcl \
  --source-root . \
  --source-commit FULL_SOURCE_COMMIT \
  --expected-sha256 FROZEN_BINARY_SHA256 \
  --zdistro /path/to/zdistro-0.1.0.pyz \
  --target tar-gz \
  --target deb \
  --output /path/to/new-release-directory
~~~

输出目录必须尚不存在。重复验证应使用新的目录，保留第一次执行的证据。

包装脚本检查源码 checkout 的 HEAD、运行时代码、Cargo 与工具链配置、依赖 gitlink，以及实际入包的安装器、文档、图标和桌面文件。检查范围内的未跟踪或被 Git 忽略文件也会拒绝；应在干净的打包 checkout 中执行，构建输出放在 `target/`。它还检查二进制的原生 ELF/PE 架构、摘要及实际 `--version` 输出。这些检查用于防止混装。二进制的编译来源仍须由构建记录提供，不能只凭版本字符串证明。

每次执行依次调用 zdistro 的 `doctor`、`plan`、`build`、`verify` 和 `matrix`。请求的目标必须全部产生且完成校验；缺失、重复、跳过目标都会失败。不会用 `--available` 将不支持的目标静默省略。

## 产物与校验记录

| 路径 | 内容 |
| --- | --- |
| `public/` | 带版本、操作系统和架构的最终软件包、各包的 SHA-256 文件及 SHA256SUMS |
| `release-result.json` | 源码、二进制、工具、目标和发布副本的摘要映射 |
| `evidence/zdistro.toml` | 此次实际执行的 manifest |
| `evidence/artifacts/` | zdistro 的原始软件包及原始 receipt |
| `evidence/legacy/` | 原有 payload 生成器的归档与文件、依赖证据，供追溯使用 |

`evidence/legacy/` 是输入和校验材料；用户下载的软件包来自 `public/` 中对应的 zdistro 输出副本。原始软件包、manifest 与 receipt 保留原始字节，不改写校验状态。发布副本改用唯一文件名，摘要必须与原始产物完全相同。

zdistro 的 receipt 会包含打包机器的绝对路径。可公开的原始证据应在固定的通用容器路径中生成，例如 `/workspace/zmcl` 与 `/workspace/releases`。先检查记录，不公开私人路径或原始会话日志。

receipt 中的 `installed: false`、`published: false` 和签名状态表示该工具自身的工作范围。实际安装、发布和界面验收另立记录，不能事后把原 receipt 改成已安装或已签名。SHA-256 可核对文件是否一致，本身不证明发布者身份。

## 软件包布局和运行条件

便携包保留原有根目录和 `scripts/install.py` 入口。DEB 将程序放在 `/usr/bin/zmcl`，同时提供桌面入口、图标、许可证和说明文件。游戏实例、存档、账号资料及 Java 不打进启动器软件包。

DEB 的 glibc 与 Fontconfig 下限来自实际二进制引用的符号；动态库名映射到 Debian 包名，遇到未映射的依赖会明确失败。还显式声明 curl、CA 证书（`ca-certificates`）、Python 与图标主题依赖，安装时禁用推荐包也会安装必需的 CA 证书。当前未从 GCC/GLIBCXX 符号推导 Debian 包版本下限，不能据此宣称任意旧系统兼容；真实安装和动态链接检查另行验收。Microsoft 凭据操作需要额外的 `libsecret-tools` 与桌面 Secret Service，Horizon 验签需要支持 Ed25519 的 OpenSSL。不能为了使安装通过而降低实际 ABI 下限。

DEB 目前只接受数字形式的 `X.Y.Z` 版本，拒绝尚未转换的 SemVer 预发布或构建后缀，防止 Debian 与 SemVer 不同的排序规则改变升级顺序。GitHub 上将数字版本标为开发预览不会改变软件包版本本身。

每个软件包在 `share/zmcl/distribution.json`（DEB 为 `/usr/share/zmcl/distribution.json`）提供应用、版本、格式、打包工具及固定官方发行页等说明。该文件用于展示安装来源，不授权自动改写程序，也不代替包管理器的所有权记录。

Arch 继续使用仓库中维护的 AUR 配方，并消费 zdistro 生成的官方 tar.gz，以保留 `provides`、`conflicts` 与可选依赖声明。DEB 的成功打包不表示所有 Debian 或 Ubuntu 版本都能运行，实际系统版本与安装结果应单独记录。

## 用户安装信息与更新入口

用户安装器仍使用 Python 3.8 或更新。在解包目录执行 `python3 scripts/install.py install --prefix "目标目录"`；Windows 可使用 `py -3`。Linux 的程序目标为 `bin/zmcl`，Windows 为 `bin/zmcl.exe`。完成提示给出可验证的版本、位置、直接启动命令和更新方式。若 PATH 优先选择另一份程序，同时展示两个路径，不修改 PATH 或用户的 shell 配置，也不删除另一份安装。

用户安装生成 `share/zmcl/installation.json`，字段为 `schema: 1`、`application: "zmcl"`、`version`、`channel: "portable"`、`executable`、`prefix` 和 `release_url`。版本优先使用经过 payload 摘要校验的发行元数据；合法旧包缺少完整信息时可以安装，但不能从目录名称推测版本。`install.json` 保留原有的严格列表格式，每项只含 `path` 和 `sha256`。带 distribution marker 的新便携安装明确管理程序、图标、桌面文件、distribution marker 与 installation receipt 五个文件；marker 和 receipt 的修改也会触发保护。

GUI 的 Settings 展示当前程序版本、路径与安装来源，并提供手动 **Check for updates**。只有通过校验的 distribution marker 才用于识别对应的打包来源；缺失 marker 时，合法 portable receipt 可以说明用户安装，但不自动标记为 zdistro。损坏、悬挂链接或与当前程序不匹配的 marker 不会被另一份 receipt 覆盖。更新结果提供官方发行页与适合当前平台的资产信息，不自动替换文件；AUR 和 DEB 仍由各自包管理器更新。

用户级升级检查旧清单与实际摘要，只覆盖确认受管且未修改的文件。卸载同样只移除匹配的受管文件；修改过的文件、非受管文件和已有游戏数据保留。详情与可复制命令见[安装说明](INSTALL.md)。

## 0.1.4 实际发行验收

0.1.4 从源码 [25c243a](https://github.com/LIghtJUNction/zmcl/commit/25c243a593b187b0f0835e740665e1fce96d3621) 生成 Linux x86_64 tar.gz、DEB 和 Windows amd64 ZIP。文件大小、摘要、二进制身份与完整流程记录在[发行报告](reports/2026-11-20-zdistro.md)和[结构化回执](validation/zdistro-release-0.1.4.json)；面向用户的入口为 [v0.1.4 发行页](https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.4)。

Linux 在 Debian forky/sid 容器完成旧 0.1.3 升级、重装、卸载，以及带空格前缀、PATH 遮蔽、九组原生 GUI 夹具和数据保留检查。最小原始镜像没有 CA 证书；正常 apt 安装 DEB，即使使用 `--no-install-recommends`，也会安装所需证书。随后 GitHub HTTPS 请求返回 200、证书验证结果为 0；一次真实手动更新查询识别本机版本高于当时线上 0.1.3，查询没有修改二进制。

[正式 Windows workflow 44739704182](https://github.com/LIghtJUNction/zmcl/actions/runs/44739704182)在原生 build 26100 runner 上完成 MSVC 构建、ZIP 打包、私有前缀安装、版本输出、Win32 窗口、五项受管文件与卸载数据保留检查。此结果不扩大为所有 Windows 版本、真实账号或实际游戏流程验收。首次 Windows 验证器 PATH 解析失败，以及 Linux 容器默认排除文档导致的首次完整性检查失败，均保留在原始记录；修正验证环境后的成功另行记录。

## 平台边界

Linux 原生包装支持 tar.gz、ZIP 和 DEB；Linux ZIP 的名称必须明确为 Linux。Windows 原生包装入口仅接受 ZIP，并要求在 Windows 执行对应二进制的版本检查。不能把 Linux 上生成 ZIP 的成功当作 Windows 应用运行验收。

本 wrapper 尚未实现 macOS 应用分发、MSI/MSIX 安装体验、RPM、AppImage、Flatpak、签名或自动更新。框架的平台支持、软件包结构验证与完整启动器的实机流程应分别报告。

## 回归与已知工具行为

~~~sh
ZMCL_TEST_ZDISTRO=/path/to/zdistro-0.1.0.pyz \
  python3 -m unittest discover -s scripts -p 'test_zdistro*.py'
~~~

测试使用小型原生 ELF、真实固定 zipapp 和真实软件包格式，检查文件、权限、来源身份、依赖下限、重复目标、篡改以及输出冲突。它不替代真实 zmcl 的安装、原生窗口和游戏流程。

固定 v0.1.0 zipapp 的入口未传播 CLI 返回码，失败时可能返回进程状态 0，同时向 stderr 写入错误 JSON。包装脚本因此同时检查错误记录、结构化结果、目标完整性和文件摘要，拒绝这种假成功。没有修改固定工具来掩盖问题；最小复现与测试应随验收记录保存。
