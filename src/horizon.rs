//! Official Horizon launcher provider. The game is a separate Rust rewrite of
//! Minecraft Java 26.3; its official launcher owns login and resource preparation.
use crate::{App, net, store, util};
use std::collections::BTreeSet;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use zmcl_core::json::{Kind, Value, get_str, quote, raw, req_str, with_json};

type Result<T> = std::result::Result<T, String>;
const PROVIDER: &str = "horizon-rust/horizon";
const API: &str = "https://api.github.com/repos/horizon-rust/horizon";
const RELEASES: &str = "https://github.com/horizon-rust/horizon/releases";
const MAX_ARCHIVE: u64 = 512 * 1024 * 1024;
const MAX_MANIFEST: usize = 8 * 1024 * 1024;
const PLATFORMS: &[(&str, &str)] = &[
    ("linux-x86_64", "horizon-launcher-linux-x86_64.tar.gz"),
    ("windows-x64", "horizon-launcher-windows-x64.zip"),
    ("windows-arm64", "horizon-launcher-windows-arm64.zip"),
    ("macos-x64", "horizon-launcher-macos-x64.zip"),
    ("macos-arm64", "horizon-launcher-macos-arm64.zip"),
];

#[derive(Clone, Debug)]
struct Asset {
    id: u64,
    name: String,
    size: u64,
    sha256: String,
    url: String,
}
#[derive(Clone, Debug)]
struct Release {
    id: u64,
    tag: String,
    version: (u64, u64, u64),
    published: String,
    commit: String,
    assets: Vec<Asset>,
}

