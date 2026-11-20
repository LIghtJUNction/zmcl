//! On-demand Java world management. Rust owns instance locks and transaction
//! staging; the bounded Python helper reads NBT and streams ZIP/file contents.
use crate::{App, util};
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type Result<T> = std::result::Result<T, String>;

fn token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{}-{nanos}", std::process::id())
}
fn valid_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 240
        || name.starts_with('.')
        || name.contains('/')
        || name.chars().any(char::is_control)
    {
        return Err("World folder name must contain 1–240 UTF-8 bytes and cannot be hidden or contain a path".into());
    }
    zmcl_core::path::validate_relative(name)
}
fn instance(app: &App, id: &str) -> Result<PathBuf> {
    app.read_instance(id)?;
    app.dir(id)
}
fn ready_instance(app: &App, id: &str) -> Result<PathBuf> {
    let dir = instance(app, id)?;
    util::check_pending_transactions(&dir)?;
    Ok(dir)
}
fn saves(instance: &Path) -> Result<PathBuf> {
    util::safe_join(instance, "game/saves")
}
fn world(instance: &Path, name: &str) -> Result<PathBuf> {
    valid_name(name)?;
    util::safe_join(&saves(instance)?, name)
}
fn private_directory(path: &Path) -> Result<()> {
    fs::create_dir(path).map_err(|e| format!("Cannot create private staging directory: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    Ok(())
}
struct Temporary {
    path: PathBuf,
    directory: bool,
}
impl Temporary {
    fn directory(parent: &Path, prefix: &str) -> Result<Self> {
        let path = util::safe_join(parent, &format!("{prefix}{}", token()))?;
        private_directory(&path)?;
        Ok(Self {
            path,
            directory: true,
        })
    }
    fn file(parent: &Path) -> Result<Self> {
        let path = parent.join(format!(".zmcl-world-{}.zip", token()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options
            .open(&path)
            .map_err(|e| format!("Cannot stage world export: {e}"))?;
        Ok(Self {
            path,
            directory: false,
        })
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        if self.directory {
            let _ = fs::remove_dir_all(&self.path);
        } else {
            let _ = fs::remove_file(&self.path);
        }
    }
}
fn capture<R: Read>(mut stream: R, limit: usize) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    let mut overflow = false;
    loop {
        let count = stream.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if output.len().saturating_add(count) <= limit {
            output.extend_from_slice(&buffer[..count]);
        } else {
            overflow = true;
        }
    }
    if overflow {
        Err("World helper output exceeded its memory budget".into())
    } else {
        Ok(output)
    }
}
fn run(app: &App, operation: &str, args: &[OsString]) -> Result<String> {
    let cache = util::safe_join(&app.root, "cache")?;
    fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    let helper = Temporary::directory(&cache, "world-helper-")?;
    let script = helper.path.join("manage.py");
    util::atomic_write(&script, include_bytes!("worlds/manage.py"))?;
    let mut child = util::python_command()?
        .arg(&script)
        .arg(operation)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("World management requires system Python 3: {e}"))?;
    let stdout = child.stdout.take().ok_or("Missing world helper stdout")?;
    let stderr = child.stderr.take().ok_or("Missing world helper stderr")?;
    let output_reader = std::thread::spawn(move || capture(stdout, 8 * 1024 * 1024));
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
        .map_err(|_| "World output reader failed")?;
    let errors = error_reader
        .join()
        .map_err(|_| "World error reader failed")?;
    if timed_out {
        return Err(
            "World operation exceeded 330 seconds; unpublished staging was discarded".into(),
        );
    }
    let status = status?;
    let errors = errors?;
    if !status.success() {
        let message = String::from_utf8_lossy(&errors);
        return Err(if message.trim().is_empty() {
            format!("World helper exited with {status}")
        } else {
            message.trim().to_owned()
        });
    }
    String::from_utf8(output?)
        .map(|v| v.trim().to_owned())
        .map_err(|_| "World helper returned invalid UTF-8".into())
}

/// Checks the recorded client process and existing Minecraft session locks.
/// The caller must hold the instance operation lock; this function does not
/// acquire it, so other mutating modules can use the same guard.
pub fn assert_stopped(app: &App, id: &str) -> Result<()> {
    let dir = instance(app, id)?;
    run(app, "guard", &[dir.into_os_string()]).map(|_| ())
}

/// Lists immediate Java save directories and bounded, read-only level.dat
/// metadata. Corrupt worlds remain visible with valid:false and an error.
pub fn list(app: &App, id: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let dir = ready_instance(app, id)?;
    run(app, "list", &[dir.into_os_string()])
}

/// Returns level.dat metadata, a confined absolute folder path and measured
/// file count/bytes. It does not parse chunks or expose seeds/player positions.
pub fn info(app: &App, id: &str, name: &str) -> Result<String> {
    valid_name(name)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let dir = ready_instance(app, id)?;
    world(&dir, name)?;
    run(app, "info", &[dir.into_os_string(), name.into()])
}

/// A reveal target for the CLI/GUI. The caller can pass its verified "path"
/// field to the platform file manager; this function itself starts no GUI.
pub fn reveal(app: &App, id: &str, name: &str) -> Result<String> {
    info(app, id, name)
}

/// Imports a Java save directory or a ZIP containing exactly one save. Existing
/// worlds are never replaced. Extraction and NBT validation precede publication.
pub fn import(app: &App, id: &str, source: &Path, name: &str) -> Result<String> {
    valid_name(name)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let dir = ready_instance(app, id)?;
    let save_dir = saves(&dir)?;
    let target = world(&dir, name)?;
    if target.exists() {
        return Err("A world with this folder name already exists".into());
    }
    fs::create_dir_all(&save_dir).map_err(|e| e.to_string())?;
    let stage = Temporary::directory(&save_dir, ".zmcl-world-")?;
    run(
        app,
        "import",
        &[
            dir.into_os_string(),
            source.as_os_str().to_owned(),
            name.into(),
            stage.path.as_os_str().to_owned(),
        ],
    )
}

/// Writes a new ZIP with level.dat at its root, excluding only the transient
/// root session.lock. The source must be stopped; no existing output is replaced.
pub fn export(app: &App, id: &str, name: &str, destination: &Path) -> Result<String> {
    valid_name(name)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let dir = ready_instance(app, id)?;
    let source = world(&dir, name)?;
    let absolute = std::path::absolute(destination).map_err(|e| e.to_string())?;
    let parent = absolute
        .parent()
        .ok_or("Export needs a file name")?
        .canonicalize()
        .map_err(|e| format!("Export parent must exist: {e}"))?;
    let file_name = absolute.file_name().ok_or("Export needs a file name")?;
    let target = parent.join(file_name);
    if target.exists() || target.is_symlink() {
        return Err("Export destination already exists".into());
    }
    if source
        .canonicalize()
        .map_err(|e| e.to_string())?
        .starts_with(&target)
    {
        return Err("Export cannot replace the world directory".into());
    }
    if parent.starts_with(source.canonicalize().map_err(|e| e.to_string())?) {
        return Err("Export destination must be outside the source world".into());
    }
    let stage = Temporary::file(&parent)?;
    run(
        app,
        "export",
        &[
            dir.into_os_string(),
            name.into(),
            target.into_os_string(),
            stage.path.as_os_str().to_owned(),
        ],
    )
}

/// Copies world bytes to a fresh folder in the same or another instance. Files
/// are copied, never hard-linked, and both instance locks use a stable order.
pub fn clone(
    app: &App,
    source_id: &str,
    name: &str,
    target_id: &str,
    new_name: &str,
) -> Result<String> {
    valid_name(name)?;
    valid_name(new_name)?;
    util::valid_id(source_id)?;
    util::valid_id(target_id)?;
    let mut ids = vec![source_id, target_id];
    ids.sort_unstable();
    ids.dedup();
    let mut locks = Vec::new();
    for id in ids {
        locks.push(util::Lock::acquire(&app.root, &format!("instance-{id}"))?);
    }
    let source = ready_instance(app, source_id)?;
    world(&source, name)?;
    let target = ready_instance(app, target_id)?;
    let target_world = world(&target, new_name)?;
    if target_world.exists() {
        return Err("A world with this folder name already exists".into());
    }
    let save_dir = saves(&target)?;
    fs::create_dir_all(&save_dir).map_err(|e| e.to_string())?;
    let stage = Temporary::directory(&save_dir, ".zmcl-world-")?;
    run(
        app,
        "clone",
        &[
            source.into_os_string(),
            name.into(),
            target.into_os_string(),
            new_name.into(),
            stage.path.as_os_str().to_owned(),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn world_names_allow_user_titles_but_not_paths() {
        for name in ["My World", "生存世界", "world_2-2026"] {
            assert!(valid_name(name).is_ok(), "{name}");
        }
        for name in [
            "",
            ".",
            "..",
            ".hidden",
            "../world",
            "a/b",
            "a\\b",
            "NUL",
            "C:world",
            "end.",
            "line\nbreak",
        ] {
            assert!(valid_name(name).is_err(), "{name}");
        }
    }
    #[test]
    fn absent_worlds_are_empty_and_guard_accepts_never_launched_instance() {
        let root = std::env::temp_dir().join(format!("zmcl-world-rust-{}", token()));
        fs::create_dir(&root).unwrap();
        let scratch = Temporary {
            path: root.clone(),
            directory: true,
        };
        let app = App { root };
        fs::create_dir(app.root.join("instances")).unwrap();
        app.create_instance("demo", "1.21.1", "vanilla", "")
            .unwrap();
        let lock = util::Lock::acquire(&app.root, "instance-demo").unwrap();
        assert_stopped(&app, "demo").unwrap();
        drop(lock);
        assert_eq!(list(&app, "demo").unwrap(), "[]");
        fs::write(
            app.dir("demo").unwrap().join(".resources-journal.json"),
            b"{}",
        )
        .unwrap();
        assert!(list(&app, "demo").is_err());
        let lock = util::Lock::acquire(&app.root, "instance-demo").unwrap();
        assert_stopped(&app, "demo").unwrap();
        drop(lock);
        drop(scratch);
    }
}
