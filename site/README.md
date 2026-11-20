# zmcl project site

A static Chinese documentation page for GitHub Pages. It uses system fonts, local assets and a small command-copy helper. Installation, current capabilities and measurement limits remain readable without JavaScript. There is no application framework, analytics or third-party script.

## Preview and validation

From the repository root:

~~~sh
python3 site/check.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
~~~

Check desktop and mobile widths, section links, the source-build disclosure, command-copy feedback and keyboard focus. The copy helper selects the command if clipboard access is unavailable.

## Current release and installation status

The page describes the official Linux x86_64 v0.1.2 development preview and the published AUR zmcl-bin 0.1.2-1 recipe. The fixed prerelease link is `/releases/tag/v0.1.2`. Its 679,358-byte archive contains the 1,473,144-byte executable whose runtime source matches `e86b5841532ef04cc74cea5585ae396150452408`, with zgpui 0.1.5 at `4aba3ba83815253fb7e4013baa8bcf894439d32d`. The packager ran at `dc9e586ed541cc3e1d75661c54caacdd021834a9`; packaging identity and application source identity are recorded separately.

The prebuilt ABI requirements are glibc >=2.39 and Fontconfig >=2.17.0. Python >=3.8 and curl run only for requested operations. The Linux executable has no direct OpenGL dependency. These requirements are not a compatibility claim for every Linux distribution.

The AUR source commit is `16b688237f8c54b47085a16279535db8f6c5c553`. The first post-publication RPC, yay and paru queries returned cached 0.1.1-1 metadata; at 18:44 Asia/Taipei, current/legacy RPC, yay and paru all returned 0.1.2-1. A fresh public clone independently downloaded the source, built the package and passed payload validation. Its first extraction attempt failed because the shared host ran out of disk space; the original failure and successful retry remain separate. Normal pacman upgrade from 0.1.1-1 to 0.1.2-1 and four installed native-window fixture groups passed. The installed executable matches the official binary, and all 19 package entries passed integrity checks. See `docs/validation/aur-install-0.1.2-2026-11-20.json` and `docs/validation/installed-gui-0.1.2-2026-11-20.json`. Historical receipts retain their original versions.

The final Linux build passed four creation fixtures and six management workflows. Windows has a complete GUI cross-build and PE inspection, but no native application run in this acceptance record. The macOS application build and desktop flow remain unverified. Actual Windows/macOS framework probes do not expand the application acceptance scope. The page offers only the Linux x86_64 package.

## Screenshots and measurement identities

The displayed 1120×760 v0.1.2 creation window is the original native capture from the final Linux build and local creation fixture. `assets/create-0.1.2.png` reuses the exact Git blob of `docs/validation/images/zmcl-0.1.2-create.png` without image editing. Its caption records zgpui 0.1.5 and source `e86b584`; installed AUR upgrade and native fixture acceptance are separately recorded as passed. The old v0.1.1 backup image stays in the repository. Current creation and compact-layout evidence is linked through `docs/reports/2026-11-20-gui-iteration.md`.

The new `iteration_measurements` array in `data.json` contains two separate prerelease comparisons. The empty-library redraw workload used three interleaved pairs and observed median GUI CPU totals of 2.65 to 1.47 seconds, a 44.53% reduction. The 2,000-world workload used two pairs and observed returned-Library GUI PSS+SwapPss medians of 7,940 to 6,110 KiB, a 23.05% reduction. Each entry retains its own binary identities, scope, aggregation and public source record. These are different candidates, and neither measurement used the final v0.1.2 binary.

The legacy field `current_release_measurement` is retained for schema compatibility, with an explicit historical flag and label. Its artifact version remains 0.1.1 and all original values remain unchanged: empty-home idle RSS/PSS are 9,784/4,647 KiB; drawing RSS reaches 13,192 KiB; management startup reaches 16,972 KiB; and management actions reach 60,860/49,269 KiB RSS/PSS across the application and its CLI/Python children. These are historical measurements, not current v0.1.2 results.

The historical `measurement` and `measurements` records retain zmcl 0.1.0 source `033776287ab10aab90f46fd5b65309fecb9b8711` and the fixed official XMCL v0.73.1 comparison. The v0.1.1 release metadata is also retained in `historical_releases`. Display-server costs, swap, incomplete samples and observer cost remain explicit in the linked reports. The full GUI target below 10 MiB remains unmet, and whole-operation process-tree peaks did not consistently improve.

## Claim review

| Claim | Evidence | Scope |
| --- | --- | --- |
| Current Linux archive and executable identity | Fixed v0.1.2 release, archive checksum and embedded release.json | Exact file bytes and hashes; not runtime memory |
| AUR 0.1.2-1 is published and installed on the acceptance host | Public recipe commit 16b6882; independent build and installation receipts | Normal single-package upgrade and four installed native-window fixtures; no clean-chroot or all-distribution claim |
| Creation and management controls work in the final Linux build | docs/validation/gui-iteration-0.1.2-2026-11-20.json | Four creation fixtures and six management workflows; synthetic states where recorded |
| Redraw CPU and large-list steady memory improved in measured candidates | Separate renderer-iteration and large-library-iteration JSON records | Two distinct prerelease comparisons; no final-binary, FPS or all-states claim |
| Historical game menus and measurements remain valid evidence | Versioned records in docs/VALIDATION.md and docs/PERFORMANCE.md | Original builds, game/loader/account modes and test conditions |
| Cross-platform implementation is complete for the whole application | Platform matrix in the GUI iteration report | Not established; framework probes and application acceptance are separate |
| Full XMCL feature coverage | Fixed v0.73.1 matrix in docs/FEATURES.md | Incomplete |

Writing review follows the user-selected Research-Paper-Writing-Skills: one message per paragraph, evidence next to quantitative claims and explicit experimental boundaries. Keep the page arranged around installation, first use, actual functions, the real window, resource costs and remaining work.

## Publish

The repository's official Pages workflow validates and uploads `site/`. Do not use remote acceptance CI for routine checks. Relative asset paths support both `/zmcl/` on Pages and a local HTTP root. Keep the public build, actual upgrade and installed-window results tied to their specific artifact identities. Never publish account data, private paths, raw session logs or user saves.
