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

/// Build a noninteractive helper command without a Windows console window.
/// Explicit pipes and log files remain available to both CLI and GUI callers.
pub fn background_command(program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    let command = std::process::Command::new(program);
    #[cfg(windows)]
    let command = {
        use std::os::windows::process::CommandExt;
        let mut command = command;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        command
    };
    command
}

pub fn command_exists(program: &str) -> bool {
    background_command(program)
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// Build a command for an installed Python 3 runtime. All helpers use only its
/// standard library: isolated UTF-8 mode also avoids user site startup work.
/// The configured executable is a literal path, never a shell command.
pub fn python_command() -> Result<std::process::Command> {
    static PYTHON: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    let executable = cached_python(&PYTHON, || {
        find_python(std::env::var_os("ZMCL_PYTHON"), cfg!(windows))
    })?;
    let mut command = python_process(executable.as_os_str());
    command.args(["-I", "-S", "-X", "utf8"]);
    Ok(command)
}

fn cached_python(
    cache: &std::sync::OnceLock<PathBuf>,
    discover: impl FnOnce() -> Result<PathBuf>,
) -> Result<&PathBuf> {
    if let Some(executable) = cache.get() {
        return Ok(executable);
    }
    // A missing runtime can be installed while the launcher remains open.
    // Cache success only, so the next user operation can retry a failed check.
    let executable = discover()?;
    Ok(cache.get_or_init(|| executable))
}

fn python_process(program: &std::ffi::OsStr) -> std::process::Command {
    let mut command = background_command(program);
    // A capability probe must not install a runtime or launch an installer.
    command
        .env("PYTHON_MANAGER_AUTOMATIC_INSTALL", "false")
        .env_remove("PYLAUNCHER_ALLOW_INSTALL")
        .env_remove("PYLAUNCHER_ALWAYS_INSTALL")
        .env_remove("PYLAUNCHER_DRYRUN");
    command
}

fn find_python(override_path: Option<std::ffi::OsString>, windows: bool) -> Result<PathBuf> {
    use std::ffi::OsStr;
    use std::time::Duration;
    let timeout = Duration::from_secs(5);
    if let Some(path) = override_path {
        if path.is_empty() {
            return Err("ZMCL_PYTHON is empty; set it to a Python executable path.".into());
        }
        return probe_python(&path, &[], timeout).map_err(|reason| {
            format!(
                "ZMCL_PYTHON could not run Python 3.8 or newer: {reason}. \
                 Set it to an executable path without arguments."
            )
        });
    }
    let candidates: &[(&str, &[&str])] = if windows {
        &[("py", &["-3"]), ("python", &[]), ("python3", &[])]
    } else {
        &[("python3", &[]), ("python", &[])]
    };
    let mut errors = Vec::new();
    for (program, arguments) in candidates {
        match probe_python(OsStr::new(program), arguments, timeout) {
            Ok(executable) => return Ok(executable),
            Err(error) => errors.push(format!(
                "{}{}: {error}",
                program,
                if arguments.is_empty() { "" } else { " -3" }
            )),
        }
    }
    Err(format!(
        "Python 3.8 or newer is required for this operation. \
         Install Python 3 or set ZMCL_PYTHON to its executable path. Tried {}.",
        errors.join("; ")
    ))
}

fn probe_python(
    program: &std::ffi::OsStr,
    arguments: &[&str],
    timeout: std::time::Duration,
) -> Result<PathBuf> {
    use std::process::Stdio;
    use std::time::{Duration, Instant};
    // Return the interpreter itself, avoiding repeated launcher/shim resolution.
    // stdout is deliberately bounded and has a marker, not version-banner parsing.
    const PROBE: &str = "import sys; \
        sys.exit(3) if sys.version_info[0] != 3 or sys.version_info < (3, 8) else None; \
        sys.stdout.buffer.write(b'zmcl-python-v1\\0' + sys.executable.encode('utf-8'))";
    const MAX_OUTPUT: u64 = 16 * 1024;
    let mut child = python_process(program)
        .args(arguments)
        .args(["-I", "-S", "-X", "utf8", "-c", PROBE])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                "not found".into()
            } else {
                format!("could not start ({})", error.kind())
            }
        })?;
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() < timeout => {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("runtime check timed out".into());
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("could not check runtime ({})", error.kind()));
            }
        }
    };
    if !status.success() {
        return Err(if status.code() == Some(3) {
            "Python 3.8 or newer is required".into()
        } else {
            "runtime check failed".into()
        });
    }
    let mut output = Vec::new();
    child
        .stdout
        .take()
        .ok_or("runtime check returned no output")?
        .take(MAX_OUTPUT + 1)
        .read_to_end(&mut output)
        .map_err(|_| "could not read runtime check")?;
    if output.len() as u64 > MAX_OUTPUT {
        return Err("runtime check output exceeded its limit".into());
    }
    let executable = output
        .strip_prefix(b"zmcl-python-v1\0")
        .and_then(|bytes| std::str::from_utf8(bytes).ok())
        .filter(|path| !path.is_empty() && !path.contains('\0'))
        .ok_or("runtime check returned an invalid executable path")?;
    let executable = PathBuf::from(executable);
    if !executable.is_absolute() || !executable.is_file() {
        return Err("runtime check did not identify an installed executable".into());
    }
    Ok(executable)
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
    fn python_runtime_cache_retries_failure_and_reuses_success() {
        let cache = std::sync::OnceLock::new();
        assert!(cached_python(&cache, || Err("not installed".into())).is_err());
        assert!(cache.get().is_none());
        let installed = PathBuf::from("installed-python");
        assert_eq!(
            cached_python(&cache, || Ok(installed.clone())).unwrap(),
            &installed
        );
        assert_eq!(
            cached_python(&cache, || panic!("successful discovery should be reused")).unwrap(),
            &installed
        );
    }

    #[test]
    fn python_helper_is_isolated_utf8_and_preserves_literal_arguments() {
        let argument = "spaces 'quotes' \"$dollars\"; $(not-a-command) 中文 😀\nline";
        let output = python_command()
            .expect("Python 3 is required by the launcher helper tests")
            .args([
                "-c",
                "import sys; assert sys.flags.isolated and sys.flags.no_site and \
                 sys.flags.utf8_mode; sys.stdout.buffer.write(sys.argv[1].encode('utf-8'))",
                argument,
            ])
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(output.stdout, argument.as_bytes());
        assert!(output.stderr.is_empty());
    }

    #[test]
    fn python_override_is_an_executable_not_a_shell_command() {
        let dir = std::env::temp_dir().join(format!(
            "zmcl-python-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&dir).unwrap();
        let marker = dir.join("must-not-exist");
        let configured = format!("python3; touch {}", marker.display());
        let error = find_python(Some(configured.into()), cfg!(windows)).unwrap_err();
        assert!(error.contains("ZMCL_PYTHON"));
        assert!(!marker.exists());
        assert!(find_python(Some("".into()), cfg!(windows)).is_err());
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn python_probe_rejects_failed_runtime_and_has_a_deadline() {
        let executable = python_command().unwrap().get_program().to_os_string();
        let failed = probe_python(
            &executable,
            &["-c", "import sys; sys.exit(3)"],
            std::time::Duration::from_secs(2),
        )
        .unwrap_err();
        assert!(failed.contains("Python 3.8"));
        let start = std::time::Instant::now();
        let error = probe_python(
            &executable,
            &["-c", "import time; time.sleep(30)"],
            std::time::Duration::from_millis(50),
        )
        .unwrap_err();
        assert!(error.contains("timed out"));
        assert!(start.elapsed() < std::time::Duration::from_secs(5));
    }

    #[cfg(unix)]
    #[test]
    fn python_override_accepts_unicode_and_spaces_in_executable_paths() {
        let dir = std::env::temp_dir().join(format!(
            "zmcl-python-path-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&dir).unwrap();
        let alias = dir.join("Python runtime 中文");
        std::os::unix::fs::symlink(python_command().unwrap().get_program(), &alias).unwrap();
        let executable = find_python(Some(alias.into_os_string()), false).unwrap();
        let output = python_process(executable.as_os_str())
            .args(["-I", "-S", "-X", "utf8", "-c", "print('运行成功')"])
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(String::from_utf8(output.stdout).unwrap().trim(), "运行成功");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn python_probe_returns_the_actual_interpreter() {
        let command = python_command().unwrap();
        let executable = command.get_program();
        let probed = probe_python(executable, &[], std::time::Duration::from_secs(5)).unwrap();
        assert_eq!(probed, PathBuf::from(executable));
        assert!(probed.is_absolute());
    }

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