fn number(value: Value<'_, '_>, key: &str) -> Result<u64> {
    value
        .get(key)
        .and_then(|v| v.as_u64())
        .ok_or_else(|| format!("Missing numeric Horizon {key}"))
}
fn hash(value: &str) -> Result<String> {
    if value.len() != 64 || !value.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Horizon requires a 64-digit SHA-256".into());
    }
    Ok(value.to_ascii_lowercase())
}
fn tag_version(tag: &str) -> Result<(u64, u64, u64)> {
    let suffix = tag
        .strip_prefix("launcher@")
        .ok_or("Expected an official launcher@X.Y.Z tag")?;
    if suffix.len() > 48 {
        return Err("Horizon release tag is too long".into());
    }
    let fields = suffix.split('.').collect::<Vec<_>>();
    if fields.len() != 3
        || fields
            .iter()
            .any(|v| v.is_empty() || !v.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err("Only stable official Horizon launcher tags are supported".into());
    }
    let parse = |value: &str| {
        value
            .parse::<u64>()
            .map_err(|_| "Horizon version component overflow".to_owned())
    };
    Ok((parse(fields[0])?, parse(fields[1])?, parse(fields[2])?))
}
fn normalize_tag(value: &str) -> Result<String> {
    let tag = if value.starts_with("launcher@") {
        value.to_owned()
    } else {
        format!("launcher@{value}")
    };
    tag_version(&tag)?;
    Ok(tag)
}
fn asset_url(tag: &str, name: &str) -> Result<String> {
    tag_version(tag)?;
    if name.is_empty()
        || name.len() > 160
        || !name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-._".contains(&b))
    {
        return Err("Invalid Horizon asset filename".into());
    }
    Ok(format!(
        "{RELEASES}/download/{}/{}",
        net::encode(tag),
        net::encode(name)
    ))
}
fn parse_release(value: Value<'_, '_>) -> Result<Release> {
    if value.get("draft").and_then(|v| v.as_bool()) != Some(false)
        || value.get("prerelease").and_then(|v| v.as_bool()) != Some(false)
    {
        return Err("Draft and prerelease Horizon launchers are not installable".into());
    }
    let tag = req_str(value, "tag_name")?;
    let version = tag_version(&tag)?;
    let list = value
        .get("assets")
        .filter(|v| v.kind() == Kind::Array)
        .ok_or("Missing Horizon release assets")?;
    let mut assets = Vec::new();
    let mut names = BTreeSet::new();
    for item in list.array() {
        if assets.len() >= 64 {
            return Err("Horizon release contains too many assets".into());
        }
        let name = req_str(item, "name")?;
        if !names.insert(name.to_ascii_lowercase()) {
            return Err("Duplicate Horizon release asset".into());
        }
        let url = req_str(item, "browser_download_url")?;
        if url != asset_url(&tag, &name)? {
            return Err("Horizon asset is outside the official release source".into());
        }
        let digest = req_str(item, "digest")?;
        let sha256 = hash(
            digest
                .strip_prefix("sha256:")
                .ok_or("GitHub did not provide a Horizon SHA-256")?,
        )?;
        let size = number(item, "size")?;
        if size == 0 || size > MAX_ARCHIVE {
            return Err("Horizon asset exceeds the download budget".into());
        }
        assets.push(Asset {
            id: number(item, "id")?,
            name,
            size,
            sha256,
            url,
        });
    }
    Ok(Release {
        id: number(value, "id")?,
        tag,
        version,
        published: req_str(value, "published_at")?,
        commit: req_str(value, "target_commitish")?,
        assets,
    })
}
fn github(path: &str) -> Result<Vec<u8>> {
    let result = net::request(
        &format!("{API}{path}"),
        "GET",
        &[
            ("Accept", "application/vnd.github+json"),
            ("X-GitHub-Api-Version", "2022-11-28"),
        ],
        None,
    )?;
    if result.status != 200 {
        return Err(format!(
            "Official Horizon release API returned HTTP {}. Check connectivity or the GitHub rate limit.",
            result.status
        ));
    }
    Ok(result.body)
}
fn catalog() -> Result<Vec<Release>> {
    let bytes = github("/releases?per_page=30")?;
    with_json(&bytes, |value| {
        if value.kind() != Kind::Array {
            return Err("GitHub release response is not an array".into());
        }
        let mut releases = Vec::new();
        for item in value.array() {
            let tag = get_str(item, "tag_name").unwrap_or_default();
            if !tag.starts_with("launcher@")
                || item.get("draft").and_then(|v| v.as_bool()) != Some(false)
                || item.get("prerelease").and_then(|v| v.as_bool()) != Some(false)
            {
                continue;
            }
            releases.push(parse_release(item)?);
        }
        releases.sort_by_key(|a| std::cmp::Reverse(a.version));
        Ok(releases)
    })
}
fn current_platform() -> Result<&'static str> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("linux", "x86_64") => Ok("linux-x86_64"),
        ("windows", "x86_64") => Ok("windows-x64"),
        ("windows", "aarch64") => Ok("windows-arm64"),
        ("macos", "x86_64") => Ok("macos-x64"),
        ("macos", "aarch64") => Ok("macos-arm64"),
        _ => Err("No official portable Horizon launcher is mapped for this OS/architecture".into()),
    }
}
fn platform_asset(platform: &str) -> Result<&'static str> {
    PLATFORMS
        .iter()
        .find(|(key, _)| *key == platform)
        .map(|(_, name)| *name)
        .ok_or_else(|| "Unsupported Horizon platform".into())
}
fn select_asset<'a>(release: &'a Release, name: &str) -> Result<&'a Asset> {
    release
        .assets
        .iter()
        .find(|a| a.name == name)
        .ok_or_else(|| format!("Official release {} has no {name}", release.tag))
}
fn valid_installation_id(value: &str) -> Result<()> {
    if value.len() > 120 {
        return Err("Horizon installation ID is too long".into());
    }
    let rest = value
        .strip_prefix("launcher-")
        .ok_or("Horizon installation ID must start with launcher-")?;
    let version = PLATFORMS
        .iter()
        .find_map(|(platform, _)| rest.strip_suffix(&format!("-{platform}")))
        .ok_or("Horizon installation ID has an unknown platform")?;
    tag_version(&format!("launcher@{version}"))?;
    Ok(())
}
fn installation_id(tag: &str, platform: &str) -> Result<String> {
    tag_version(tag)?;
    platform_asset(platform)?;
    let id = format!("launcher-{}-{platform}", &tag["launcher@".len()..]);
    valid_installation_id(&id)?;
    Ok(id)
}
fn release_json(release: &Release) -> String {
    let assets = PLATFORMS
        .iter()
        .filter_map(|(platform, name)| {
            release
                .assets
                .iter()
                .find(|asset| asset.name == *name)
                .map(|asset| {
                    util::object(&[
                        ("platform", quote(platform)),
                        ("asset", quote(&asset.name)),
                        ("bytes", asset.size.to_string()),
                        ("sha256", quote(&asset.sha256)),
                        ("source", quote(&asset.url)),
                    ])
                })
        })
        .collect::<Vec<_>>()
        .join(",");
    util::object(&[
        ("tag", quote(&release.tag)),
        ("published", quote(&release.published)),
        ("release_id", release.id.to_string()),
        ("commit", quote(&release.commit)),
        (
            "url",
            quote(&format!("{RELEASES}/tag/{}", net::encode(&release.tag))),
        ),
        (
            "signed_manifest_available",
            (release.assets.iter().any(|a| a.name == "SHA256SUMS")
                && release.assets.iter().any(|a| a.name == "SHA256SUMS.sig"))
            .to_string(),
        ),
        ("assets", format!("[{assets}]")),
    ])
}

