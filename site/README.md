# zmcl project site

A static Chinese documentation page for GitHub Pages. It uses system fonts, local assets and a small command-copy helper. Installation, current capabilities and measurement limits remain readable without JavaScript. There is no application framework, analytics or third-party script.

## Preview and validation

From the repository root:

```sh
python3 site/check.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

Check desktop and mobile widths, section links, the source-build disclosure, command-copy feedback and keyboard focus. The copy helper selects the command if clipboard access is unavailable.

## Current release and historical evidence

The page describes official Linux x86_64 v0.1.1 and AUR zmcl-bin 0.1.1-1. The fixed prerelease link is `/releases/tag/v0.1.1`. Its 664,850-byte archive contains the 1,439,712-byte executable built from `35eb8a3242a6ba637266ba1480354138389c9cb0`, with native zgpui 0.1.2 at `5b268da910639abd2fb8e581acb4f6eed50b7516`. The measured prebuilt ABI requirements are glibc >=2.39 and Fontconfig >=2.17.0. These are not universal Linux compatibility claims.

The current screenshot is an original 1120×760 native-window capture of the backup page after restoration. The success notice explicitly says a pre-restore snapshot was saved. It is not a mockup or an edited reconstruction.

The current measurement object in `data.json` links the exact source and binary to three idle/drawing trials and six management workflows. Empty-home idle RSS/PSS are 9,784/4,647 KiB; drawing RSS reaches 13,192 KiB, management startup reaches 16,972 KiB, and management actions reach 60,860/49,269 KiB RSS/PSS across the application and its CLI/Python children. The full GUI target remains unmet. Display-server costs, swap, incomplete samples and observer cost stay explicit in `docs/PERFORMANCE.md`.

The historical `measurement` and `measurements` records retain zmcl 0.1.0 source `033776287ab10aab90f46fd5b65309fecb9b8711` and the fixed official XMCL v0.73.1 comparison. Earlier results are never silently relabelled as measurements of new code. A source change or a passing window test alone does not establish a new memory result.

## Claim review

| Claim | Evidence | Scope |
| --- | --- | --- |
| Linux v0.1.1 is downloadable | Explicit GitHub release and release-0.1.1 validation JSON | Exact package identity |
| AUR 0.1.1-1 is installable | Public Git/RPC/helper readbacks, fresh build, normal host upgrade and installed window | Tested Arch host; no clean-chroot claim |
| World, backup, resource and server controls are connected | GUI source, six local fixture workflows, installed management page | Remaining features and online paths stay explicit |
| Empty-home idle RSS is below 10 MiB | Three complete idle trials of the published binary | Startup, drawing and management exceed the target |
| Selected game menus were reached previously | Versioned records in `docs/VALIDATION.md` | Those older builds and game/loader/account modes |
| Full XMCL feature coverage | Fixed v0.73.1 matrix in `docs/FEATURES.md` | Incomplete |

Writing review follows the user-selected Research-Paper-Writing-Skills: one message per paragraph, evidence next to quantitative claims and explicit experimental boundaries. Keep the page arranged around installation, first use, actual functions, the real window, resource costs and remaining work.

## Publish

The repository's official Pages workflow validates and uploads `site/`. Do not use remote acceptance CI for routine checks. Relative asset paths support both `/zmcl/` on Pages and a local HTTP root. Never publish account data, private paths, raw session logs or user saves.
