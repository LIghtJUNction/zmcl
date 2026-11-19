//! Optional, immutable Temurin JRE installations from the official Adoptium API.
//! Source: https://github.com/adoptium/api.adoptium.net/blob/main/docs/cookbook.adoc
use crate::{
    App, net, runtime, store,
    util::{self, Result},
};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{SystemTime, UNIX_EPOCH},
};
use zmcl_core::{
    json::{get_str, quote, req_str, with_json},
    path,
};
const EXTRACT: &str = include_str!("java/extract.py");

#[derive(Debug)]
struct Package {
    release: String,
    url: String,
    checksum: String,
    size: u64,
    filename: String,
}

fn platform() -> Result<(&'static str, &'static str)> {
    let os = match std::env::consts::OS {
        "linux" if cfg!(target_env = "musl") => "alpine-linux",
        "linux" => "linux",
        "windows" => "windows",
        "macos" => "mac",
        _ => {
            return Err(
                "Adoptium runtime installation is unavailable for this operating system".into(),
            );
        }
    };
    let arch = match std::env::consts::ARCH {
        "x86_64" => "x64",
        "x86" => "x86",
        "aarch64" => "aarch64",
        "arm" => "arm",
        "powerpc64" if cfg!(target_endian = "little") => "ppc64le",
        "powerpc64" => "ppc64",
        "s390x" => "s390x",
        "riscv64" => "riscv64",
        _ => {
            return Err(
                "Adoptium runtime installation is unavailable for this architecture".into(),
            );
        }
    };
    Ok((os, arch))
}
fn group(app: &App, major: u32) -> Result<PathBuf> {
    if !(8..=255).contains(&major) {
        return Err("Java major version must be 8..255".into());
    }
    let (os, arch) = platform()?;
    util::safe_join(&app.root, &format!("runtimes/{major}-{os}-{arch}"))
}
fn select_package(bytes: &[u8], major: u32, os: &str, arch: &str) -> Result<Package> {
    with_json(bytes, |root| {
        if root.kind() != zmcl_core::json::Kind::Array {
            return Err("Invalid Adoptium asset response".into());
        }
        for entry in root.array() {
            if get_str(entry, "vendor").as_deref() != Some("eclipse") {
                continue;
            }
            let Some(binary) = entry.get("binary") else {
                continue;
            };
            if get_str(binary, "image_type").as_deref() != Some("jre")
                || get_str(binary, "os").as_deref() != Some(os)
                || get_str(binary, "architecture").as_deref() != Some(arch)
                || get_str(binary, "jvm_impl").as_deref() != Some("hotspot")
            {
                continue;
            }
            let version = entry.get("version").ok_or("Missing Adoptium version")?;
            if version.get("major").and_then(|v| v.as_u64()) != Some(u64::from(major)) {
                continue;
            }
            if get_str(version, "pre").is_some_and(|s| !s.is_empty()) {
                continue;
            }
            let release = req_str(entry, "release_name")?;
            if release.len() > 96 || release.contains("-ea") || release.contains("_ea") {
                continue;
            }
            path::validate_id(&release)?;
            let package = binary.get("package").ok_or("Missing Adoptium package")?;
            let checksum = req_str(package, "checksum")?.to_ascii_lowercase();
            if !path::valid_sha256(&checksum) {
                return Err("Adoptium package has no valid SHA-256 digest".into());
            }
            let filename = req_str(package, "name")?;
            path::validate_relative(&filename)?;
            if filename.contains('/')
                || !(filename.ends_with(".zip")
                    || filename.ends_with(".tar.gz")
                    || filename.ends_with(".tgz"))
            {
                return Err("Adoptium package is not a supported ZIP or gzip TAR archive".into());
            }
            let url = zmcl_core::model::normalize_url(&req_str(package, "link")?)?;
            let size = package
                .get("size")
                .and_then(|v| v.as_u64())
                .ok_or("Missing Adoptium package size")?;
            if size == 0 || size > 512 * 1024 * 1024 {
                return Err("Adoptium runtime archive exceeds the 512 MiB limit".into());
            }
            return Ok(Package {
                release,
                url,
                checksum,
                size,
                filename,
            });
        }
        Err(format!(
            "No stable Temurin Java {major} JRE is available for {os}/{arch}"
        ))
    })
}
fn metadata(major: u32, package: &Package, executable: &str) -> String {
    util::object(&[
        ("schema", "1".into()),
        ("major", major.to_string()),
        ("release", quote(&package.release)),
        ("sha256", quote(&package.checksum)),
        ("executable", quote(executable)),
        ("source", quote("adoptium")),
    ])
}
fn checked_executable(release: &Path, relative: &str) -> Result<PathBuf> {
    path::validate_relative(relative)?;
    let candidate = release.join(relative);
    let actual = candidate.canonicalize().map_err(|e| e.to_string())?;
    let root = release.canonicalize().map_err(|e| e.to_string())?;
    if !actual.starts_with(&root) || !actual.is_file() {
        return Err("Managed Java executable leaves its runtime".into());
    }
    Ok(actual)
}
fn find_java(stage: &Path) -> Result<String> {
    let exe = if cfg!(windows) { "java.exe" } else { "java" };
    let mut roots = vec![stage.to_path_buf()];
    for entry in fs::read_dir(stage).map_err(|e| e.to_string())?.take(128) {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            roots.push(entry.path());
        }
    }
    let mut found = Vec::new();
    for root in roots {
        for java in [
            root.join("bin").join(exe),
            root.join("Contents/Home/bin").join(exe),
        ] {
            if java.is_file() {
                let relative = java
                    .strip_prefix(stage)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                let _ = checked_executable(stage, &relative)?;
                if !found.contains(&relative) {
                    found.push(relative);
                }
            }
        }
    }
    if found.len() != 1 {
        return Err("Java archive must contain exactly one usable JRE".into());
    }
    Ok(found.remove(0))
}
fn verify(release: &Path, relative: &str, major: u32) -> Result<PathBuf> {
    let executable = checked_executable(release, relative)?;
    let actual = runtime::java_major(&executable.to_string_lossy())?;
    if actual != major {
        return Err(format!(
            "Downloaded runtime is Java {actual}; Java {major} was requested"
        ));
    }
    Ok(executable)
}
fn selected_executable(base: &Path) -> Result<PathBuf> {
    let bytes = util::read_limited(&util::safe_join(base, "selected.json")?, 64 * 1024)?;
    let (directory, relative) = with_json(&bytes, |v| {
        Ok((req_str(v, "directory")?, req_str(v, "executable")?))
    })?;
    path::validate_id(&directory)?;
    let release = util::safe_join(base, &directory)?;
    checked_executable(&release, &relative)
}
/// Selected managed runtimes only. No process is launched while discovering paths.
pub fn candidates(app: &App) -> Vec<String> {
    let Ok((os, arch)) = platform() else {
        return Vec::new();
    };
    let Ok(root) = util::safe_join(&app.root, "runtimes") else {
        return Vec::new();
    };
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    let suffix = format!("-{os}-{arch}");
    let mut result = Vec::new();
    for entry in entries.take(512).flatten() {
        if !entry.file_type().is_ok_and(|t| t.is_dir()) {
            continue;
        }
        if !entry.file_name().to_string_lossy().ends_with(&suffix) {
            continue;
        }
        if let Ok(path) = selected_executable(&entry.path()) {
            result.push(path.to_string_lossy().into_owned());
        }
    }
    result.sort();
    result.dedup();
    result
}
/// Installed release inventory; older releases stay available for running games.
pub fn list(app: &App) -> Result<String> {
    let root = util::safe_join(&app.root, "runtimes")?;
    if !root.exists() {
        return Ok("[]".into());
    }
    let mut items = Vec::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())?.take(512) {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let selected = selected_executable(&entry.path()).ok();
        for release in fs::read_dir(entry.path())
            .map_err(|e| e.to_string())?
            .take(512)
        {
            let release = release.map_err(|e| e.to_string())?;
            if !release.file_type().map_err(|e| e.to_string())?.is_dir()
                || release.file_name().to_string_lossy().starts_with('.')
            {
                continue;
            }
            let meta = util::safe_join(&release.path(), "runtime.json")?;
            if !meta.is_file() {
                continue;
            }
            let bytes = util::read_limited(&meta, 64 * 1024)?;
            let item = with_json(&bytes, |v| {
                let executable = checked_executable(&release.path(), &req_str(v, "executable")?)?;
                let major = v
                    .get("major")
                    .and_then(|n| n.as_u64())
                    .ok_or("Invalid runtime metadata")?;
                Ok(util::object(&[
                    ("major", major.to_string()),
                    ("release", quote(&req_str(v, "release")?)),
                    ("sha256", quote(&req_str(v, "sha256")?)),
                    ("source", quote("adoptium")),
                    ("path", quote(&executable.to_string_lossy())),
                    (
                        "selected",
                        (selected.as_ref() == Some(&executable)).to_string(),
                    ),
                ]))
            })?;
            items.push(item);
        }
    }
    items.sort();
    Ok(format!("[{}]", items.join(",")))
}

