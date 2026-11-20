# XMCL compatibility record

zmcl targets the functional scope of **XMCL v0.73.1**, commit **aeb52403945f017a17f4014cb6542f73fa47dd15**. The [fixed service interfaces](https://github.com/Voxelum/x-minecraft-launcher/tree/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services) define the comparison baseline. The [official feature catalogue](https://xmcl.app/en/features/) provides product context and may describe newer upstream work. Full coverage remains a development target; the gaps below remain part of the acceptance work.

This record separates implementation, local fixtures and live results. **Implemented** identifies a reachable code path. **Fixture accepted** identifies tests with controlled files, synthetic NBT, fake network peers or process fixtures. **Live accepted** identifies a named provider download, native interface interaction or real game outcome. Each result applies to its stated environment. Build identities, current test totals and sanitized outcomes belong in [VALIDATION.md](VALIDATION.md); process-tree and storage measurements belong in [PERFORMANCE.md](PERFORMANCE.md).

## Runtime, interface and accounts

| Area | Implemented behavior and source | Recorded acceptance | Remaining scope |
|---|---|---|---|
| Native GUI | [zui desktop interface](../src/gui.rs): Library, Discover, Modpacks, Accounts, Settings, activity and cancellation. Instance creation exposes vanilla, Fabric, Quilt, Forge and NeoForge; the last two require a loader version. | Linux private-display interaction: create an instance, operate a 640×520 window, export/import a small vanilla mrpack. | New world, backup, server, resource and Horizon workflows are primarily CLI paths. Complete keyboard/accessibility, gamepad and Windows/macOS interface acceptance remain open. |
| CLI | [Command entry points](../src/main.rs) cover instances, installs, launches, accounts, mods, packs, resources, worlds, backups, Java, local servers, Horizon and diagnostics. Operations use explicit arguments and JSON results. | Named command workflows are recorded below and in the validation report. | CLI availability and GUI availability are tracked separately. |
| Launcher exit after launch | Optional GUI launch flag `gui --exit-after-launch`. The GUI exits after an uncancelled launch worker succeeds and returns valid JSON with a positive PID. The default keeps the GUI open. | [PID/result gate tests](../src/gui.rs) and [real Linux lifecycle](measurements/2026-11-20-gui-exit-and-window.json) accepted: the GUI exited successfully while its separately grouped Java game process continued running. This check did not capture a game menu. | Report launcher-session residency separately from the continuing Java/game process. `launch --wait` intentionally keeps the calling CLI alive. |
| Official metadata and vanilla | [Runtime](../src/runtime.rs) fetches version manifests, verifies version JSON and installs client, libraries, assets, logging configuration and natives. | [Game-menu acceptance](validation/game-menus-2026-11-20.json): real Linux Minecraft 26.3 main menu with Java 25; the earlier fresh Minecraft 1.21.1 run reached the welcome screen. | Actual world play/save, Realms and multiplayer were not exercised. |
| Portable launch planning | [Core](../crates/zmcl-core/src/) handles inheritance, legacy/modern arguments, OS/architecture/features rules, Maven paths, native classifiers and Java requirements. It creates argv vectors. | Unit tests, a `no_std` target check and official old/current manifest plans for multiple OS configurations. | Real execution across the historical version range and each OS remains open. |
| Fabric | Official loader profile and dependency installation, merged with the Minecraft version. | Real Linux Minecraft 1.21.1 main menu, Fabric API and Mod Menu; a Mod Menu screen was captured. | Broader loader/game/mod combinations remain to be accepted. |
| Quilt | Official loader metadata, semantic loader-version ordering and profile inheritance. | Real Linux Minecraft 1.21.1 main menu with Quilt 0.30.2 after the automatic-selection fix. | Broader Quilt loader/game/mod combinations remain to be accepted. |
| Forge | Official installer execution, generated profile discovery and locally produced artifact handling. | Linux Forge 52.1.20 installation, launch plan and real Minecraft 1.21.1 main menu accepted after the local-artifact fix. | Representative old/new Forge versions and mod combinations remain open. |
| NeoForge | Official installer with an explicitly selected compatible loader version. | Implementation and shared installation checks. | Fresh installation and real game launch across supported versions. |
| Managed Java | [Java manager](../src/java.rs) lists/discovers runtimes, downloads an official Adoptium JRE using SHA-256 and size, extracts it in staging, checks `java -version`, and retains immutable version directories. | Real Linux installation of Temurin `jdk-21.0.13.1+2`; archive size 52,190,567 bytes. Unsafe archive and link fixtures are tested. | Windows/macOS runtime execution and the supported major-version range. |
| Automatic Java during install | The install path checks the version's required Java major and can provision a missing managed runtime when no explicit Java path is configured. Java selection itself remains a selector; direct launch does not download a runtime. | Real Minecraft 26.3 installation automatically added Temurin `jdk-25.0.5.1+2` to a home that previously contained only Java 21. The resulting launch dry run selected Java 25, and a real Minecraft 26.3 launch reached the main menu. | Keep explicit Java configuration, installation-time provisioning and launch-time selection distinguishable in reports. |
| Microsoft accounts | [Device authorization, refresh, Xbox/XSTS and Minecraft profile/entitlement flow](../src/auth.rs), account selection and OS secret storage adapters. A registered application ID is required. | Code and local validation paths. | A live licensed-account sign-in/refresh/launch result; adapters on each OS. |
| Offline single-player profile | Explicit user name and compatible offline UUID in the launch plan. | The recorded Linux vanilla/Fabric launches used explicit offline names. | Online ownership authentication is handled through the account flow. |
| Diagnostics | Runtime/tool discovery, Java reporting and shared-storage statistics. | CLI diagnostics and GUI Settings paths. | Redacted crash analysis, AI providers and authorized automated repair. |

The Rust dependency named `zui` in this repository is the **`zui-native`** package. It supplies the current native window/OpenGL implementation. **`zgpui` is a separate Project Z library** for `no_std` scene/layout and renderer contracts; it is not the library currently wired into zmcl. See the [manifest](../Cargo.toml), [pinned submodules](../.gitmodules) and [architecture](ARCHITECTURE.md).

## Content and distribution

| Area | Implemented behavior and source | Recorded acceptance | Remaining scope |
|---|---|---|---|
| Verified downloads and shared storage | [HTTPS transport](../src/net.rs), bounded concurrency/retry and [digest-addressed storage](../src/store.rs). Downloads require expected digests and limits. Conventional Minecraft paths reuse cached objects. | Real Minecraft, mod, resource, Java and Horizon downloads; [repair after both a library path and its cache object were corrupted](validation/cache-repair-2026-11-20.json); an edited active resource ZIP leaves its cache object unchanged. | Byte-range resume, mirror selection, broader network settings and additional damaged-cache cases. |
| Modrinth mods | [Content management](../src/content.rs) searches with game/loader filters; installs required dependencies; lists, toggles, updates and removes tracked mods; exposes transaction recovery. Required-version conflicts and incompatible projects are rejected. | [CLI acceptance](validation/content-cli-2026-11-20.json): real Mod Menu 11.0.4→11.0.5 update with Fabric API and Placeholder API; required-dependency remove/disable refusal; toggle, update, remove and v2 recovery checks. Existing Fabric/Mod Menu game-menu evidence is recorded separately. | Optional dependency selection, groups and exhaustive version-combination solving. The resolver has explicit graph/iteration bounds. |
| Modrinth pack catalogue | Search official modpack projects and install a selected project release through the mrpack importer. | [Pack acceptance](validation/modrinth-pack-2026-11-20.json): real `sop-continued` project `IpYIvkPB`, release `TAU7qd8y`: search, install, instance inspection and launch dry run. The pack installed 19 files totalling 15,962,909 bytes with Fabric 0.19.3. | The named pack's game launch remains unverified. Existing game-cache reuse is part of this result, not a fresh-install timing benchmark. |
| Mrpack import/export | Safe fresh-instance import, staged overrides and export. Dependency identifiers include `minecraft`, `fabric-loader`, `quilt-loader`, `forge` and `neoforge`. | ZIP/path fixtures, GUI export/import of a small vanilla instance and the named public-pack install. | Representative Forge/NeoForge/Quilt pack launches and unavailable-file cases remain open. |
| Mrpack hashes | Download/import verifies SHA-1 plus size; received SHA-512 is retained as metadata. Export streams the files, checks SHA-1/size and computes SHA-512 for the output index. | [Archive helper](../src/content/archive.py) and format tests. | The reader accepts legacy inputs without SHA-512; strict conformance to every current format rule is not claimed. |
| Resource packs | [Resource manager](../src/resources.rs): exact project/game filtering, add/list/update/remove, availability toggle, revision history and restore. Only tracked, unchanged ZIP files are modified. | [20-command real CLI acceptance](../src/resources/test_cli.py), including No Pumpkin Blur download, a real older-release upgrade, retained old bytes, manual-file conflict protection and restore. | In-game selection/rendering is a separate acceptance step. The command leaves the selected-pack list in `options.txt` unchanged. |
| Shader packs | Same tracked-file lifecycle in `shaderpacks`, with upstream loader requirements returned to the caller. | Actual Terder's Potato Shader download, verified size/hash/catalogue, list output and unchanged game configuration. | Shader-engine installation and in-game shader compilation/rendering. Downloading a shader ZIP does not install Iris or OptiFine. |
| Resource transaction recovery | Per-file journal, retained originals and before/after lock snapshots; ambiguous or manually changed files are preserved. | Interrupted and repeated recovery fixtures; live remove/history/restore workflow; refusal with a recorded live-PID fixture. | Windows/macOS failure-recovery acceptance. Non-Unix ambiguous identity cases are left for explicit recovery. Historical archives currently remain on disk. |

The [current Modrinth format specification](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack) requires both SHA-1 and SHA-512 fields. Field preservation, download verification and export computation are separate behaviors. Resource-pack/shader dependencies are reported; these commands do not silently install additional projects or shader engines.

## Instance data, local services and Horizon

| Area | Implemented behavior and source | Recorded acceptance | Remaining scope |
|---|---|---|---|
| Instance lifecycle | [Create/list/show/configure](../src/app.rs), [independent game-data clone, trash/list/restore](../src/instance_manage.rs). A clone is marked for installation so shared binaries/profiles/natives can be recreated. Trash retains shared objects. | Local tests cover independent writable copies, trash/restore, collisions and symlink refusal. The [synthetic-data CLI lifecycle](../scripts/test_lifecycle_cli.py) also verifies separate inodes, occupied-target refusal and retained clone edits. | Large-instance acceptance, groups, directory relocation and migrations from other launchers. |
| World management | [List/info/path/import/export/clone](../src/worlds.rs). Reads bounded gzip Java NBT metadata; copies world bytes without editing chunks; accepts a world directory or one clearly rooted ZIP. | [Synthetic Java-save and NBT fixtures](../src/worlds/test_manage.py), complete file roundtrips, malicious archives, changed-source rejection and actual OS file-lock contention. The real CLI also completed directory/ZIP import, list/info/path, export and clone using the explicitly synthetic fixture. | Playing an imported real world, data-pack management, world previews, NBT editing, chunk tools and 3D blueprints. The path command returns a verified path/metadata target. |
| Backup/restore | [Manual snapshots](../src/backup.rs) of selected mutable game data; SHA-256 file manifest; safe staged restore; pre-restore snapshot and interruption journal. Managed roots, caches/logs and credential-like paths follow explicit exclusion policy. | Fixture roundtrips and recovery checks; actual CLI snapshot/restore with every manifest SHA-256 checked, original bytes restored, excluded mods retained and pre-restore bytes recovered from the retained snapshot. | Scheduled backups, retention controls, remote/cloud backup and broad real-world recovery acceptance. |
| Local Java server management | [Create/install/start/status/stop/list](../src/server.rs), official server JAR verification, Java selection, logs and process identity records. Start requires an already explicit `eula=true`; creation keeps EULA acceptance false. | [Six-command CLI acceptance](validation/server-cli-2026-11-20.json): official Minecraft 1.21.1 server JAR, 51,627,615 bytes, with matching SHA-1/size; status/list accepted and start explicitly refused with unchanged `eula=false` and no server process. Owned-process stop protection has fixtures. | A user-authorized live Minecraft server/world lifecycle. Managed start/stop is Linux-only and requires procfs/pidfd support. |
| Server status ping | Bounded Java server-list-ping handshake, status JSON and ping/pong over TCP, with DNS/connect/read limits. | A real local TCP fake server exercises the wire protocol and malformed packets. | External servers, older legacy ping protocols and broader timeout/network cases. |
| Horizon launcher provider | [Separate provider](../src/horizon.rs) manages the official Horizon **launcher component**. It verifies GitHub asset digest, signed `SHA256SUMS` and extracted-file hashes; launch rechecks managed files. | Actual Linux x86_64 `launcher@0.2.84` download (35,123,939 bytes), signature/manifest verification, extraction and verified `--version` execution. | Horizon sign-in, legitimate game preparation and entering a game. Java mod-loader support is not transferred to Horizon. Other OS archives have fixtures, not native execution acceptance. |

### Recorded resource acceptance

The reproducible resource script uses a new test home and two small official releases. Its [sanitized acceptance record](validation/resources-cli-2026-11-20.json) retains the command outcomes and explicit limits:

| Project | Kind | Accepted release | Archive bytes |
|---|---|---|---:|
| No Pumpkin Blur (`iQRsGaVM`) | Resource pack | `kcDL19Mv` | 768 |
| Terder's Potato Shader (`j8vzmojd`) | Shader | `IEpWGvCX` | 24,189 |

The upgrade test seeds a previous managed state from the official compatible release `1BoGsbLr`, updates through the real CLI to `kcDL19Mv`, and verifies that the previous archive remains recoverable. Expected failures for manual collisions, edited managed content and a sleeping-process fixture with a live PID are part of the accepted command sequence. No Minecraft world or shader renderer is started by this script.

### Recorded lifecycle acceptance

[`scripts/test_lifecycle_cli.py`](../scripts/test_lifecycle_cli.py) ran [21 recorded real CLI commands](validation/lifecycle-cli-2026-11-20.json) in a new isolated data root. Three commands deliberately tested and received protective failures: importing over an existing world, exporting over an existing ZIP, and restoring an instance into an occupied target. The successful commands checked world directory/ZIP/clone roundtrips, backup manifests and pre-restore snapshots, independent writable instance copies, and trash/list/restore.

The source world contains three files totalling 2,111 bytes: generated gzip NBT, an explicitly opaque region fixture and a fixture marker. It is not a game-loadable world. No Java/game/server process or network download is requested. The acceptance establishes command routing, response fields and file preservation; playing and saving a real imported world remains a separate target.

## Platform acceptance

| Surface | Linux | Windows | macOS |
|---|---|---|---|
| Java client and native GUI | X11/XWayland path; private Xvfb/software-driver and real vanilla/Fabric/Forge/Quilt process outcomes recorded. | Source paths and generated launch plans; native runtime acceptance pending. | Source paths and generated launch plans; native runtime acceptance pending. |
| Managed Java | Official x86_64 JRE installation accepted. | Platform selection/extraction paths; native execution pending. | Platform selection/extraction paths; native execution pending. |
| World, backup and resource mutation | Process-record checks plus session-lock safeguards; named fixture/CLI scenarios accepted. | Conservative refusal when an existing launch record cannot establish exit; transaction and filesystem behavior need native acceptance. | Conservative launch-record handling and native filesystem acceptance pending. |
| Local managed server | Start/stop implementation uses procfs and pidfd identity checks. | Managed start/stop explicitly unavailable; installation/ping have portable code paths. | Managed start/stop explicitly unavailable; installation/ping have portable code paths. |
| Horizon provider | Linux x86_64 glibc launcher component accepted. | x64/arm64 archive mappings, pending native execution. | x64/arm64 archive mappings, pending native execution. |

No native Wayland backend, hardware-driver result, Windows result or macOS result is inferred from the Linux private-display run. Process, archive and account-provider prerequisites are described in [ARCHITECTURE.md](ARCHITECTURE.md).

## Capabilities still outside the implemented scope

These remain visible acceptance targets from the fixed XMCL baseline:

- OptiFine/LabyMod installation; alternate Yggdrasil, Ely.by and LittleSkin authentication.
- CurseForge/FTB distribution and unavailable-file handling; CurseForge, MCBBS, Packwiz, offline/server-pack workflows beyond the implemented mrpack path.
- Mod/content groups, optional-dependency UI and creator project/gallery/version publication.
- World/data-pack editing, chunk visualization and 3D blueprint workflows.
- Scheduled/remote backups and retention management; remote SSH/SFTP servers, deployment and service credentials.
- P2P LAN discovery, ICE/STUN/relay traversal and two-client acceptance; Together-specific service integrations.
- AI crash diagnosis and model-provider/tool permissions beyond basic local diagnostics.
- Full desktop file associations, shortcuts, notifications and accessibility; themes/backgrounds; controller-only Big Picture and virtual keyboard.
- Windows Bedrock management; a signed launcher self-update/rollback channel; complete OS/game/loader acceptance.

## Claim–evidence review

| Review dimension | Question and current answer |
|---|---|
| Contribution | Which useful paths exist? The tables identify concrete commands and native flows, with source links. Full XMCL coverage remains the goal. |
| Clarity | Can readers distinguish file operations, GUI exposure and game execution? Separate columns and named outcomes provide that distinction. |
| Experimental strength | What ran against real software? Named Minecraft, Modrinth, Adoptium and Horizon outcomes are recorded in [VALIDATION.md](VALIDATION.md). Performance claims use the matched protocol in [PERFORMANCE.md](PERFORMANCE.md). |
| Evaluation completeness | What remains? Synthetic-world tests, untested OS paths, account authorization, shader rendering and missing services remain explicitly scoped. |
| Design soundness | What is protected? Expected digests, staged publication, operation locks and retained revisions are backed by the cited code/tests. Recovery limits and history storage cost remain documented. |

## Upstream references

- [XMCL v0.73.1 release](https://github.com/Voxelum/x-minecraft-launcher/releases/tag/v0.73.1) and [fixed service interfaces](https://github.com/Voxelum/x-minecraft-launcher/tree/aeb52403945f017a17f4014cb6542f73fa47dd15/xmcl-runtime-api/src/services).
- [Modrinth project search](https://docs.modrinth.com/api/operations/searchprojects/), [version files](https://docs.modrinth.com/api/operations/getprojectversions/) and [mrpack specification](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack).
- [Adoptium API cookbook](https://github.com/adoptium/api.adoptium.net/blob/main/docs/cookbook.adoc).
- [Horizon installation and first launch](https://playhorizon.dev/docs/install), [official source](https://github.com/horizon-rust/horizon) and [pinned release-signature implementation](https://github.com/horizon-rust/horizon/blob/41e53103f721966f902f9c793ba89ac51904e5ad/crates/horizon-release-sign/src/lib.rs).
