# Architecture and resource accounting

## Goal and boundary

zmcl is a native Minecraft launcher built from Project Z crates. It aims to cover XMCL v0.73.1 while keeping the launcher process and installation small. The feature matrix is the acceptance record; this architecture alone does not establish compatibility or a performance advantage.

The workspace separates a portable `no_std + alloc` core from operating-system work. `zmcl-core` validates metadata, applies inheritance and platform rules, builds download and launch plans, and computes streaming content digests. It performs no filesystem access, network requests or process launches. `zconv` supplies bounded borrowed JSON views. The application uses `zclap` for argument parsing and optional `zui-native` for its desktop interface.

## Why this layout reduces avoidable work

The GUI owns only interface state, bounded activity text and child-operation handles. It renders after input or a state change and waits for events when idle. Installing a game and signing in run in separate command invocations. Starting the game returns its process identifier; the GUI does not need to retain a Java process object.

Downloads use the operating system's curl with bounded concurrency and connection reuse. Every promoted game or Modrinth artifact has a trusted manifest digest and is checked before publication. A failed batch retains files that passed verification, so retrying does not repeat successful work. Cancellation does not make an instance installed. Partial byte-range resume is not currently implemented.

The shared store contains immutable content objects and conventional Minecraft paths backed by hard links when supported. Copying is the fallback across filesystems. Assets, libraries and managed mod files may be shared; configuration, logs and world saves belong to each instance. Changing a mod replaces its directory entry rather than writing through the shared link.

## Data layout

```text
data root/
  instances/<id>/instance.json
  instances/<id>/game/
  instances/<id>/loader.json
  instances/<id>/natives/
  instances/<id>/logs/
  shared/objects/
  shared/versions/
  shared/libraries/
  shared/assets/
  cache/
  accounts/
  locks/
```

The default data root follows the platform's application-data convention. `--root` and `ZMCL_HOME` select a separate root for portable use or testing. A test must use its own root; it must not import a user's existing launcher data merely to construct a benchmark.

## Trust boundaries

Version and resource paths are validated before filesystem access. Managed paths reject traversal, absolute paths, portable reserved names and symbolic-link destinations. ZIP import checks normalized duplicate names, entry types, individual and aggregate sizes, and collisions between the manifest and overrides before publication. Existing instances are never overwritten by an import.

Java receives an argv vector, not a shell command. Launch diagnostics replace access-token values. OAuth exchanges use curl configuration through stdin, keeping tokens out of helper command lines. Public account metadata contains only identity and expiration data. Secret material is delegated to the OS credential store: Secret Service on Linux, Keychain on macOS and Credential Manager on Windows. There is no plain-text token fallback.

A registered Microsoft application ID must be supplied by the application operator. The launcher does not use another launcher's OAuth identity. Device authorization, token refresh, Xbox user/device/XSTS exchange and Minecraft entitlement/profile requests are separate steps. Account-dependent end-to-end success must be recorded independently of unit tests.

## Dependencies that must be counted

Only Project Z crates appear in the Rust dependency graph. The application still uses the Rust standard library, OS libraries and system tools. A GUI build links the native X11/OpenGL/text libraries on Linux, and the corresponding zui platform backend elsewhere. curl, Python 3, OpenSSL, a credential service, Java and GPU-driver allocations are additional dependencies.

The Python standard library supplies bounded ZIP/TAR extraction; OpenSSL supplies P-256 signing. These processes are short-lived. Their memory and installed size must be included when measuring the relevant operation. Calling the application “zero dependency” would be inaccurate.

## Measurement protocol

Measure the stripped executable, the required launcher installation, the GUI's idle process tree, download peak, startup latency and physical game-data use separately. Report Java/game memory separately from launcher memory. On Linux, use PSS as well as RSS and count distinct inode/device pairs for physical storage. Measure the full process tree during installation, including curl and extraction helpers.

A fair XMCL comparison requires the same machine, game version, mod files, instance count, cache state and display backend. XMCL already shares files with hard links. No improvement ratio may be inferred solely from choosing Rust or implementing a shared store.

## Review questions

- Contribution: does the running program reduce overhead for a useful launcher operation? Requires measured results.
- Clarity: can a new reader build and reproduce the recorded test? Commands and inputs belong in the validation report.
- Evidence: is each supported feature tied to an observed result? Unverified integrations remain explicitly marked.
- Coverage: do error, cancellation and corrupted-content tests exercise the trust boundaries? Keep those regression tests with the implementation.
- Design: does a small launcher merely shift cost to helper processes? Include helpers and native drivers in measurements.

## References

- [XMCL v0.73.1](https://github.com/Voxelum/x-minecraft-launcher/releases/tag/v0.73.1)
- [XMCL storage architecture](https://xmcl.app/en/guide/manage)
- [Microsoft device authorization](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Modrinth pack format](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)
- [Project Z](https://github.com/LIghtJUNction/project-z)
