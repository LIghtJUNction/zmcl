//! Read-only launcher release checks and advisory distribution identity.
use crate::util::{self, Result};
#[cfg(feature = "gui")]
use std::path::Path;
use std::{cmp::Ordering, env, io::Read, process::Stdio};
#[cfg(feature = "gui")]
use zmcl_core::json::get_str;
use zmcl_core::json::{Kind, quote, req_str, with_json};

pub const RELEASES_PAGE: &str = "https://github.com/LIghtJUNction/zmcl/releases";
#[cfg(feature = "gui")]
pub const BROWSER_ERROR: &str = "Could not open a browser. Copy the official release link.";
const RELEASES_API: &str = "https://api.github.com/repos/LIghtJUNction/zmcl/releases?per_page=10";
const MAX_RESPONSE: usize = 1024 * 1024;
#[cfg(any(feature = "gui", test))]
const MARKER_LIMIT: usize = 16 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
struct Version {
    numbers: [u64; 3],
    pre: Vec<String>,
}

impl Version {
    fn parse(text: &str) -> Result<Self> {
        if text.is_empty() || text.len() > 80 || !text.is_ascii() {
            return Err("Invalid release version".into());
        }
        let (without_build, build) = text
            .split_once('+')
            .map_or((text, None), |(a, b)| (a, Some(b)));
        if let Some(build) = build {
            identifiers(build, false)?;
        }
        let (core, pre) = without_build
            .split_once('-')
            .map_or((without_build, ""), |(a, b)| (a, b));
        let mut numbers = [0; 3];
        let mut fields = core.split('.');
        for number in &mut numbers {
            let field = fields.next().ok_or("Invalid release version")?;
            if field.is_empty()
                || (field.len() > 1 && field.starts_with('0'))
                || !field.bytes().all(|b| b.is_ascii_digit())
            {
                return Err("Invalid release version".into());
            }
            *number = field.parse().map_err(|_| "Invalid release version")?;
        }
        if fields.next().is_some() || (without_build.contains('-') && pre.is_empty()) {
            return Err("Invalid release version".into());
        }
        Ok(Self {
            numbers,
            pre: if pre.is_empty() {
                Vec::new()
            } else {
                identifiers(pre, true)?
            },
        })
    }
}