/// Install only when requested. Existing release directories are never overwritten.
pub fn install(app: &App, major: u32) -> Result<String> {
    let base = group(app, major)?;
    let (os, arch) = platform()?;
    let _lock = util::Lock::acquire(&app.root, &format!("java-{major}-{os}-{arch}"))?;
    let url = format!(
        "https://api.adoptium.net/v3/assets/latest/{major}/hotspot?architecture={arch}&image_type=jre&os={os}&vendor=eclipse"
    );
    let bytes = net::get(&url)?;
    let package = select_package(&bytes, major, os, arch)?;
    let archive = store::ensure(
        app,
        &format!(
            "runtime-archives/{}/{}",
            &package.checksum[..16],
            package.filename
        ),
        &package.url,
        None,
        Some(&package.checksum),
        Some(package.size),
    )?;
    fs::create_dir_all(&base).map_err(|e| e.to_string())?;
    let directory = format!("{}-{}", package.release, &package.checksum[..16]);
    path::validate_id(&directory)?;
    let release = util::safe_join(&base, &directory)?;
    let relative = if release.exists() {
        let bytes = util::read_limited(&util::safe_join(&release, "runtime.json")?, 64 * 1024)?;
        let relative = with_json(&bytes, |v| {
            if get_str(v, "sha256").as_deref() != Some(&package.checksum) {
                return Err("Existing runtime metadata does not match the release".into());
            }
            req_str(v, "executable")
        })?;
        verify(&release, &relative, major)?;
        relative
    } else {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let stage = util::safe_join(&base, &format!(".stage-{}-{nonce}", std::process::id()))?;
        let prepared = (|| {
            let output = Command::new("python3")
                .args(["-c", EXTRACT])
                .arg(&archive)
                .arg(&stage)
                .stdin(Stdio::null())
                .output()
                .map_err(|e| format!("Java archive extraction requires system Python 3: {e}"))?;
            if !output.status.success() {
                return Err(format!(
                    "Java archive extraction failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                        .chars()
                        .take(1200)
                        .collect::<String>()
                ));
            }
            let relative = find_java(&stage)?;
            verify(&stage, &relative, major)?;
            util::atomic_write(
                &util::safe_join(&stage, "runtime.json")?,
                metadata(major, &package, &relative).as_bytes(),
            )?;
            fs::rename(&stage, &release).map_err(|e| e.to_string())?;
            Ok(relative)
        })();
        if prepared.is_err() && stage.is_dir() {
            let _ = fs::remove_dir_all(&stage);
        }
        prepared?
    };
    let executable = verify(&release, &relative, major)?;
    let selection = util::object(&[
        ("schema", "1".into()),
        ("major", major.to_string()),
        ("directory", quote(&directory)),
        ("release", quote(&package.release)),
        ("sha256", quote(&package.checksum)),
        ("executable", quote(&relative)),
    ]);
    util::atomic_write(
        &util::safe_join(&base, "selected.json")?,
        selection.as_bytes(),
    )?;
    Ok(util::object(&[
        ("major", major.to_string()),
        ("release", quote(&package.release)),
        ("path", quote(&executable.to_string_lossy())),
        ("sha256", quote(&package.checksum)),
        ("selected", true.to_string()),
        ("source", quote("adoptium")),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn requires_exact_platform_and_pinned_sha256() {
        let good=br#"[{"vendor":"eclipse","release_name":"jdk-21.0.9+10","version":{"major":21},"binary":{"image_type":"jre","os":"linux","architecture":"x64","jvm_impl":"hotspot","package":{"name":"jre.tar.gz","link":"https://github.com/adoptium/example.tar.gz","checksum":"0123456789012345678901234567890123456789012345678901234567890123","size":42}}}]"#;
        assert!(select_package(good, 21, "linux", "x64").is_ok());
        assert!(select_package(good, 17, "linux", "x64").is_err());
        assert!(select_package(good, 21, "windows", "x64").is_err());
        let invalid = String::from_utf8(good.to_vec()).unwrap().replace(
            "0123456789012345678901234567890123456789012345678901234567890123",
            "bad",
        );
        assert!(select_package(invalid.as_bytes(), 21, "linux", "x64").is_err());
    }
}
