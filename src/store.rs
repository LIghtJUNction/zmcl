use crate::{
    App, net,
    util::{self, Result},
};
use std::{
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
use zmcl_core::hash::{Sha1, Sha256};
static NEXT: AtomicU64 = AtomicU64::new(0);

pub fn digests(path: &Path) -> Result<(String, String, u64)> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let mut sha1 = Sha1::new();
    let mut sha256 = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut size = 0u64;
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        sha1.update(&buffer[..n]);
        sha256.update(&buffer[..n]);
        size += n as u64;
    }
    Ok((
        zmcl_core::hash::hex(&sha1.finalize()),
        zmcl_core::hash::hex(&sha256.finalize()),
        size,
    ))
}
fn valid_hash(hash: &str, len: usize) -> Result<()> {
    if hash.len() != len || !hash.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("Invalid content digest".into());
    }
    Ok(())
}
fn matches(
    path: &Path,
    sha1: Option<&str>,
    sha256: Option<&str>,
    size: Option<u64>,
) -> Result<bool> {
    if !path.is_file() {
        return Ok(false);
    }
    let meta = fs::metadata(path).map_err(|e| e.to_string())?;
    if size.is_some_and(|s| s != meta.len()) {
        return Ok(false);
    }
    if sha1.is_none() && sha256.is_none() {
        return Ok(true);
    }
    let (a, b, _) = digests(path)?;
    Ok(sha1.is_none_or(|v| v.eq_ignore_ascii_case(&a))
        && sha256.is_none_or(|v| v.eq_ignore_ascii_case(&b)))
}
pub fn materialize(source: &Path, dest: &Path, immutable: bool) -> Result<()> {
    if fs::symlink_metadata(dest).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("Refusing symbolic link destination".into());
    }
    let parent = dest.parent().ok_or("Missing destination parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let temp = parent.join(format!(
        ".zmcl-copy-{}-{}.tmp",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        if !immutable || fs::hard_link(source, &temp).is_err() {
            fs::copy(source, &temp).map_err(|e| e.to_string())?;
            if !immutable {
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    fs::set_permissions(&temp, fs::Permissions::from_mode(0o600))
                        .map_err(|e| e.to_string())?;
                }
                #[cfg(not(unix))]
                {
                    let mut p = fs::metadata(&temp)
                        .map_err(|e| e.to_string())?
                        .permissions();
                    p.set_readonly(false);
                    fs::set_permissions(&temp, p).map_err(|e| e.to_string())?;
                }
            }
        }
        fs::rename(&temp, dest).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}