fn identifiers(text: &str, prerelease: bool) -> Result<Vec<String>> {
    text.split('.')
        .map(|part| {
            if part.is_empty()
                || !part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
                || (prerelease
                    && part.len() > 1
                    && part.starts_with('0')
                    && part.bytes().all(|b| b.is_ascii_digit()))
            {
                return Err("Invalid release version".into());
            }
            Ok(part.to_owned())
        })
        .collect()
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        self.numbers.cmp(&other.numbers).then_with(|| {
            match (self.pre.is_empty(), other.pre.is_empty()) {
                (true, false) => return Ordering::Greater,
                (false, true) => return Ordering::Less,
                _ => {}
            }
            for (a, b) in self.pre.iter().zip(&other.pre) {
                let a_number = a.bytes().all(|c| c.is_ascii_digit());
                let b_number = b.bytes().all(|c| c.is_ascii_digit());
                let comparison = match (a_number, b_number) {
                    (true, true) => a.len().cmp(&b.len()).then_with(|| a.cmp(b)),
                    (true, false) => Ordering::Less,
                    (false, true) => Ordering::Greater,
                    (false, false) => a.cmp(b),
                };
                if comparison != Ordering::Equal {
                    return comparison;
                }
            }
            self.pre.len().cmp(&other.pre.len())
        })
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn release_url(version: &str) -> Result<String> {
    Version::parse(version)?;
    Ok(format!("{RELEASES_PAGE}/tag/v{version}"))
}

#[cfg(any(feature = "gui", test))]
pub fn official_page(url: &str) -> bool {
    url == RELEASES_PAGE
        || url
            .strip_prefix(&format!("{RELEASES_PAGE}/tag/v"))
            .is_some_and(|version| Version::parse(version).is_ok())
}

#[cfg(feature = "gui")]
pub fn platform() -> String {
    format!("{}-{}", env::consts::OS, env::consts::ARCH)
}

#[derive(Clone, Debug)]
pub struct Check {
    pub status: String,
    pub version: Option<String>,
    pub platform: String,
    pub release_url: String,
    pub message: String,
}

impl Check {
    fn json(&self) -> String {
        util::object(&[
            ("current_version", quote(env!("CARGO_PKG_VERSION"))),
            ("status", quote(&self.status)),
            (
                "available_version",
                self.version
                    .as_deref()
                    .map(quote)
                    .unwrap_or_else(|| "null".into()),
            ),
            ("platform", quote(&self.platform)),
            ("release_url", quote(&self.release_url)),
            ("message", quote(&self.message)),
        ])
    }

    #[cfg(feature = "gui")]
    pub fn from_json(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > 8192 {
            return Err("Invalid update result".into());
        }
        with_json(bytes, |root| {
            let status = req_str(root, "status")?;
            let url = req_str(root, "release_url")?;
            let current = req_str(root, "current_version")?;
            let found_platform = req_str(root, "platform")?;
            if !matches!(
                status.as_str(),
                "available" | "current" | "ahead" | "unavailable" | "empty"
            ) || !official_page(&url)
                || current != env!("CARGO_PKG_VERSION")
                || found_platform != platform()
            {
                return Err("Invalid update result".into());
            }
            let version = get_str(root, "available_version");
            if let Some(version) = &version {
                Version::parse(version)?;
            }
            let message = req_str(root, "message")?;
            if message.len() > 240 || message.chars().any(char::is_control) {
                return Err("Invalid update result".into());
            }
            Ok(Self {
                status,
                version,
                platform: found_platform,
                release_url: url,
                message,
            })
        })
    }
}

fn package_for(name: &str, version: &str, os: &str, arch: &str) -> bool {
    let systems: &[&str] = match os {
        "macos" => &["darwin", "macos"],
        "linux" => &["linux"],
        "windows" => &["windows"],
        _ => return false,
    };
    let architectures: &[&str] = match arch {
        "x86_64" => &["x86_64", "amd64"],
        "aarch64" => &["aarch64", "arm64"],
        _ => return false,
    };
    systems.iter().any(|system| {
        architectures.iter().any(|architecture| {
            let stem = format!("zmcl-{version}-{system}-{architecture}");
            name == format!("{stem}.tar.gz")
                || name == format!("{stem}.zip")
                || (os == "linux" && name == format!("{stem}.deb"))
        })
    })
}

fn parse_releases(bytes: &[u8], current: &str, os: &str, arch: &str) -> Result<Check> {
    if bytes.len() > MAX_RESPONSE {
        return Err("Update response exceeds 1 MiB. Try again later.".into());
    }
    let current = Version::parse(current)?;
    with_json(bytes, |root| {
        if root.kind() != Kind::Array {
            return Err("Invalid release response. Try again later.".into());
        }
        let mut latest: Option<(Version, String)> = None;
        let mut compatible: Option<(Version, String)> = None;
        for (index, entry) in root.array().enumerate() {
            if index >= 10 {
                return Err("Release response contains more than 10 entries.".into());
            }
            let draft = entry
                .get("draft")
                .and_then(|v| v.as_bool())
                .ok_or("Invalid release response: missing draft flag.")?;
            if draft {
                continue;
            }
            let tag = req_str(entry, "tag_name")?;
            let Some(version) = tag.strip_prefix('v') else {
                continue;
            };
            let Ok(parsed) = Version::parse(version) else {
                continue;
            };
            let url = req_str(entry, "html_url")?;
            if url != release_url(version)? {
                return Err("Release response contains an unexpected page URL.".into());
            }
            let assets = entry
                .get("assets")
                .ok_or("Invalid release response: missing assets.")?;
            if assets.kind() != Kind::Array {
                return Err("Invalid release response: assets are not a list.".into());
            }
            let mut available = false;
            for (index, asset) in assets.array().enumerate() {
                if index >= 64 {
                    return Err("Release response contains too many assets.".into());
                }
                let name = req_str(asset, "name")?;
                if name.len() > 160 {
                    return Err("Invalid release asset name.".into());
                }
                if !package_for(&name, version, os, arch) {
                    continue;
                }
                let download = req_str(asset, "browser_download_url")?;
                let expected = format!("{RELEASES_PAGE}/download/{tag}/{name}");
                if download != expected
                    || asset
                        .get("size")
                        .and_then(|v| v.as_u64())
                        .is_none_or(|n| n == 0)
                {
                    return Err("Release package metadata is invalid. Try again later.".into());
                }
                available = true;
            }
            if latest.as_ref().is_none_or(|(old, _)| parsed > *old) {
                latest = Some((parsed.clone(), version.to_owned()));
            }
            if available && compatible.as_ref().is_none_or(|(old, _)| parsed > *old) {
                compatible = Some((parsed, version.to_owned()));
            }
        }
        let (status, selected, message) = if let Some((version, label)) = compatible {
            match version.cmp(&current) {
                Ordering::Greater => (
                    "available",
                    Some(label),
                    "A newer package is available. Review the release before updating.",
                ),
                Ordering::Equal => (
                    "current",
                    Some(label),
                    "You have the latest published package for this platform.",
                ),
                Ordering::Less => (
                    "ahead",
                    Some(label),
                    "This build is newer than the published package. No downgrade is offered.",
                ),
            }
        } else if let Some((_, label)) = latest {
            (
                "unavailable",
                Some(label),
                "No package for this platform in the latest 10 releases.",
            )
        } else {
            (
                "empty",
                None,
                "No published version found in the latest 10 releases.",
            )
        };
        Ok(Check {
            status: status.into(),
            release_url: selected
                .as_deref()
                .map(release_url)
                .transpose()?
                .unwrap_or_else(|| RELEASES_PAGE.into()),
            version: selected,
            platform: format!("{os}-{arch}"),
            message: message.into(),
        })
    })
    .map_err(|error| format!("Could not read official releases: {error}"))
}

fn fetch_releases() -> Result<Vec<u8>> {
    let mut child = util::background_command("curl")
        .args([
            "--disable",
            "--silent",
            "--proto",
            "=https",
            "--connect-timeout",
            "5",
            "--max-time",
            "20",
            "--max-filesize",
            "1048576",
            "--header",
            "Accept: application/vnd.github+json",
            "--header",
            "X-GitHub-Api-Version: 2022-11-28",
            "--user-agent",
            "zmcl-update-check",
            "--write-out",
            "\n%{http_code}",
            RELEASES_API,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "Could not start curl. Install curl with your package manager, then retry.")?;
    let mut output = Vec::new();
    let read = child
        .stdout
        .take()
        .ok_or("Missing update response")?
        .take(MAX_RESPONSE as u64 + 5)
        .read_to_end(&mut output);
    if read.is_err() || output.len() > MAX_RESPONSE + 4 {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Update response exceeds 1 MiB or could not be read. Retry later.".into());
    }
    let status = child
        .wait()
        .map_err(|_| "Could not finish the update check. Retry.")?;
    if !status.success() {
        let message = match status.code() {
            Some(6 | 7) => {
                "Could not connect to GitHub. Check your connection or proxy, then retry."
            }
            Some(28) => "Update check timed out. Check your connection, then retry.",
            Some(35 | 60) => {
                "Could not verify the HTTPS connection. Check your clock and system certificates."
            }
            Some(63) => "Update response exceeds 1 MiB. Retry later.",
            _ => "Update check failed. Check your connection, then retry.",
        };
        return Err(message.into());
    }
    let split = output
        .iter()
        .rposition(|b| *b == b'\n')
        .ok_or("Invalid update HTTP response. Retry later.")?;
    match &output[split + 1..] {
        b"200" => {}
        b"403" | b"429" => {
            return Err("GitHub's request limit was reached. Wait a while, then retry.".into());
        }
        _ => return Err("GitHub could not serve the release list. Retry later.".into()),
    }
    output.truncate(split);
    Ok(output)
}

pub fn check_json() -> Result<String> {
    parse_releases(
        &fetch_releases()?,
        env!("CARGO_PKG_VERSION"),
        env::consts::OS,
        env::consts::ARCH,
    )
    .map(|check| check.json())
}

#[cfg(feature = "gui")]
pub struct Identity {
    pub location: String,
    pub platform: String,
    pub distribution: &'static str,
    pub guidance: &'static str,
}

#[cfg(feature = "gui")]
pub fn identity() -> Identity {
    let executable = env::current_exe().ok();
    let home = env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" });
    let location = executable
        .as_deref()
        .map(|path| display_location(path, home.as_deref().map(Path::new)))
        .unwrap_or_else(|| "Unavailable".into());
    let format = executable.as_deref().and_then(distribution);
    let (distribution, guidance) = match format
        .as_ref()
        .map(|(format, packaged)| (format.as_str(), *packaged))
    {
        Some(("portable", true)) => (
            "Portable · zdistro",
            "Use the portable installer to update this copy.",
        ),
        Some(("portable", false)) => (
            "Portable installation",
            "Use the portable installer to update this copy.",
        ),
        Some(("deb", _)) => (
            "Debian package · zdistro",
            "Update with your Debian package manager.",
        ),
        Some(("rpm", _)) => (
            "RPM package · zdistro",
            "Update with your RPM package manager.",
        ),
        Some(("arch", _)) => (
            "Arch package · zdistro",
            "Update with your Arch package manager.",
        ),
        _ => (
            "Unknown / source build",
            "Use the same installation method when updating.",
        ),
    };
    Identity {
        location,
        platform: platform(),
        distribution,
        guidance,
    }
}

#[cfg(feature = "gui")]
fn display_location(path: &Path, home: Option<&Path>) -> String {
    let value = home
        .filter(|p| !p.as_os_str().is_empty())
        .and_then(|home| path.strip_prefix(home).ok())
        .map(|tail| Path::new("~").join(tail))
        .unwrap_or_else(|| path.to_owned());
    value
        .to_string_lossy()
        .chars()
        .map(|c| if c.is_control() { '�' } else { c })
        .collect()
}

#[cfg(feature = "gui")]
fn distribution(executable: &Path) -> Option<(String, bool)> {
    let parent = executable.parent()?;
    let prefix = if parent.file_name().is_some_and(|name| name == "bin") {
        parent.parent()?
    } else {
        parent
    };
    let marker_path = prefix.join("share/zmcl/distribution.json");
    match std::fs::symlink_metadata(&marker_path) {
        Ok(metadata) => {
            if !metadata.file_type().is_file() {
                return None;
            }
            let marker = util::read_limited(&marker_path, MARKER_LIMIT).ok()?;
            return parse_distribution(&marker, env!("CARGO_PKG_VERSION"))
                .ok()
                .map(|format| (format, true));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => {
            return None;
        }
    }
    let receipt_path = prefix.join("share/zmcl/installation.json");
    if !receipt_path.is_file() {
        return None;
    }
    let receipt = util::read_limited(&receipt_path, MARKER_LIMIT).ok()?;
    parse_installation(&receipt, env!("CARGO_PKG_VERSION"), executable, prefix)
        .ok()
        .map(|format| (format, false))
}

#[cfg(feature = "gui")]
fn parse_installation(
    bytes: &[u8],
    current: &str,
    executable: &Path,
    prefix: &Path,
) -> Result<String> {
    if bytes.len() > MARKER_LIMIT {
        return Err("Invalid installation receipt".into());
    }
    with_json(bytes, |root| {
        if root.get("schema").and_then(|v| v.as_u64()) != Some(1)
            || req_str(root, "application")? != "zmcl"
            || req_str(root, "version")? != current
            || req_str(root, "channel")? != "portable"
            || Path::new(&req_str(root, "executable")?) != executable
            || Path::new(&req_str(root, "prefix")?) != prefix
            || req_str(root, "release_url")? != release_url(current)?
        {
            return Err("Invalid installation receipt".into());
        }
        Ok("portable".into())
    })
}

#[cfg(any(feature = "gui", test))]
fn parse_distribution(bytes: &[u8], current: &str) -> Result<String> {
    if bytes.len() > MARKER_LIMIT {
        return Err("Invalid distribution marker".into());
    }
    with_json(bytes, |root| {
        let version = req_str(root, "version")?;
        let format = req_str(root, "format")?;
        if root.get("schema").and_then(|v| v.as_u64()) != Some(1)
            || req_str(root, "application")? != "zmcl"
            || version != current
            || !matches!(format.as_str(), "portable" | "deb" | "rpm" | "arch")
            || req_str(root, "packager")? != "zdistro"
            || req_str(root, "packager_version")? != "0.1.0"
            || req_str(root, "packager_commit")? != "6a54a137fc697d372b54ef85105315f2ce522b3d"
            || req_str(root, "release_url")? != release_url(&version)?
        {
            return Err("Invalid distribution marker".into());
        }
        Ok(format)
    })
}

#[cfg(feature = "gui")]
pub fn open_page(url: &str) -> Result<std::process::Child> {
    if !official_page(url) {
        return Err("Only official zmcl release pages can be opened.".into());
    }
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut c = util::background_command("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        c
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut c = util::background_command("open");
        c.arg(url);
        c
    };
    #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
    let mut command = {
        let mut c = util::background_command("xdg-open");
        c.arg(url);
        c
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| BROWSER_ERROR.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn release(version: &str, draft: bool, package: bool) -> String {
        let name = format!("zmcl-{version}-linux-x86_64.tar.gz");
        let assets = if package {
            format!(
                r#"[{{"name":{name},"size":100,"browser_download_url":{url}}}]"#,
                name = quote(&name),
                url = quote(&format!("{RELEASES_PAGE}/download/v{version}/{name}"))
            )
        } else {
            "[]".into()
        };
        format!(
            r#"{{"tag_name":"v{version}","draft":{draft},"prerelease":true,"html_url":"{RELEASES_PAGE}/tag/v{version}","assets":{assets}}}"#
        )
    }

    fn check(body: &str) -> Check {
        parse_releases(body.as_bytes(), "0.1.3", "linux", "x86_64").unwrap()
    }

    #[test]
    fn versions_are_numeric_and_follow_semver_precedence() {
        let order = [
            "0.1.3-alpha",
            "0.1.3-alpha.2",
            "0.1.3-alpha.10",
            "0.1.3-beta",
            "0.1.3",
            "0.1.4",
            "0.1.10",
            "0.10.0",
            "1.0.0",
        ];
        for pair in order.windows(2) {
            assert!(Version::parse(pair[0]).unwrap() < Version::parse(pair[1]).unwrap());
        }
        assert_eq!(
            Version::parse("1.2.3+build.1").unwrap(),
            Version::parse("1.2.3+other").unwrap()
        );
        for bad in [
            "",
            "1.2",
            "01.2.3",
            "1.2.3.4",
            "1.2.3-",
            "1.2.3-01",
            "1.2.3+",
            "1.2.3+x+y",
            "1.2.3/other",
            "18446744073709551616.0.0",
        ] {
            assert!(Version::parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn release_selection_handles_preview_draft_unsorted_same_and_older() {
        let body = format!(
            "[{},{},{}]",
            release("0.1.4", false, true),
            release("9.0.0", true, true),
            release("0.1.10", false, true)
        );
        let found = check(&body);
        assert_eq!(found.status, "available");
        assert_eq!(found.version.as_deref(), Some("0.1.10"));
        assert_eq!(
            check(&format!("[{}]", release("0.1.3", false, true))).status,
            "current"
        );
        assert_eq!(
            check(&format!("[{}]", release("0.1.2", false, true))).status,
            "ahead"
        );
        assert_eq!(check("[]").status, "empty");
        assert_eq!(
            check(&format!("[{}]", release("0.1.4", false, false))).status,
            "unavailable"
        );
        let wrong_platform = parse_releases(
            format!("[{}]", release("0.1.4", false, true)).as_bytes(),
            "0.1.3",
            "windows",
            "x86_64",
        )
        .unwrap();
        assert_eq!(wrong_platform.status, "unavailable");
    }

    #[test]
    fn release_response_limits_and_urls_are_enforced() {
        for bad in ["{}", "not json", "[{}]", "[null]"] {
            assert!(parse_releases(bad.as_bytes(), "0.1.3", "linux", "x86_64").is_err());
        }
        let body = format!("[{}]", release("0.1.4", false, true));
        assert!(
            parse_releases(
                body.replace("https://github.com/", "https://evil.example/")
                    .as_bytes(),
                "0.1.3",
                "linux",
                "x86_64"
            )
            .is_err()
        );
        assert!(parse_releases(&vec![b' '; MAX_RESPONSE + 1], "0.1.3", "linux", "x86_64").is_err());
        let too_many = format!("[{}]", vec![release("0.1.4", false, true); 11].join(","));
        assert!(parse_releases(too_many.as_bytes(), "0.1.3", "linux", "x86_64").is_err());
        for bad in [
            "https://github.com/LIghtJUNction/zmcl/releases.evil",
            "https://github.com/LIghtJUNction/zmcl/releases/tag/v1.2.3?x=1",
            "https://github.com/LIghtJUNction/zmcl/releases/tag/v1.2.3/other",
            "file:///tmp/x",
        ] {
            assert!(!official_page(bad));
        }
        assert!(official_page(RELEASES_PAGE));
        assert!(official_page(&release_url("1.2.3").unwrap()));
    }

    #[test]
    fn markers_require_the_actual_version_and_official_identity() {
        let marker = r#"{"schema":1,"application":"zmcl","version":"0.1.3","format":"deb","packager":"zdistro","packager_version":"0.1.0","packager_commit":"6a54a137fc697d372b54ef85105315f2ce522b3d","release_url":"https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.3"}"#;
        assert_eq!(
            parse_distribution(marker.as_bytes(), "0.1.3").unwrap(),
            "deb"
        );
        assert!(parse_distribution(marker.as_bytes(), "0.1.4").is_err());
        for (old, new) in [
            ("\"schema\":1", "\"schema\":2"),
            ("\"application\":\"zmcl\"", "\"application\":\"other\""),
            ("\"format\":\"deb\"", "\"format\":\"unknown\""),
            ("https://github.com", "https://evil.example"),
        ] {
            assert!(parse_distribution(marker.replace(old, new).as_bytes(), "0.1.3").is_err());
        }
    }

    #[test]
    fn package_names_match_native_packager_targets_without_cross_platform_guessing() {
        assert!(package_for(
            "zmcl-0.1.4-windows-amd64.zip",
            "0.1.4",
            "windows",
            "x86_64"
        ));
        assert!(package_for(
            "zmcl-0.1.4-windows-arm64.zip",
            "0.1.4",
            "windows",
            "aarch64"
        ));
        assert!(package_for(
            "zmcl-0.1.4-linux-x86_64.deb",
            "0.1.4",
            "linux",
            "x86_64"
        ));
        assert!(package_for(
            "zmcl-0.1.4-darwin-arm64.tar.gz",
            "0.1.4",
            "macos",
            "aarch64"
        ));
        assert!(!package_for(
            "zmcl-0.1.4-windows-amd64.zip",
            "0.1.4",
            "linux",
            "x86_64"
        ));
        assert!(!package_for(
            "zmcl-0.1.4-linux-x86_64.tar.gz.sha256",
            "0.1.4",
            "linux",
            "x86_64"
        ));
        assert!(!package_for(
            "zmcl-0.1.40-linux-x86_64.tar.gz",
            "0.1.4",
            "linux",
            "x86_64"
        ));
    }

    #[cfg(feature = "gui")]
    #[test]
    fn installation_receipt_is_only_an_exact_portable_identity_hint() {
        let receipt = br#"{"schema":1,"application":"zmcl","version":"0.1.3","channel":"portable","executable":"/opt/fixture/bin/zmcl","prefix":"/opt/fixture","release_url":"https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.3"}"#;
        let exe = Path::new("/opt/fixture/bin/zmcl");
        let prefix = Path::new("/opt/fixture");
        assert_eq!(
            parse_installation(receipt, "0.1.3", exe, prefix).unwrap(),
            "portable"
        );
        assert!(parse_installation(receipt, "0.1.4", exe, prefix).is_err());
        assert!(parse_installation(receipt, "0.1.3", Path::new("/usr/bin/zmcl"), prefix).is_err());
        assert!(parse_installation(receipt, "0.1.3", exe, Path::new("/other")).is_err());
    }

    #[cfg(all(feature = "gui", unix))]
    #[test]
    fn invalid_marker_cannot_fall_back_to_a_valid_receipt() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let prefix = env::temp_dir().join(format!("zmcl-marker-{}-{unique}", std::process::id()));
        let directory = prefix.join("share/zmcl");
        std::fs::create_dir_all(&directory).unwrap();
        let executable = prefix.join("bin/zmcl");
        let version = env!("CARGO_PKG_VERSION");
        let receipt = util::object(&[
            ("schema", "1".into()),
            ("application", quote("zmcl")),
            ("version", quote(version)),
            ("channel", quote("portable")),
            ("executable", quote(&executable.to_string_lossy())),
            ("prefix", quote(&prefix.to_string_lossy())),
            ("release_url", quote(&release_url(version).unwrap())),
        ]);
        std::fs::write(directory.join("installation.json"), receipt).unwrap();
        assert_eq!(distribution(&executable), Some(("portable".into(), false)));
        let marker = directory.join("distribution.json");
        std::os::unix::fs::symlink("missing-marker.json", &marker).unwrap();
        assert_eq!(distribution(&executable), None);
        std::fs::remove_file(&marker).unwrap();
        std::fs::write(&marker, b"not JSON").unwrap();
        assert_eq!(distribution(&executable), None);
        std::fs::remove_dir_all(prefix).unwrap();
    }

    #[cfg(feature = "gui")]
    #[test]
    fn home_alias_uses_path_components() {
        assert_eq!(
            display_location(
                Path::new("/home/person/.local/bin/zmcl"),
                Some(Path::new("/home/person"))
            ),
            Path::new("~").join(".local/bin/zmcl").to_string_lossy()
        );
        assert_eq!(
            display_location(
                Path::new("/home/person2/bin/zmcl"),
                Some(Path::new("/home/person"))
            ),
            "/home/person2/bin/zmcl"
        );
        assert_eq!(
            display_location(Path::new("/usr/bin/zmcl"), None),
            "/usr/bin/zmcl"
        );
    }
}
