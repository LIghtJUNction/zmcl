# zmcl project site

This is a static project site for GitHub Pages. It contains no build framework, remote font, analytics script or third-party JavaScript dependency.

## Preview

From the repository root:

```sh
python3 site/check.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

Open http://127.0.0.1:8080/ in a browser. HTTP is required for the JSON data request; opening index.html directly as a file is not the preview path.

## Update evidence

Edit data.json without rebuilding the site. schema_version is currently 1.

- project.updated is the user-facing record date; project.baseline is the pinned XMCL release.
- features[].state is verified, implemented or planned. The label and summary must describe the actual verification scope. A small configuration round trip does not prove that every public modpack works.
- measurement.phase identifies initial or final measurements. Keep measurement.label, environment, scope, sample_count and comparison consistent with the recorded procedure.
- measurement.commit can contain the immutable commit once a measured build has been associated with it.
- measurements[].zmcl.value and measurements[].xmcl.value are a nonnegative number or null. null means unmeasured, never zero.
- Each measured cell can include note for sample count, range, median definition or format. Both applications use the same visual weight.
- Units are bytes, kib, ms, ticks, seconds or percent. Preserve raw values; app.js converts their presentation.
- Do not calculate or publish speed or memory ratios until both applications have paired observations on the same machine and equivalent functionality.
- Never put account tokens, personal paths, private launch logs or user worlds in this directory.

The current data describes source 033776287ab10aab90f46fd5b65309fecb9b8711 (ELF SHA-256 8139bab8416ec9720bc4c9e11765944755f85bbe778b5cf404863bd6fb3ba30c) and official XMCL 0.73.1, measured on 2026-11-20 with the same schema 4 script, three trials and eleven samples per trial. Exact raw records and derived summaries are under docs/measurements/. The screenshot is from the final zmcl trial. Shared-host memory pressure differed between application batches; the page discloses available memory, swapped pages and different default home content. Hardware GPU, installation peak and physical game storage remain unmeasured. Earlier records are historical and do not supply the displayed final values.

The Linux x86_64 prerelease package is 648,849 bytes (tar.gz); its seven regular files total 1,453,865 logical bytes. It was packaged from clean commit a8f72474c4f5289ea881226299ae7706b8024b59 and preserves the measured application binary. The prebuilt ELF directly requires glibc 2.43; this is not a blanket Linux compatibility claim. The download URL is the explicit /releases/tag/v0.1.0 prerelease page, not /releases/latest. Exact package hashes and file manifests are linked from docs/validation/package-install-2026-11-20.json.

## Publish

Use GitHub Pages with GitHub Actions and upload site/ as the Pages artifact. The publishing workflow belongs to the repository maintainer, outside this directory. The standard sequence is checkout → check → upload-pages-artifact (path: site) → deploy-pages. No Jekyll or asset build step is required.

All site links and assets are relative where appropriate, so the same directory works under the repository prefix /zmcl/ and under a local HTTP root.

## Source and claim review

The overview states the goal, describes the mechanism and links to code. The feature table distinguishes implementation from verification. The performance table carries units, environment and exclusions alongside the values. The comparison method includes child processes, physical file use and the existing shared storage in XMCL.

Before publishing: check the feature matrix, replace provisional measurements if newer evidence exists, confirm the screenshot is from the stated build, and run check.py. Browser review should include the feature filters, command-copy feedback, wide and narrow viewports, and the no-data failure message.
