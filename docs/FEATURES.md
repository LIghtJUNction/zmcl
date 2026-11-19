# XMCL compatibility record

Baseline: **XMCL v0.73.1**, commit `aeb52403945f017a17f4014cb6542f73fa47dd15`. The official feature catalogue and the fixed runtime service interfaces define the comparison. The target remains full functional coverage; this initial implementation does not meet that target yet.

“Implemented” means a code path exists. “Verified” requires the evidence named in the validation report. An OS-specific source file does not prove that the corresponding operating system works.

| Area | Current implementation | Acceptance still required |
|---|---|---|
| Native UI and CLI | zui desktop library, instances, content, accounts and system pages; zclap CLI | Real interface screenshots, keyboard and narrow-window checks, idle measurements |
| Instances | Create, list, inspect, rename display name, Java and memory configuration; separate game directories | Clone/move/delete, groups, migration from all supported launchers |
| Official metadata | Official version list and verified per-version metadata | Offline and damaged-cache regression |
| Vanilla install | Client, libraries, assets, logging config and natives | Fresh-directory game main-menu test |
| Launch planning | Modern/legacy arguments, inheritance, OS/arch/features rules, native classifiers, Java requirements | Real old and current game versions |
| Fabric | Loader lookup, profile inheritance, checksum-backed Maven artifacts | Real modded game launch |
| Quilt | Loader lookup and profile inheritance | Native end-to-end launch |
| Forge | Official installer execution and profile discovery | Representative old/new installers and processor outputs |
| NeoForge | Official installer with explicitly selected loader version | Game/loader validation and end-to-end launch |
| OptiFine and LabyMod | Planned | Legal distribution source, install, launch and repair |
| Java discovery | System and configured runtime detection; major-version preflight | Managed runtime installation and cross-platform validation |
| Microsoft accounts | Device flow, refresh, Xbox device proof, XSTS, entitlement/profile, OS secrets | Registered application and real licensed-account verification |
| Offline profile | Explicit single-player name and compatible UUID | Actual game launch; this is not online ownership authentication |
| Yggdrasil, Ely.by, LittleSkin | Planned | Configurable endpoints, authlib integration and account verification |
| Shared content | Digest objects, hard links, cross-filesystem copy fallback | Physical-size and corruption/repair evidence |
| Downloads | HTTPS, bounded pooled connections, retry, digest/size validation | Resume by byte range, mirrors and user network settings |
| Modrinth search | Version/loader filters and project results | Real search, dependency install and game compatibility |
| Modrinth mods | Required dependencies, conflict checks, lockfile, enable/disable | Updates, optional dependencies, groups and resource-pack/shader management |
| CurseForge and FTB | Planned | Authorized API keys, downloads and unavailable-file handling |
| Modrinth packs | Safe fresh-instance import, staged publication, export and SHA-1 validation | Real public pack launch and all loader dependencies |
| Other pack formats | Planned | CurseForge, MCBBS, Packwiz, offline ZIP and server packs |
| Content creator workflows | Planned | Modrinth projects, image gallery, version publication and permissions |
| World management | Planned | Save import/export, preview, data packs, chunk tools and 3D blueprints |
| Backup and restore | Planned | Scheduled/manual backups, retention, conflicts and interruption recovery |
| Local servers | Planned | Server installation, explicit EULA acceptance, lifecycle and status |
| Remote servers | Planned | SSH/SFTP, deployment previews, service management and credentials |
| P2P multiplayer | Planned | Two real clients, LAN visibility, ICE/STUN, NAT and relay cases |
| Together services | External service integration not implemented | Service availability, account access and service-specific compatibility |
| AI diagnostics | Local basic system checks implemented; assistant planned | Redacted crash diagnosis, local/cloud model providers and tool authorization |
| Desktop integration | Native window implemented | OS shortcuts, file associations, accessible controls and notifications |
| Customization | Minimal dark native interface | Themes, backgrounds, per-instance appearance and documented CSS compatibility policy |
| Gamepad and Big Picture | Planned | Input mapping, controller-only navigation and virtual keyboard |
| Bedrock | Planned, Windows-specific upstream feature | Windows AppX/UWP management and actual gameplay |
| Updates | Source/build workflow | Verified update channel, signed packages, safe update/rollback and uninstall |
| Platforms | Portable core and zui platform backends | Separate Linux, Windows and macOS runtime evidence |

## Sources

- [Official feature catalogue](https://xmcl.app/en/features/)
- [Pinned service interface directory](https://github.com/Voxelum/x-minecraft-launcher/tree/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services)
- [ModpackService](https://github.com/Voxelum/x-minecraft-launcher/blob/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services/ModpackService.ts)
- [BackupService](https://github.com/Voxelum/x-minecraft-launcher/blob/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services/BackupService.ts)
- [RemoteServerService](https://github.com/Voxelum/x-minecraft-launcher/blob/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services/RemoteServerService.ts)
- [P2P](https://xmcl.app/en/guide/p2p), [Bedrock](https://xmcl.app/en/guide/bedrock), [Big Picture](https://xmcl.app/en/guide/big-picture)

## Claim–evidence checks

| Claim | Evidence | Status |
|---|---|---|
| Core does not require the Rust standard library | `#![no_std]`, Cargo graph and core verification | Code-backed; build evidence recorded separately |
| Only Project Z Rust crates are included | Fixed submodules and Cargo.lock | Inspectable |
| Resource promotion checks upstream digests | Store validation and corruption tests | Implementation present; integration evidence required |
| Lower overhead than XMCL | No matched XMCL benchmark yet | Not claimed |
| Full XMCL functionality | Many rows above remain planned | Not claimed |
