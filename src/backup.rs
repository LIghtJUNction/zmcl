//! Private, checksummed local game-data snapshots. System Python 3 supplies the
//! bounded ZIP codec; Rust owns instance locking and the helper's hard timeout.
use crate::{App, util};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type Result<T> = std::result::Result<T, String>;
const MAX_OUTPUT: usize = 8 * 1024 * 1024;

fn token() -> String {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{}-{stamp}", std::process::id())
}
fn valid_backup_id(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 120
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("Backup ID must contain 1–120 ASCII letters, digits, '-' or '_'".into());
    }
    Ok(())
}
fn paths(app: &App, id: &str, create: bool) -> Result<(PathBuf, PathBuf)> {
    app.read_instance(id)?;
    let instance = app.dir(id)?;
    let base = util::safe_join(&app.root, "backups")?;
    let backups = util::safe_join(&base, id)?;
    if create {
        fs::create_dir_all(&backups).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&base, fs::Permissions::from_mode(0o700))
                .map_err(|e| e.to_string())?;
            fs::set_permissions(&backups, fs::Permissions::from_mode(0o700))
                .map_err(|e| e.to_string())?;
        }
    }
    Ok((instance, backups))
}
struct Helper {
    dir: PathBuf,
}
impl Drop for Helper {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}
fn capture<R: Read>(mut stream: R, limit: usize) -> Result<Vec<u8>> {
    let mut result = Vec::new();
    let mut chunk = [0u8; 8192];
    let mut overflow = false;
    loop {
        let count = stream.read(&mut chunk).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if result.len().saturating_add(count) <= limit {
            result.extend_from_slice(&chunk[..count]);
        } else {
            overflow = true;
        }
    }
    if overflow {
        return Err("Backup helper output exceeded its memory budget".into());
    }
    Ok(result)
}
fn run(
    app: &App,
    operation: &str,
    instance: &Path,
    backups: &Path,
    id: &str,
    tail: &[&str],
) -> Result<String> {
    let cache = util::safe_join(&app.root, "cache")?;
    fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    let dir = util::safe_join(&cache, &format!("backup-helper-{}", token()))?;
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let helper = Helper { dir };
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&helper.dir, fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let script = helper.dir.join("archive.py");
    util::atomic_write(&script, include_bytes!("backup/archive.py"))?;
    let executable = if cfg!(windows) { "python" } else { "python3" };
    let mut child = Command::new(executable)
        .arg("-I")
        .arg(&script)
        .arg(operation)
        .arg(instance)
        .arg(backups)
        .arg(id)
        .args(tail)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Backups require system Python 3: {e}"))?;
    let stdout = child.stdout.take().ok_or("Missing backup stdout")?;
    let stderr = child.stderr.take().ok_or("Missing backup stderr")?;
    let output_reader = std::thread::spawn(move || capture(stdout, MAX_OUTPUT));
    let error_reader = std::thread::spawn(move || capture(stderr, 8192));
    let started = Instant::now();
    let mut timed_out = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if started.elapsed() > Duration::from_secs(330) => {
                timed_out = true;
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
    let output = output_reader
        .join()
        .map_err(|_| "Backup output reader failed")?;
    let errors = error_reader
        .join()
        .map_err(|_| "Backup error reader failed")?;
    if timed_out {
        return Err("Backup helper exceeded 330 seconds. Existing snapshots remain intact; the next backup restore/create resolves any restore journal.".into());
    }
    let status = status?;
    let errors = errors?;
    if !status.success() {
        let message = String::from_utf8_lossy(&errors);
        return Err(if message.trim().is_empty() {
            format!("Backup helper exited with {status}")
        } else {
            message.trim().to_owned()
        });
    }
    String::from_utf8(output?)
        .map(|value| value.trim().to_owned())
        .map_err(|_| "Backup helper returned invalid UTF-8".into())
}

/// Creates an immutable, private ZIP under ROOT/backups/ID. Mutable game roots
/// are included except managed binaries, caches, logs, hidden roots and account
/// filenames. A snapshot never overwrites another snapshot.
pub fn create(app: &App, id: &str, label: Option<&str>) -> Result<String> {
    let (instance, backups) = paths(app, id, true)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let label = label.unwrap_or("");
    if label.chars().count() > 128 || label.chars().any(char::is_control) {
        return Err("Backup label must contain at most 128 visible characters".into());
    }
    let created = util::now().to_string();
    let backup_id = format!("b{created}-{}", token());
    run(
        app,
        "create",
        &instance,
        &backups,
        id,
        &[&backup_id, label, &created],
    )
}

/// Lists every retained snapshot. Listing validates its manifest/ZIP structure;
/// file bytes are re-hashed only during restore, as data_verified:false states.
pub fn list(app: &App, id: &str) -> Result<String> {
    let (instance, backups) = paths(app, id, false)?;
    run(app, "list", &instance, &backups, id, &[])
}

/// Verifies the complete snapshot in private staging, creates a pre-restore backup,
/// and publishes the complete game directory under the same instance lock.
/// Preserved managed roots retain their contents. Interrupted directory swaps
/// are recovered conservatively on the next create/restore call.
pub fn restore(app: &App, id: &str, backup_id: &str) -> Result<()> {
    valid_backup_id(backup_id)?;
    let (instance, backups) = paths(app, id, true)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    run(app, "restore", &instance, &backups, id, &[backup_id]).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use zmcl_core::json::{req_str, with_json};
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn app() -> (App, Scratch) {
        let path = std::env::temp_dir().join(format!("zmcl-backup-test-{}", token()));
        fs::create_dir(&path).unwrap();
        let app = App { root: path.clone() };
        fs::create_dir(path.join("locks")).unwrap();
        fs::create_dir(path.join("instances")).unwrap();
        app.create_instance("demo", "1.20.1", "vanilla", "")
            .unwrap();
        (app, Scratch(path))
    }
    #[test]
    fn backup_ids_are_portable_and_confined() {
        for bad in ["", "../secret", "x.zip", "C:drive", "a/b", "a\\b", "a b"] {
            assert!(valid_backup_id(bad).is_err(), "{bad}");
        }
        assert!(valid_backup_id("b123-456_test").is_ok());
    }
    #[test]
    fn backup_roundtrip_preserves_mods_and_old_saves() {
        let (app, _scratch) = app();
        let game = app.dir("demo").unwrap().join("game");
        fs::create_dir(game.join("saves")).unwrap();
        fs::create_dir(game.join("mods")).unwrap();
        fs::write(game.join("saves/level.dat"), b"old-world").unwrap();
        fs::write(game.join("options.txt"), b"music:0.5").unwrap();
        fs::write(game.join("mods/test.jar"), b"managed-mod").unwrap();
        let raw = create(&app, "demo", Some("first snapshot")).unwrap();
        let backup_id = with_json(raw.as_bytes(), |v| req_str(v, "id")).unwrap();
        fs::write(game.join("saves/level.dat"), b"new-world").unwrap();
        fs::write(game.join("options.txt"), b"music:1.0").unwrap();
        restore(&app, "demo", &backup_id).unwrap();
        assert_eq!(
            fs::read(game.join("saves/level.dat")).unwrap(),
            b"old-world"
        );
        assert_eq!(fs::read(game.join("options.txt")).unwrap(), b"music:0.5");
        assert_eq!(
            fs::read(game.join("mods/test.jar")).unwrap(),
            b"managed-mod"
        );
        let listed = list(&app, "demo").unwrap();
        assert!(listed.contains("\"pre-"));
        assert!(listed.contains("\"data_verified\":false"));
        assert!(
            !app.dir("demo")
                .unwrap()
                .join(".backup-restore.json")
                .exists()
        );
        assert!(app.root.join("cache").read_dir().unwrap().next().is_none());
    }
}
