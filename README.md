# zmcl

A small native Minecraft launcher built on Project Z.

The goal is a low-memory, small-install launcher with the capabilities of [XMCL](https://github.com/Voxelum/x-minecraft-launcher). Compatibility is evaluated against **XMCL v0.73.1**, not inferred from the presence of a launch button.

## Design

- Rust `no_std + alloc` launch core; bounded JSON views from `zconv`.
- Native optional `zui` interface, with a command-line interface for scripts.
- Shared content storage for immutable downloads; isolated instance settings and saves.
- Download verification, explicit Java selection, structured launch arguments and redacted diagnostics.
- Feature and platform claims accompanied by reproducible checks.

## Status

Implementation has started. Full XMCL parity is the target, not a claim about this initial repository. The implementation, feature matrix, measurements and build instructions are being committed in independently verified steps.

Minecraft and XMCL are separate projects. This repository is not an official Minecraft product or an XMCL release.
