use super::*;
use std::io::Write;

const MARKER: &str = ".zmcl-content-transaction.json";
const JOURNAL: &str = ".content-journal.json";

fn file_hash(path: &Path) -> Result<Option<String>> {
    if !path.exists() {
        return Ok(None);
    }
    Ok(Some(zmcl_core::hash::sha256_hex(&util::read_limited(
        path, MAX_LOCK,
    )?)))
}
fn marker_body(token: &str, role: &str, hash: &str) -> String {
    util::object(&[
        ("format", "1".into()),
        ("token", quote(token)),
        ("role", quote(role)),
        ("lock_sha256", quote(hash)),
    ])
}
fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(|e| e.to_string())?;
    file.write_all(bytes)
        .and_then(|_| file.sync_all())
        .map_err(|e| e.to_string())
}
fn marker(root: &Path, token: &str, role: &str, hash: &str, required: bool) -> Result<bool> {
    let path = util::safe_join(root, MARKER)?;
    if !path.exists() {
        if required {
            return Err(
                "Recovery directory identity marker is missing; all files were retained".into(),
            );
        }
        return Ok(false);
    }
    with_json(&util::read_limited(&path, 4096)?, |value| {
        if value.get("format").and_then(Value::as_u64) != Some(1)
            || get_str(value, "token").as_deref() != Some(token)
            || get_str(value, "role").as_deref() != Some(role)
            || get_str(value, "lock_sha256").as_deref() != Some(hash)
        {
            return Err(
                "Recovery directory identity differs from its journal; all files were retained"
                    .into(),
            );
        }
        Ok(true)
    })
}
#[derive(Debug)]
struct Transaction {
    stage: PathBuf,
    token: String,
    had_mods: bool,
    had_lock: bool,
    old_hash: Option<String>,
    new_hash: String,
}
fn transaction(instance: &Path, bytes: &[u8]) -> Result<Transaction> {
    with_json(bytes, |value| {
        if value.get("format").and_then(Value::as_u64) != Some(2) {
            return Err("This legacy content journal has no safe directory identities. Preserve its stage and current mods for manual recovery; no files were changed.".into());
        }
        let name = req_str(value, "stage")?;
        let suffix = name
            .strip_prefix(".content-stage-")
            .ok_or("Invalid content recovery stage")?;
        if suffix.is_empty()
            || suffix.len() > 100
            || !suffix.bytes().all(|c| c.is_ascii_digit() || c == b'-')
        {
            return Err("Invalid content recovery stage name".into());
        }
        let token = req_str(value, "token")?;
        if token.is_empty()
            || token.len() > 128
            || !token.bytes().all(|c| c.is_ascii_digit() || c == b'-')
        {
            return Err("Invalid content transaction identity".into());
        }
        let had_mods = value
            .get("had_mods")
            .and_then(Value::as_bool)
            .ok_or("Missing original Mod state")?;
        let had_lock = value
            .get("had_lock")
            .and_then(Value::as_bool)
            .ok_or("Missing original lock state")?;
        let old_hash = get_str(value, "old_lock_sha256")
            .map(|v| digest(&v, 64))
            .transpose()?;
        if had_lock != old_hash.is_some() {
            return Err("Content journal lock identity is inconsistent".into());
        }
        Ok(Transaction {
            stage: util::safe_join(instance, &name)?,
            token,
            had_mods,
            had_lock,
            old_hash,
            new_hash: digest(&req_str(value, "new_lock_sha256")?, 64)?,
        })
    })
}
fn cleanup_completed_marker(instance: &Path) -> Result<()> {
    let mods = util::safe_join(instance, "game/mods")?;
    let path = util::safe_join(&mods, MARKER)?;
    if !path.exists() {
        return Ok(());
    }
    let expected = with_json(&util::read_limited(&path, 4096)?, |value| {
        if value.get("format").and_then(Value::as_u64) != Some(1)
            || get_str(value, "role").as_deref() != Some("new")
        {
            return Err(
                "Unmatched content transaction marker; preserve the Mod directory for inspection"
                    .into(),
            );
        }
        digest(&req_str(value, "lock_sha256")?, 64)
    })?;
    if file_hash(&util::safe_join(instance, "mods-lock.json")?)?.as_deref() != Some(&expected) {
        return Err(
            "Completed content marker does not match the current lock; preserve all files".into(),
        );
    }
    fs::remove_file(path).map_err(|e| e.to_string())
}
/// Roll back only journal-identified directories. New staged data is retained,
/// including any manual additions made after a failed operation.
fn rollback(instance: &Path) -> Result<Option<PathBuf>> {
    let journal = util::safe_join(instance, JOURNAL)?;
    if !journal.exists() {
        cleanup_completed_marker(instance)?;
        return Ok(None);
    }
    let bytes = util::read_limited(&journal, 65536)?;
    let tx = transaction(instance, &bytes)?;
    if !tx.stage.is_dir() {
        return Err("Content recovery stage is missing; no files were changed".into());
    }
    let target = util::safe_join(instance, "game/mods")?;
    let staged = util::safe_join(&tx.stage, "mods")?;
    let previous = util::safe_join(&tx.stage, "previous-mods")?;
    let lock = util::safe_join(instance, "mods-lock.json")?;
    let current = file_hash(&lock)?;
    if current != tx.old_hash && current.as_deref() != Some(&tx.new_hash) {
        return Err("Content lock changed outside its transaction; preserve the journal and both Mod directories".into());
    }
    let old_bytes = if tx.had_lock {
        let path = util::safe_join(&tx.stage, "previous-lock.json")?;
        let old = util::read_limited(&path, MAX_LOCK)?;
        if Some(zmcl_core::hash::sha256_hex(&old)) != tx.old_hash {
            return Err(
                "Previous content lock failed recovery checksum; all files were retained".into(),
            );
        }
        Some(old)
    } else {
        None
    };
    let old_hash = tx.old_hash.as_deref().unwrap_or("");
    if tx.had_mods {
        if previous.exists() {
            marker(&previous, &tx.token, "old", old_hash, true)?;
            if target.exists() {
                marker(&target, &tx.token, "new", &tx.new_hash, true)?;
                if staged.exists() {
                    return Err("Ambiguous content recovery: both new Mod locations exist".into());
                }
                fs::rename(&target, &staged).map_err(|e| e.to_string())?;
            } else {
                marker(&staged, &tx.token, "new", &tx.new_hash, true)?;
            }
            fs::rename(&previous, &target).map_err(|e| e.to_string())?;
        } else {
            if !target.is_dir() || !staged.is_dir() {
                return Err(
                    "Original Mod directory cannot be identified safely; all files were retained"
                        .into(),
                );
            }
            marker(&staged, &tx.token, "new", &tx.new_hash, true)?;
            let identified = marker(&target, &tx.token, "old", old_hash, false)?;
            if !identified && current != tx.old_hash {
                return Err("Unmarked original Mods do not match the old lock state".into());
            }
        }
    } else {
        if previous.exists() {
            return Err("Unexpected previous Mod directory in recovery stage".into());
        }
        if target.exists() {
            marker(&target, &tx.token, "new", &tx.new_hash, true)?;
            if staged.exists() {
                return Err("Ambiguous content recovery: both new Mod locations exist".into());
            }
            fs::rename(&target, &staged).map_err(|e| e.to_string())?;
        } else {
            marker(&staged, &tx.token, "new", &tx.new_hash, true)?;
        }
    }
    if let Some(old) = old_bytes {
        util::atomic_write(&lock, &old)?;
    } else if lock.exists() {
        fs::remove_file(&lock).map_err(|e| e.to_string())?;
    }
    if tx.had_mods {
        let old_marker = util::safe_join(&target, MARKER)?;
        if old_marker.exists() {
            marker(&target, &tx.token, "old", old_hash, true)?;
            fs::remove_file(old_marker).map_err(|e| e.to_string())?;
        }
    }
    let archive = util::safe_join(&tx.stage, "recovered-journal.json")?;
    if archive.exists() {
        if util::read_limited(&archive, 65536)? != bytes {
            return Err("Recovery record path already contains other data".into());
        }
    } else {
        write_new(&archive, &bytes)?;
    }
    fs::remove_file(&journal).map_err(|e| e.to_string())?;
    Ok(Some(tx.stage))
}
pub(super) fn recover_locked(app: &App, id: &str) -> Result<()> {
    let dir = app.dir(id)?;
    let journal = util::safe_join(&dir, JOURNAL)?;
    let old_marker = util::safe_join(&dir, &format!("game/mods/{MARKER}"))?;
    if journal.exists() || old_marker.exists() {
        worlds::assert_stopped(app, id)?;
        if let Some(stage) = rollback(&dir)? {
            eprintln!(
                "Recovered Mod transaction; staged files retained at {}",
                stage.display()
            );
        }
    }
    Ok(())
}
pub fn recover(app: &App, id: &str) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    app.read_instance(id)?;
    recover_locked(app, id)
}
fn prepare(instance: &Path, temp: &mut Temp, entries: &[Entry]) -> Result<()> {
    let target = util::safe_join(instance, "game/mods")?;
    let staged = util::safe_join(&temp.path, "mods")?;
    let lock = util::safe_join(instance, "mods-lock.json")?;
    let previous_lock = if lock.exists() {
        Some(util::read_limited(&lock, MAX_LOCK)?)
    } else {
        None
    };
    let old_hash = previous_lock
        .as_ref()
        .map(|bytes| zmcl_core::hash::sha256_hex(bytes));
    let new_hash = zmcl_core::hash::sha256_hex(lock_json(entries).as_bytes());
    let token = format!(
        "{}-{}-{}",
        std::process::id(),
        util::now(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    );
    let marker_new = util::safe_join(&staged, MARKER)?;
    let marker_old = util::safe_join(&target, MARKER)?;
    if marker_new.exists() || marker_old.exists() {
        return Err("Reserved content transaction marker already exists".into());
    }
    if let Some(bytes) = &previous_lock {
        util::atomic_write(&temp.path.join("previous-lock.json"), bytes)?;
    }
    write_new(
        &marker_new,
        marker_body(&token, "new", &new_hash).as_bytes(),
    )?;
    let journal = util::safe_join(instance, JOURNAL)?;
    if journal.exists() {
        return Err("A content transaction is already pending".into());
    }
    util::atomic_write(
        &journal,
        util::object(&[
            ("format", "2".into()),
            (
                "stage",
                quote(
                    &temp
                        .path
                        .file_name()
                        .ok_or("Missing stage name")?
                        .to_string_lossy(),
                ),
            ),
            ("token", quote(&token)),
            ("had_mods", target.exists().to_string()),
            ("had_lock", previous_lock.is_some().to_string()),
            (
                "old_lock_sha256",
                old_hash
                    .as_ref()
                    .map(|hash| quote(hash))
                    .unwrap_or_else(|| "null".into()),
            ),
            ("new_lock_sha256", quote(&new_hash)),
        ])
        .as_bytes(),
    )?;
    temp.retain = true;
    if target.exists() {
        write_new(
            &marker_old,
            marker_body(&token, "old", old_hash.as_deref().unwrap_or("")).as_bytes(),
        )?;
    }
    Ok(())
}
pub(super) fn commit(instance: &Path, temp: &mut Temp, entries: &[Entry]) -> Result<()> {
    let target = util::safe_join(instance, "game/mods")?;
    let previous = temp.path.join("previous-mods");
    let journal = util::safe_join(instance, JOURNAL)?;
    let result = (|| {
        prepare(instance, temp, entries)?;
        if target.exists() {
            fs::rename(&target, &previous).map_err(|e| e.to_string())?;
        }
        fs::rename(temp.path.join("mods"), &target).map_err(|e| e.to_string())?;
        util::atomic_write(
            &util::safe_join(instance, "mods-lock.json")?,
            lock_json(entries).as_bytes(),
        )
    })();
    if let Err(error) = result {
        if !journal.exists() {
            return Err(error);
        }
        let recovered = rollback(instance);
        temp.retain = true;
        return Err(match recovered {
            Ok(Some(stage)) => format!(
                "Content transaction failed: {error}; previous mods restored, staged files retained at {}",
                stage.display()
            ),
            Ok(None) => format!("Content transaction failed: {error}; no pending transaction"),
            Err(recovery) => {
                format!("Content transaction failed: {error}; recovery stopped safely: {recovery}")
            }
        });
    }
    fs::remove_file(&journal)
        .map_err(|e| format!("Content committed; journal cleanup failed: {e}"))?;
    cleanup_completed_marker(instance)?;
    temp.retain = false;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Temp, App, PathBuf, Temp) {
        let outer = Temp::new(&std::env::temp_dir()).unwrap();
        let app = App::new(Some(outer.path.join("app").to_string_lossy().into_owned())).unwrap();
        app.create_instance("recovery", "1.20.1", "fabric", "0.16.0")
            .unwrap();
        let dir = app.dir("recovery").unwrap();
        util::atomic_write(&dir.join("game/mods/manual.jar"), b"old manual").unwrap();
        util::atomic_write(&dir.join("mods-lock.json"), lock_json(&[]).as_bytes()).unwrap();
        let mut stage = Temp::new(&dir).unwrap();
        fs::create_dir(stage.path.join("mods")).unwrap();
        util::atomic_write(&stage.path.join("mods/new.jar"), b"new bytes").unwrap();
        prepare(&dir, &mut stage, &[]).unwrap();
        (outer, app, dir, stage)
    }
    #[test]
    fn rollback_after_old_move_preserves_all_staged_data() {
        let (_outer, app, dir, stage) = fixture();
        fs::rename(dir.join("game/mods"), stage.path.join("previous-mods")).unwrap();
        recover(&app, "recovery").unwrap();
        assert_eq!(
            fs::read(dir.join("game/mods/manual.jar")).unwrap(),
            b"old manual"
        );
        assert_eq!(
            fs::read(stage.path.join("mods/new.jar")).unwrap(),
            b"new bytes"
        );
        assert!(!dir.join(JOURNAL).exists());
        assert!(stage.path.join("recovered-journal.json").exists());
    }
    #[test]
    fn rollback_after_new_move_retains_later_manual_additions() {
        let (_outer, app, dir, stage) = fixture();
        fs::rename(dir.join("game/mods"), stage.path.join("previous-mods")).unwrap();
        fs::rename(stage.path.join("mods"), dir.join("game/mods")).unwrap();
        util::atomic_write(&dir.join("game/mods/user-added.jar"), b"preserve this too").unwrap();
        recover(&app, "recovery").unwrap();
        assert_eq!(
            fs::read(dir.join("game/mods/manual.jar")).unwrap(),
            b"old manual"
        );
        assert_eq!(
            fs::read(stage.path.join("mods/user-added.jar")).unwrap(),
            b"preserve this too"
        );
        assert!(!dir.join("game/mods").join(MARKER).exists());
    }
    #[test]
    fn unknown_identity_and_legacy_journals_fail_without_deletion() {
        let (_outer, app, dir, stage) = fixture();
        fs::rename(dir.join("game/mods"), stage.path.join("previous-mods")).unwrap();
        fs::write(
            stage.path.join("previous-mods").join(MARKER),
            b"foreign identity",
        )
        .unwrap();
        assert!(recover(&app, "recovery").is_err());
        assert_eq!(
            fs::read(stage.path.join("previous-mods/manual.jar")).unwrap(),
            b"old manual"
        );
        assert!(dir.join(JOURNAL).exists());
        util::atomic_write(
            &dir.join(JOURNAL),
            br#"{"stage":".content-stage-1-2-3","had_mods":true,"had_lock":true}"#,
        )
        .unwrap();
        assert!(recover(&app, "recovery").unwrap_err().contains("legacy"));
        assert!(stage.path.join("mods/new.jar").exists());
    }
}
