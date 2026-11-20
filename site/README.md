# zmcl project site

A static Chinese documentation page for GitHub Pages. It uses system fonts, local assets and a small command-copy helper. Installation, current capabilities and measurement limits remain readable without JavaScript. There is no application framework, analytics or third-party script.

## Preview and validation

From the repository root:

~~~sh
python3 site/check.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
~~~

Check desktop and mobile widths, the three current asset links, installation disclosures, command-copy feedback and keyboard focus. The copy helper selects the command if clipboard access is unavailable.

## Current release: 0.1.4

The primary download section presents exactly three official assets for the development preview:

| Target | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Linux x86_64 user installation | `zmcl-0.1.4-linux-x86_64.tar.gz` | 700244 | `8265e672ef7914362c1038b71ed382d18efafe49668d7791005713faa5f580f1` |
| Debian x86_64 system package | `zmcl-0.1.4-linux-x86_64.deb` | 571108 | `c19afcdd4e567ffcb580f60fbe5812d6915c12b484e9751518c3f0b790c5b7b8` |
| Windows amd64 user installation | `zmcl-0.1.4-windows-amd64.zip` | 670411 | `48bd7dc7ce0eb799fed225678b6a6d9c4ed518f7ee96482b2c5c1f0f516271cf` |

The fixed release page is `/releases/tag/v0.1.4`; all asset paths use `/releases/download/v0.1.4/`. Source is `25c243a593b187b0f0835e740665e1fce96d3621`. Linux executable identity is 1504616 bytes / `586968d15c5187c0223d5223294ddcba585789d93934e6a37a3c7e1c516a3615`; Windows executable identity is 1382912 bytes / `33164565b43bf2f9db63da8e61db2a211318ca2fa682fd29ef988e910612910f`. These file sizes do not measure runtime memory.

Linux requirements remain glibc >=2.39, Fontconfig >=2.17.0, FreeType and X11/XWayland. Python >=3.8, curl and CA certificates are used by installation and requested helper operations. The native Linux CPU executable has no direct OpenGL dependency. The DEB explicitly requires CA certificates even with `--no-install-recommends`; this is not a compatibility claim for every stable Debian or Ubuntu release.

AUR zmcl-bin 0.1.4-1 is publicly available at source commit `a769068256b4aa0803ab08b7f0691a8a4024a464`. Public recipe verification is distinct from installation acceptance: the fresh public clone build, normal host upgrade and installed native update-window acceptance have passed. The real official check reports current 0.1.4. Previous AUR versions and their original identities stay in `historical_releases`; they are not the current primary download route.

## Acceptance and boundaries

| Claim | Evidence | Scope |
| --- | --- | --- |
| Three 0.1.4 asset identities and reproducible user commands | `docs/validation/zdistro-release-0.1.4.json`, `docs/INSTALL.md` | Exact files, hashes, source and named platforms |
| Linux DEB upgrade, reinstall and uninstall | `docs/reports/2026-11-20-zdistro.md` | Debian forky/sid container, real 0.1.3 to 0.1.4 upgrade and data preservation |
| Portable installation and interface | Same release report and structured receipt | Prefix with spaces, PATH shadowing, nine native GUI fixture groups; no real account or game claim |
| Minimal Debian can verify official HTTPS | Same release receipt | Initially missing CA certificates, normal apt installation of required CA, HTTP 200 and certificate verification result 0 |
| Manual update checking does not alter the executable | Same release receipt | One real query before publication reported local 0.1.4 ahead of online 0.1.3; binary unchanged |
| Windows native packaging and window | [workflow 44739704182](https://github.com/LIghtJUNction/zmcl/actions/runs/44739704182) | MSVC, ZIP, private prefix, version, Win32 window, five managed files, uninstall and data preservation on build 26100 |
| Full Windows version matrix, actual Windows games and accounts | Not established by this run | Keep separate from native runner packaging/window acceptance |
| macOS distribution, signed automatic updates and complete XMCL coverage | Not established | Remain planned |

The first Windows validator PATH lookup failure and Linux container documentation-exclusion failure retain their own original records. The successful corrected acceptance does not overwrite them. The user-facing page should remain about choosing, installing and updating the program; implementation-level failure analysis belongs in the report.

## Historical screenshots and measurements

The original v0.1.2 native captures are stored in the [verified historical archive](https://drive.google.com/file/d/1jMOYzLeo1prfhObfF_XA1OE852OJKUNt/view). Their source `e86b584`, zgpui 0.1.5 identity and original blob hashes remain recorded in the archive manifest. The site links to the archive without tracking screenshot binaries. They must not be relabelled as v0.1.4 captures.

The complete previous 0.1.3 release object is preserved in `historical_releases`, including source `9ef89bb`, 679517-byte tar, 1473752-byte binary and AUR recipe `60c9ad8`. The 0.1.3 management measurements retain their environments, binary identities, missing samples and peak lower-bound interpretation. No new whole-application memory comparison was made for 0.1.4.

The `iteration_measurements` array contains two separate 0.1.2 prerelease comparisons. Three interleaved empty-library redraw pairs observed median GUI CPU totals of 2.65 to 1.47 seconds, a 44.53% reduction. Two 2000-world pairs observed returned-Library GUI PSS+SwapPss medians of 7940 to 6110 KiB, a 23.05% reduction. Neither comparison used the final 0.1.2 binary; they cannot become 0.1.4, FPS or all-state claims.

The legacy `current_release_measurement` field is retained for schema compatibility with its explicit historical flag and label. Its artifact version remains 0.1.1 and all original values remain unchanged. The `measurement` and `measurements` records retain zmcl 0.1.0 source `033776287ab10aab90f46fd5b65309fecb9b8711` and the fixed official XMCL v0.73.1 comparison. The full GUI target below 10 MiB remains unmet.

## Publish

The official Pages workflow validates and uploads `site/`. Run the local checker after the release report and structured receipt are present. Relative asset paths support both `/zmcl/` on Pages and a local HTTP root. Publish the page only after the three release assets are publicly downloadable; AUR publication is a separate fact.

Writing follows the user-selected Research-Paper-Writing-Skills: one message per paragraph, evidence next to quantitative claims, and explicit validation boundaries. Keep the page arranged around download selection, first use, actual functions, the real window, resource costs and remaining work. Never publish private paths, raw session logs, account data or user saves.

## Published 0.1.4 evidence

The release tag is fixed at `25c243a593b187b0f0835e740665e1fce96d3621`. All eight public release assets were downloaded without credentials and matched their expected bytes and SHA-256. The public AUR recipe was rebuilt independently and the installed host was upgraded to 0.1.4-1.

- [Release and installation report](https://github.com/LIghtJUNction/zmcl/blob/76c38d5dab19d9ac39b6831bd509528bcd1799f5/docs/reports/2026-11-20-zdistro.md)
- [Linux and Windows release evidence](https://github.com/LIghtJUNction/zmcl/blob/76c38d5dab19d9ac39b6831bd509528bcd1799f5/docs/validation/zdistro-release-0.1.4.json)
- [Public AUR build and installed GUI evidence](https://github.com/LIghtJUNction/zmcl/blob/76c38d5dab19d9ac39b6831bd509528bcd1799f5/docs/validation/aur-install-0.1.4-2026-11-20.json)
