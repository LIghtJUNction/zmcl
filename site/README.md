# zmcl project site

A static Chinese documentation page for GitHub Pages. The page uses system fonts,
local assets and a small command-copy helper. The installation instructions,
feature status and measurement summary remain readable without JavaScript.
There is no application framework, analytics or third-party script.

## Preview and validation

From the repository root:

```sh
python3 site/check.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

Open the local HTTP URL in a browser. Check desktop and mobile widths, section
links, the source-build disclosure, command-copy feedback and keyboard focus.
The copy helper selects the command if clipboard access is unavailable.

## Content and evidence

The page is arranged as installation, first use, current features, the actual
window, resource measurements and remaining work. Keep text factual and specific
to the released version. Do not replace missing acceptance with a completion
claim, a performance ratio or a universal compatibility statement.

`data.json` remains the machine-readable historical record. It describes zmcl
source `033776287ab10aab90f46fd5b65309fecb9b8711` and official XMCL v0.73.1,
measured on 2026-11-20 with schema 4, three trials and eleven samples per trial.
The page shows a short static summary; update it alongside the evidence record
when a newly measured build is published. Earlier samples are not silently
relabelled as results for new code.

The current screenshot shows the v0.1.0 native window. The Linux x86_64 archive
is 648,849 bytes and contains the measured 1,412,176-byte executable. Its direct
ELF GLIBC requirement is 2.43. The fixed download link is
`/releases/tag/v0.1.0`, because this release is a prerelease.

The displayed idle GUI values are 89,946 KiB PSS and 108,728 KiB RSS. They cover
the observed launcher process tree in Xvfb with llvmpipe at 1024×720 after a
20-second warmup. Xvfb, Java and Minecraft are outside that scope. The 10 MiB GUI
target remains unmet by this released build. The existing UI still uses
zui-native; the planned unified zgpui backend must pass actual window and memory
acceptance before the page describes it as shipped.

Keep the AUR status in `index.html` consistent with a readback of the public
`zmcl-bin` package. A packaging file in GitHub does not establish that the AUR
entry is published or installable. Keep the GitHub download available while an
AUR publication is pending.

## Claim review

| Claim | Evidence | Status |
| --- | --- | --- |
| Linux v0.1.0 can be downloaded | Explicit GitHub release and `docs/validation/package-install-2026-11-20.json` | Accepted for that package |
| Selected game and loader menus were reached | `docs/VALIDATION.md` and its screenshots | Limited to recorded versions |
| Some management tools are CLI only | `docs/FEATURES.md` and current GUI source | Kept explicit |
| Idle memory remains above 10 MiB | `docs/PERFORMANCE.md` and raw samples | Target not achieved |
| Full XMCL feature coverage | `docs/FEATURES.md`, fixed v0.73.1 baseline | Incomplete |

Writing review follows the user-selected Research-Paper-Writing-Skills:
one message per paragraph, evidence next to quantitative claims, limited table
rules and explicit experimental boundaries. The page avoids unsupported
superiority claims. Remaining evaluation gaps include hardware GPU measurements,
installation peaks, large modpack workloads and independent Windows/macOS runs.

## Publish

The repository's official Pages workflow validates and uploads `site/`.
Do not run a separate frontend build or remote acceptance CI for this page.
Relative asset paths support both `/zmcl/` on Pages and a local HTTP root.
Never include account data, private paths, raw application logs or user saves.
