use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

pub type Result<T> = std::result::Result<T, String>;
static SEQUENCE: AtomicU64 = AtomicU64::new(0);

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn safe_join(root: &Path, relative: &str) -> Result<PathBuf> {
    if relative
        .split('/')
        .any(|p| p.is_empty() || p == "." || p == "..")
        || relative.len() > 4096
        || relative.contains(['\\', ':'])
        || relative.chars().any(char::is_control)
        || relative.starts_with('/')
    {
        return Err("Invalid relative path".into());
    }
    let mut out = root.to_path_buf();
    for part in Path::new(relative).components() {
        let Component::Normal(part) = part else {
            return Err("Unsafe path component".into());
        };
        let text = part.to_string_lossy();
        let base = text.split('.').next().unwrap_or("").to_ascii_uppercase();
        if text.ends_with([' ', '.'])
            || matches!(base.as_str(), "CON" | "PRN" | "AUX" | "NUL")
            || (base.len() == 4
                && (base.starts_with("COM") || base.starts_with("LPT"))
                && base.as_bytes()[3].is_ascii_digit())
        {
            return Err("Unsupported portable file name".into());
        }
        out.push(part);
        if fs::symlink_metadata(&out).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err("Symbolic links are not allowed in managed paths".into());
        }
    }
    Ok(out)
}

pub fn valid_id(id: &str) -> Result<()> {
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        Err("Instance ID must contain 1–64 ASCII letters, numbers, '-' or '_'".into())
    } else {
        Ok(())
    }
}

pub fn read_limited(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let file = File::open(path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    if file.metadata().map_err(|e| e.to_string())?.len() > limit as u64 {
        return Err("File exceeds the configured size limit".into());
    }
    let mut out = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut out)
        .map_err(|e| e.to_string())?;
    if out.len() > limit {
        return Err("File exceeds the configured size limit".into());
    }
    Ok(out)
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or("Missing parent directory")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("Refusing to replace a symbolic link".into());
    }
    let tmp = parent.join(format!(
        ".zmcl-{}-{}.tmp",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&tmp).map_err(|e| e.to_string())?;
        file.write_all(bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        fs::rename(&tmp, path).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}

pub fn object(fields: &[(&str, String)]) -> String {
    let pairs: Vec<_> = fields
        .iter()
        .map(|(k, v)| format!("{}:{v}", zmcl_core::json::quote(k)))
        .collect();
    format!("{{{}}}", pairs.join(","))
}

pub fn command_exists(program: &str) -> bool {
    std::process::Command::new(program)
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

pub struct Lock {
    // Keep the handle open for the whole operation. Never unlink the lock file:
    // removing it would allow another process to lock a different inode.
    _file: File,
}
impl Lock {
    pub fn acquire(root: &Path, name: &str) -> Result<Self> {
        let path = safe_join(root, &format!("locks/{name}.lock"))?;
        fs::create_dir_all(path.parent().ok_or("Missing lock parent")?)
            .map_err(|e| e.to_string())?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&path)
            .map_err(|e| format!("Cannot open operation lock: {e}"))?;
        file.try_lock()
            .map_err(|e| format!("Operation already active, or locking is unavailable: {e}"))?;
        file.set_len(0).map_err(|e| e.to_string())?;
        write!(
            file,
            "locking=os-v1\npid={}\nstarted={}\n",
            std::process::id(),
            now()
        )
        .map_err(|e| e.to_string())?;
        Ok(Self { _file: file })
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        // Explicit unlock also handles a descriptor inherited briefly by a
        // concurrently spawning child before its close-on-exec takes effect.
        let _ = self._file.unlock();
    }
}

/// Refuse to use a half-published instance. Each owning subsystem is responsible
/// for proving and recovering its own journal before this gate can pass.
pub fn check_pending_transactions(instance: &Path) -> Result<()> {
    let id = instance
        .file_name()
        .and_then(|x| x.to_str())
        .unwrap_or("ID");
    for (file, action) in [
        (".content-journal.json", "mods recover"),
        (".resources-journal.json", "resources recover"),
        (".backup-restore.json", "backup create"),
    ] {
        if fs::symlink_metadata(instance.join(file)).is_ok() {
            return Err(format!(
                "Unfinished instance transaction {file}; run 'zmcl {action} {id}' to recover it before using this instance."
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unsafe_paths_are_rejected() {
        for p in [
            "../outside",
            "/root",
            "C:/x",
            "a\\b",
            "a/../../b",
            "NUL.txt",
            "foo.",
            "a\0b",
        ] {
            assert!(
                safe_join(Path::new("/tmp/zmcl-path-test"), p).is_err(),
                "{p}"
            );
        }
        assert!(safe_join(Path::new("/tmp/zmcl-path-test"), "a/b.jar").is_ok());
    }
    #[test]
    fn locks_exclude_competitors_and_allow_reuse() {
        let dir = std::env::temp_dir().join(format!(
            "zmcl-lock-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let first = Lock::acquire(&dir, "instance-a").unwrap();
        assert!(Lock::acquire(&dir, "instance-a").is_err());
        let other = Lock::acquire(&dir, "instance-b").unwrap();
        drop(first);
        let next = Lock::acquire(&dir, "instance-a").unwrap();
        drop(next);
        drop(other);
        assert!(dir.join("locks/instance-a.lock").is_file());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn lock_survives_process_death() {
        const KEY: &str = "ZMCL_LOCK_TEST_CHILD";
        if let Some(path) = std::env::var_os(KEY) {
            let dir = PathBuf::from(path);
            let _lock = Lock::acquire(&dir, "crash").unwrap();
            fs::write(dir.join("ready"), b"ready").unwrap();
            std::thread::sleep(std::time::Duration::from_secs(60));
            return;
        }
        let dir = std::env::temp_dir().join(format!(
            "zmcl-lock-crash-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "util::tests::lock_survives_process_death",
                "--nocapture",
            ])
            .env(KEY, &dir)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !dir.join("ready").is_file() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let ready = dir.join("ready").is_file();
        if ready {
            assert!(Lock::acquire(&dir, "crash").is_err());
        }
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(ready, "child did not acquire the test lock");
        let recovered = Lock::acquire(&dir, "crash").unwrap();
        drop(recovered);
        fs::remove_dir_all(dir).unwrap();
    }
}