pub fn ensure(
    app: &App,
    relative: &str,
    url: &str,
    sha1: Option<&str>,
    sha256: Option<&str>,
    size: Option<u64>,
) -> Result<PathBuf> {
    if sha1.is_none() && sha256.is_none() {
        return Err("A trusted digest is required before downloading content".into());
    }
    if let Some(x) = sha1 {
        valid_hash(x, 40)?;
    }
    if let Some(x) = sha256 {
        valid_hash(x, 64)?;
    }
    let root = app.shared();
    let dest = util::safe_join(&root, relative)?;
    if matches(&dest, sha1, sha256, size)? {
        return Ok(dest);
    }
    let expected_object = if let Some(h) = sha256 {
        Some(format!("objects/sha256/{}", h.to_ascii_lowercase()))
    } else {
        sha1.map(|h| format!("objects/sha1/{}", h.to_ascii_lowercase()))
    };
    if let Some(relative) = expected_object.as_ref() {
        let cached = util::safe_join(&root, relative)?;
        if matches(&cached, sha1, sha256, size)? {
            materialize(&cached, &dest, true)?;
            return Ok(dest);
        }
    }
    let cache = util::safe_join(&app.root, "cache/downloads")?;
    fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    let temp = cache.join(format!(
        "{}-{}.part",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let maximum = size.unwrap_or(1024 * 1024 * 1024);
        if maximum > 4 * 1024 * 1024 * 1024 {
            return Err("Artifact exceeds 4 GiB download limit".into());
        }
        net::download(url, &temp, maximum)?;
        let (actual1, actual256, actual_size) = digests(&temp)?;
        if sha1.is_some_and(|v| !v.eq_ignore_ascii_case(&actual1))
            || sha256.is_some_and(|v| !v.eq_ignore_ascii_case(&actual256))
            || size.is_some_and(|v| v != actual_size)
        {
            return Err("Downloaded content failed hash or size verification".into());
        }
        let object = util::safe_join(
            &root,
            &expected_object.unwrap_or_else(|| format!("objects/sha256/{actual256}")),
        )?;
        fs::create_dir_all(object.parent().ok_or("Missing object parent")?)
            .map_err(|e| e.to_string())?;
        fs::rename(&temp, &object).map_err(|e| e.to_string())?;
        let mut perms = fs::metadata(&object)
            .map_err(|e| e.to_string())?
            .permissions();
        perms.set_readonly(true);
        fs::set_permissions(&object, perms).map_err(|e| e.to_string())?;
        materialize(&object, &dest, true)?;
        Ok(dest.clone())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
pub fn stats(app: &App) -> Result<String> {
    let mut files = 0u64;
    let mut logical = 0u64;
    let mut physical = 0u64;
    let mut stack = vec![app.root.clone()];
    #[cfg(unix)]
    let mut seen = std::collections::BTreeSet::new();
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let metadata = entry.metadata().map_err(|e| e.to_string())?;
            if entry.file_type().map_err(|e| e.to_string())?.is_symlink() {
                continue;
            }
            if metadata.is_dir() {
                stack.push(entry.path());
            } else if metadata.is_file() {
                files += 1;
                logical += metadata.len();
                #[cfg(unix)]
                {
                    use std::os::unix::fs::MetadataExt;
                    if seen.insert((metadata.dev(), metadata.ino())) {
                        physical += metadata.blocks() * 512;
                    }
                }
                #[cfg(not(unix))]
                {
                    physical += metadata.len();
                }
            }
        }
    }
    Ok(util::object(&[
        ("files", files.to_string()),
        ("logical_bytes", logical.to_string()),
        ("allocated_bytes", physical.to_string()),
        ("physical_deduplication_measured", cfg!(unix).to_string()),
    ]))
}

pub fn batch(app: &App, artifacts: &[zmcl_core::plan::Artifact], jobs: usize) -> Result<()> {
    use zmcl_core::plan::Artifact;
    let mut pending: Vec<&Artifact> = Vec::new();
    for item in artifacts {
        if item.download.sha1.is_none() {
            return Err("Batch downloads require a trusted SHA-1 digest".into());
        }
        if let Some(h) = &item.download.sha1 {
            valid_hash(h, 40)?;
        }
        let dest = util::safe_join(&app.shared(), &item.path)?;
        if matches(
            &dest,
            item.download.sha1.as_deref(),
            None,
            item.download.size,
        )? {
            continue;
        }
        if let Some(h) = &item.download.sha1 {
            let cached = util::safe_join(
                &app.shared(),
                &format!("objects/sha1/{}", h.to_ascii_lowercase()),
            )?;
            if matches(&cached, Some(h), None, item.download.size)? {
                materialize(&cached, &dest, true)?;
                continue;
            }
        }
        if item.download.url.is_empty() {
            return Err(format!(
                "Loader-generated file is missing or invalid: {}. Reinstall the loader with a compatible Java runtime.",
                item.path
            ));
        }
        pending.push(item);
    }
    let count = pending.len();
    if count == 0 {
        return Ok(());
    }
    eprintln!(
        "Downloading {count} verified files with {} connections",
        jobs.clamp(1, 16)
    );
    let cache = util::safe_join(&app.root, "cache/downloads")?;
    fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    for (chunk_index, chunk) in pending.chunks(192).enumerate() {
        let mut transfers = Vec::new();
        for item in chunk {
            let max = item.download.size.unwrap_or(1024 * 1024 * 1024);
            if max > 4 * 1024 * 1024 * 1024 {
                return Err("Artifact exceeds the download limit".into());
            }
            transfers.push((
                item.download.url.clone(),
                cache.join(format!(
                    "{}-{}.part",
                    std::process::id(),
                    NEXT.fetch_add(1, Ordering::Relaxed)
                )),
                max,
            ));
        }
        let network = net::download_batch(&transfers, jobs);
        let mut failed = 0usize;
        for (item, (_, temp, _)) in chunk.iter().zip(&transfers) {
            let result = (|| {
                if !matches(
                    temp,
                    item.download.sha1.as_deref(),
                    None,
                    item.download.size,
                )? {
                    return Err("Hash or size verification failed".to_owned());
                }
                let (_, hash256, _) = digests(temp)?;
                let relative = if let Some(h) = &item.download.sha1 {
                    format!("objects/sha1/{}", h.to_ascii_lowercase())
                } else {
                    format!("objects/sha256/{hash256}")
                };
                let object = util::safe_join(&app.shared(), &relative)?;
                fs::create_dir_all(object.parent().ok_or("Missing object parent")?)
                    .map_err(|e| e.to_string())?;
                fs::rename(temp, &object).map_err(|e| e.to_string())?;
                let mut perms = fs::metadata(&object)
                    .map_err(|e| e.to_string())?
                    .permissions();
                perms.set_readonly(true);
                fs::set_permissions(&object, perms).map_err(|e| e.to_string())?;
                materialize(&object, &util::safe_join(&app.shared(), &item.path)?, true)
            })();
            if result.is_err() {
                failed += 1;
                let _ = fs::remove_file(temp);
            }
        }
        eprintln!(
            "Verified {}/{} files",
            ((chunk_index + 1) * 192).min(count),
            count
        );
        if failed > 0 {
            return Err(format!(
                "{failed} files failed verification or transfer. Verified files are retained; retry install. {}",
                network.err().unwrap_or_default()
            ));
        }
    }
    Ok(())
}