/// Lists recent official launcher releases, independently from game/nightly SDK
/// releases. Catalog metadata is not a claim that assets have been downloaded.
pub fn versions() -> Result<String> {
    let releases = catalog()?;
    let entries = releases
        .iter()
        .map(release_json)
        .collect::<Vec<_>>()
        .join(",");
    Ok(util::object(&[
        ("provider", quote(PROVIDER)),
        ("component", quote("launcher")),
        ("website", quote("https://playhorizon.dev")),
        ("catalog_page_size", "30".into()),
        (
            "platform",
            current_platform()
                .map(quote)
                .unwrap_or_else(|_| "null".into()),
        ),
        ("releases", format!("[{entries}]")),
    ]))
}
fn provider_root(app: &App) -> Result<PathBuf> {
    util::safe_join(&app.root, "providers/horizon")
}
fn installed_root(app: &App) -> Result<PathBuf> {
    util::safe_join(&provider_root(app)?, "installed")
}
fn nonce() -> String {
    format!(
        "{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    )
}
struct Temporary(PathBuf);
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn private_directory(path: &Path) -> Result<()> {
    fs::create_dir_all(path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn read_pipe<R: Read>(mut stream: R, maximum: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    let mut block = [0u8; 8192];
    let mut exceeded = false;
    loop {
        let count = stream.read(&mut block).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if bytes.len().saturating_add(count) <= maximum {
            bytes.extend_from_slice(&block[..count]);
        } else {
            exceeded = true;
        }
    }
    if exceeded {
        return Err("Horizon helper output exceeded its memory budget".into());
    }
    Ok(bytes)
}
fn helper(app: &App, arguments: &[OsString]) -> Result<String> {
    let cache = util::safe_join(&app.root, "cache")?;
    private_directory(&cache)?;
    let temporary = Temporary(util::safe_join(
        &cache,
        &format!("horizon-helper-{}", nonce()),
    )?);
    fs::create_dir(&temporary.0).map_err(|e| e.to_string())?;
    private_directory(&temporary.0)?;
    let script = temporary.0.join("archive.py");
    util::atomic_write(&script, include_bytes!("horizon/archive.py"))?;
    let mut child = util::python_command()?
        .arg(&script)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Horizon portable installation requires system Python 3: {e}"))?;
    let stdout = child.stdout.take().ok_or("Missing Horizon helper stdout")?;
    let stderr = child.stderr.take().ok_or("Missing Horizon helper stderr")?;
    let output = std::thread::spawn(move || read_pipe(stdout, MAX_MANIFEST));
    let errors = std::thread::spawn(move || read_pipe(stderr, 8192));
    let start = Instant::now();
    let mut timeout = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if start.elapsed() > Duration::from_secs(330) => {
                timeout = true;
                let _ = child.kill();
                break child.wait().map_err(|e| e.to_string());
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(error.to_string());
            }
        }
    };
    let output = output.join().map_err(|_| "Horizon output reader failed")?;
    let errors = errors.join().map_err(|_| "Horizon error reader failed")?;
    if timeout {
        return Err(
            "Horizon verification/extraction exceeded 330 seconds; installation was not published"
                .into(),
        );
    }
    let status = status?;
    if !status.success() {
        let errors = errors?;
        let message = String::from_utf8_lossy(&errors);
        return Err(if message.trim().is_empty() {
            format!("Horizon helper exited with {status}")
        } else {
            message.trim().to_owned()
        });
    }
    String::from_utf8(output?)
        .map(|v| v.trim().to_owned())
        .map_err(|_| "Invalid Horizon helper output encoding".into())
}
fn cached(app: &App, asset: &Asset) -> Result<PathBuf> {
    store::ensure(
        app,
        &format!(
            "providers/horizon/artifacts/{}/{}",
            asset.sha256, asset.name
        ),
        &asset.url,
        None,
        Some(&asset.sha256),
        Some(asset.size),
    )
}

#[derive(Debug)]
struct Installed {
    id: String,
    tag: String,
    platform: String,
    asset: String,
    archive_hash: String,
    archive_bytes: u64,
    installed: u64,
    entrypoint: String,
    source: String,
}
impl Installed {
    fn parse(bytes: &[u8]) -> Result<Self> {
        with_json(bytes, |value| {
            if number(value, "format_version")? != 1
                || req_str(value, "provider")? != PROVIDER
                || req_str(value, "component")? != "launcher"
                || value.get("signature_verified").and_then(|v| v.as_bool()) != Some(true)
            {
                return Err("Invalid official Horizon installation manifest".into());
            }
            let id = req_str(value, "id")?;
            let tag = req_str(value, "tag")?;
            let platform = req_str(value, "platform")?;
            let asset = req_str(value, "asset")?;
            if installation_id(&tag, &platform)? != id || asset != platform_asset(&platform)? {
                return Err(
                    "Horizon installation identity does not match its platform and release".into(),
                );
            }
            let source = req_str(value, "source")?;
            if source != asset_url(&tag, &asset)? {
                return Err("Horizon installation source is not official".into());
            }
            let archive_hash = hash(&req_str(value, "archive_sha256")?)?;
            hash(&req_str(value, "entry_sha256")?)?;
            let entrypoint = req_str(value, "entrypoint")?;
            let expected_name = if platform.starts_with("windows-") {
                "horizon-launcher.exe"
            } else {
                "horizon-launcher"
            };
            if Path::new(&entrypoint).file_name().and_then(|x| x.to_str()) != Some(expected_name) {
                return Err("Unexpected Horizon entrypoint filename".into());
            }
            Ok(Self {
                id,
                tag,
                platform,
                asset,
                archive_hash,
                archive_bytes: number(value, "archive_bytes")?,
                installed: number(value, "installed")?,
                entrypoint,
                source,
            })
        })
    }
    fn summary(&self) -> String {
        util::object(&[
            ("id", quote(&self.id)),
            ("provider", quote(PROVIDER)),
            ("component", quote("launcher")),
            ("tag", quote(&self.tag)),
            ("platform", quote(&self.platform)),
            ("asset", quote(&self.asset)),
            ("archive_sha256", quote(&self.archive_hash)),
            ("archive_bytes", self.archive_bytes.to_string()),
            ("installed", self.installed.to_string()),
            ("source", quote(&self.source)),
            (
                "verification",
                quote("Pinned upstream Ed25519 release signature and SHA-256"),
            ),
            (
                "launch_command",
                quote(&format!("zmcl horizon launch {}", self.id)),
            ),
            (
                "game_preparation",
                quote(
                    "The official Horizon launcher signs in to an owned Minecraft Java account and prepares game resources.",
                ),
            ),
        ])
    }
}
fn read_installed(app: &App, id: &str) -> Result<(Installed, PathBuf)> {
    valid_installation_id(id)?;
    let root = util::safe_join(&installed_root(app)?, id)?;
    let bytes = util::read_limited(&util::safe_join(&root, "installation.json")?, MAX_MANIFEST)?;
    let installed = Installed::parse(&bytes)?;
    if installed.id != id {
        return Err("Horizon manifest ID does not match its directory".into());
    }
    let payload = util::safe_join(&root, "payload")?;
    util::safe_join(&payload, &installed.entrypoint)?;
    Ok((installed, root))
}
fn check_installed(app: &App, root: &Path) -> Result<()> {
    helper(
        app,
        &[
            "check".into(),
            root.join("installation.json").into_os_string(),
            root.join("payload").into_os_string(),
        ],
    )
    .map(|_| ())
}

/// Downloads and installs a signed portable official Horizon launcher. None or
/// "latest" selects the newest compatible signed launcher on the recent page.
/// Installation does not run Horizon or any downloaded installation script.
pub fn install(app: &App, tag: Option<&str>) -> Result<String> {
    let platform = current_platform()?;
    let asset_name = platform_asset(platform)?;
    let _lock = util::Lock::acquire(&app.root, "horizon-provider")?;
    let release = if let Some(requested) = tag.filter(|v| *v != "latest") {
        let expected = normalize_tag(requested)?;
        let bytes = github(&format!("/releases/tags/{}", net::encode(&expected)))?;
        let release = with_json(&bytes, parse_release)?;
        if release.tag != expected {
            return Err("GitHub returned a different Horizon release tag".into());
        }
        release
    } else {
        catalog()?
            .into_iter()
            .find(|r| {
                r.assets.iter().any(|a| a.name == asset_name)
                    && r.assets.iter().any(|a| a.name == "SHA256SUMS")
                    && r.assets.iter().any(|a| a.name == "SHA256SUMS.sig")
            })
            .ok_or(
                "No compatible signed Horizon launcher is available on the recent releases page",
            )?
    };
    let asset = select_asset(&release, asset_name)?;
    let sums = select_asset(&release, "SHA256SUMS")?;
    let signature = select_asset(&release, "SHA256SUMS.sig")?;
    if sums.size > 1024 * 1024 || signature.size > 16384 {
        return Err("Horizon signature metadata exceeds its budget".into());
    }
    let id = installation_id(&release.tag, platform)?;
    let base = installed_root(app)?;
    private_directory(&base)?;
    let destination = util::safe_join(&base, &id)?;
    if destination.exists() {
        let (installed, root) = read_installed(app, &id)?;
        if installed.archive_hash != asset.sha256 {
            return Err(
                "Existing Horizon tag has different release bytes; it will not be overwritten"
                    .into(),
            );
        }
        check_installed(app, &root)?;
        return Ok(installed.summary());
    }
    let sums_path = cached(app, sums)?;
    let signature_path = cached(app, signature)?;
    let archive = cached(app, asset)?;
    let stage = Temporary(util::safe_join(&base, &format!(".stage-{}", nonce()))?);
    fs::create_dir(&stage.0).map_err(|e| e.to_string())?;
    private_directory(&stage.0)?;
    let payload = stage.0.join("payload");
    private_directory(&payload)?;
    let result = helper(
        app,
        &[
            "install".into(),
            archive.into_os_string(),
            sums_path.into_os_string(),
            signature_path.into_os_string(),
            release.tag.clone().into(),
            asset.name.clone().into(),
            asset.sha256.clone().into(),
            platform.into(),
            payload.into_os_string(),
        ],
    )?;
    let manifest = with_json(result.as_bytes(), |value| {
        if value.get("signature_verified").and_then(|v| v.as_bool()) != Some(true) {
            return Err("Horizon release signature was not verified".into());
        }
        let entrypoint = req_str(value, "entrypoint")?;
        util::safe_join(&stage.0.join("payload"), &entrypoint)?;
        let entry_hash = hash(&req_str(value, "entry_sha256")?)?;
        let files = value
            .get("files")
            .filter(|v| v.kind() == Kind::Array)
            .ok_or("Missing Horizon extracted file manifest")?;
        let directories = value
            .get("directories")
            .filter(|v| v.kind() == Kind::Array)
            .ok_or("Missing Horizon extracted directories")?;
        Ok(util::object(&[
            ("format_version", "1".into()),
            ("provider", quote(PROVIDER)),
            ("component", quote("launcher")),
            ("id", quote(&id)),
            ("tag", quote(&release.tag)),
            ("platform", quote(platform)),
            ("asset", quote(&asset.name)),
            ("source", quote(&asset.url)),
            ("release_id", release.id.to_string()),
            ("asset_id", asset.id.to_string()),
            ("commit", quote(&release.commit)),
            ("published", quote(&release.published)),
            ("archive_sha256", quote(&asset.sha256)),
            ("archive_bytes", asset.size.to_string()),
            ("installed", util::now().to_string()),
            ("entrypoint", quote(&entrypoint)),
            ("entry_sha256", quote(&entry_hash)),
            ("signature_verified", "true".into()),
            ("files", raw(files)),
            ("directories", raw(directories)),
        ]))
    })?;
    util::atomic_write(&stage.0.join("installation.json"), manifest.as_bytes())?;
    check_installed(app, &stage.0)?;
    if destination.exists() {
        return Err(
            "Horizon installation destination appeared during staging; refusing to replace it"
                .into(),
        );
    }
    fs::rename(&stage.0, &destination).map_err(|e| e.to_string())?;
    Ok(Installed::parse(manifest.as_bytes())?.summary())
}

/// Lists provider-managed launcher versions without executing any of them.
pub fn list(app: &App) -> Result<String> {
    let base = installed_root(app)?;
    if !base.exists() {
        return Ok("[]".into());
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(&base).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry
            .file_name()
            .to_str()
            .ok_or("Non-UTF8 Horizon installation directory")?
            .to_owned();
        if name.starts_with('.') {
            continue;
        }
        if entries.len() >= 512 {
            return Err("Horizon installation listing exceeds 512 versions".into());
        }
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let (installed, _) = read_installed(app, &name)?;
        entries.push((name, installed.summary()));
    }
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(format!(
        "[{}]",
        entries
            .into_iter()
            .map(|(_, v)| v)
            .collect::<Vec<_>>()
            .join(",")
    ))
}

/// Explicitly starts a verified official launcher in provider-owned persistent
/// data directories. Its built-in self-update is disabled; zmcl installs new
/// signed versions without replacing the previous version or its game data.
pub fn launch(app: &App, id: &str, dry: bool, wait: bool) -> Result<String> {
    valid_installation_id(id)?;
    let _lock = util::Lock::acquire(&app.root, "horizon-provider")?;
    let (installed, root) = read_installed(app, id)?;
    if installed.platform != current_platform()? {
        return Err("Installed Horizon platform does not match this machine".into());
    }
    check_installed(app, &root)?;
    let executable = util::safe_join(&root.join("payload"), &installed.entrypoint)?;
    let data = util::safe_join(&provider_root(app)?, "data")?;
    let launcher_home = util::safe_join(&data, "launcher")?;
    let game_home = util::safe_join(&data, "game")?;
    let location = util::safe_join(&data, "location.json")?;
    let environment = util::object(&[
        (
            "HORIZON_LAUNCHER_HOME",
            quote(&launcher_home.to_string_lossy()),
        ),
        ("HORIZON_HOME", quote(&game_home.to_string_lossy())),
        (
            "HORIZON_LAUNCHER_LOCATION_FILE",
            quote(&location.to_string_lossy()),
        ),
        ("HORIZON_LAUNCHER_NO_SELF_UPDATE", quote("1")),
    ]);
    if dry {
        return Ok(util::object(&[
            ("provider", quote(PROVIDER)),
            ("component", quote("launcher")),
            ("id", quote(id)),
            ("executable", quote(&executable.to_string_lossy())),
            ("args", "[]".into()),
            ("environment", environment),
            ("working_directory", quote(&data.to_string_lossy())),
            ("verified", "true".into()),
            ("executed", "false".into()),
        ]));
    }
    private_directory(&data)?;
    private_directory(&launcher_home)?;
    private_directory(&game_home)?;
    let logs = util::safe_join(&data, "logs")?;
    private_directory(&logs)?;
    let logfile = logs.join(format!("{id}-{}.log", nonce()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let log = options.open(&logfile).map_err(|e| e.to_string())?;
    let mut command = Command::new(&executable);
    command
        .current_dir(&data)
        .env("HORIZON_LAUNCHER_HOME", &launcher_home)
        .env("HORIZON_HOME", &game_home)
        .env("HORIZON_LAUNCHER_LOCATION_FILE", &location)
        .env("HORIZON_LAUNCHER_NO_SELF_UPDATE", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x00000200 | 0x08000000); // New group, no console.
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Cannot start the verified Horizon launcher: {e}"))?;
    let pid = child.id();
    if let Err(error) = util::atomic_write(
        &data.join("last-launch.json"),
        util::object(&[
            ("id", quote(id)),
            ("pid", pid.to_string()),
            ("started", util::now().to_string()),
            ("executable", quote(&executable.to_string_lossy())),
            ("log", quote(&logfile.to_string_lossy())),
        ])
        .as_bytes(),
    ) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    if wait {
        let status = child.wait().map_err(|e| e.to_string())?;
        if !status.success() {
            return Err(format!(
                "Horizon exited with {status}; inspect {}",
                logfile.display()
            ));
        }
    } else {
        std::thread::spawn(move || {
            let _ = child.wait();
        });
    }
    Ok(util::object(&[
        ("provider", quote(PROVIDER)),
        ("component", quote("launcher")),
        ("id", quote(id)),
        ("pid", pid.to_string()),
        ("spawned", "true".into()),
        ("waited", wait.to_string()),
        ("log", quote(&logfile.to_string_lossy())),
        ("data_directory", quote(&data.to_string_lossy())),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn horizon_tags_and_sources_are_scoped() {
        assert_eq!(normalize_tag("0.2.84").unwrap(), "launcher@0.2.84");
        assert_eq!(tag_version("launcher@0.2.84").unwrap(), (0, 2, 84));
        for value in [
            "nightly",
            "v0.2.84",
            "launcher@../0.2",
            "launcher@0.2",
            "launcher@0.2.0-beta",
        ] {
            assert!(tag_version(value).is_err(), "{value}");
        }
        assert_eq!(
            asset_url("launcher@0.2.84", "SHA256SUMS").unwrap(),
            "https://github.com/horizon-rust/horizon/releases/download/launcher%400.2.84/SHA256SUMS"
        );
        for name in ["../a", "a/b", "a\\b", "a?token=x"] {
            assert!(asset_url("launcher@0.2.84", name).is_err());
        }
    }
    #[test]
    fn horizon_platform_ids_are_portable() {
        for (platform, asset) in PLATFORMS {
            assert_eq!(platform_asset(platform).unwrap(), *asset);
            valid_installation_id(&installation_id("launcher@0.2.84", platform).unwrap()).unwrap();
        }
        assert!(installation_id("launcher@0.2.84", "other").is_err());
        for invalid in [
            "../launcher-0.2.84-linux-x86_64",
            "launcher-../0.2.84-linux-x86_64",
            "launcher-0.2.84-unknown",
            "launcher-0.2.84-linux-x86_64/extra",
            "launcher-0.2.84-linux-x86_64\\\\extra",
            "launcher-0.2.84:stream-linux-x86_64",
        ] {
            assert!(valid_installation_id(invalid).is_err(), "{invalid}");
        }
    }
    #[test]
    fn horizon_release_rejects_wrong_origin_and_missing_digest() {
        let make = |url: &str, digest: &str| {
            format!(
                r#"{{"id":1,"draft":false,"prerelease":false,"tag_name":"launcher@0.2.84","published_at":"2026-11-19T23:08:54Z","target_commitish":"41e53103","assets":[{{"id":2,"name":"horizon-launcher-linux-x86_64.tar.gz","size":100,"digest":{},"browser_download_url":{}}}]}}"#,
                quote(digest),
                quote(url)
            )
        };
        let url = asset_url("launcher@0.2.84", "horizon-launcher-linux-x86_64.tar.gz").unwrap();
        let digest = format!("sha256:{}", "a".repeat(64));
        assert!(with_json(make(&url, &digest).as_bytes(), parse_release).is_ok());
        assert!(
            with_json(
                make(
                    "https://github.com/other/horizon/releases/download/x/file",
                    &digest
                )
                .as_bytes(),
                parse_release
            )
            .is_err()
        );
        assert!(with_json(make(&url, "sha1:abcd").as_bytes(), parse_release).is_err());
    }
}
