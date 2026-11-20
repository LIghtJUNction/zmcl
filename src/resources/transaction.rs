//! Per-file transactions: manual directories/files are never swapped wholesale.
//! Old archives and both lock snapshots survive successful updates/removals.
use super::*;
use std::fs::{File, Metadata};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);
const HISTORY: &str = "resources-history";

#[derive(Clone)]
struct Change {
    before: Option<Entry>,
    after: Option<Entry>,
}
fn changes(before: &[Entry], after: &[Entry]) -> Vec<Change> {
    let old: BTreeMap<_, _> = before.iter().map(|entry| (entry.key(), entry)).collect();
    let new: BTreeMap<_, _> = after.iter().map(|entry| (entry.key(), entry)).collect();
    let keys: BTreeSet<_> = old.keys().chain(new.keys()).cloned().collect();
    keys.into_iter()
        .filter_map(|key| {
            let before = old.get(&key).copied();
            let after = new.get(&key).copied();
            if before == after {
                None
            } else {
                Some(Change {
                    before: before.cloned(),
                    after: after.cloned(),
                })
            }
        })
        .collect()
}
fn directory_sync(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        File::open(path)
            .and_then(|file| file.sync_all())
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
fn metadata(path: &Path) -> Result<Option<Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(meta) => Ok(Some(meta)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}
fn private_dir(path: &Path) -> Result<()> {
    fs::create_dir(path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    Ok(())
}
struct Stage {
    id: String,
    path: PathBuf,
    retain: bool,
}
impl Stage {
    fn new(dir: &Path) -> Result<Self> {
        let root = util::safe_join(dir, HISTORY)?;
        fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        for _ in 0..64 {
            let id = format!(
                "r{}-{}-{}",
                util::now(),
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            );
            let path = util::safe_join(&root, &id)?;
            if metadata(&path)?.is_some() {
                continue;
            }
            private_dir(&path)?;
            return Ok(Self {
                id,
                path,
                retain: false,
            });
        }
        Err("Cannot allocate a unique resource transaction".into())
    }
}
impl Drop for Stage {
    fn drop(&mut self) {
        if !self.retain {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}
fn state(stage: &Path, had_lock: bool, value: &str) -> Result<()> {
    util::atomic_write(
        &stage.join("transaction.json"),
        util::object(&[
            ("format", "1".into()),
            ("had_lock", had_lock.to_string()),
            ("state", quote(value)),
            ("created", util::now().to_string()),
        ])
        .as_bytes(),
    )?;
    directory_sync(stage)
}
fn cleanup_new(stage: &Path, operations: &[Change]) {
    for (index, change) in operations.iter().enumerate() {
        if change.after.is_some() {
            let _ = fs::remove_file(stage.join(format!("new-{index}.zip")));
        }
    }
}
fn compare_lock(dir: &Path, expected: Option<&[u8]>) -> Result<bool> {
    let path = util::safe_join(dir, LOCK)?;
    match (metadata(&path)?, expected) {
        (None, None) => Ok(true),
        (Some(meta), Some(bytes)) if meta.file_type().is_file() => {
            Ok(util::read_limited(&path, MAX_LOCK)? == bytes)
        }
        _ => Ok(false),
    }
}
fn preflight(
    dir: &Path,
    before: &[Entry],
    old_lock: Option<&[u8]>,
    after: &[Entry],
    operations: &[Change],
) -> Result<()> {
    let serialized = lock_json(after);
    if serialized.len() > MAX_LOCK {
        return Err("Resources lock exceeds 4 MiB".into());
    }
    parse_lock(serialized.as_bytes())?;
    if !compare_lock(dir, old_lock)? {
        return Err("Resource lock changed while planning the operation".into());
    }
    let game = game_dir(dir)?;
    let mut removed = BTreeSet::new();
    for change in operations {
        if let Some(entry) = &change.before {
            verify_entry(&game, entry)?;
            removed.insert(entry.actual_path());
        }
    }
    let mut existing: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut count = 0;
    for kind in [Kind::ResourcePack, Kind::Shader] {
        let folder = util::safe_join(&game, kind.folder())?;
        if !folder.exists() {
            continue;
        }
        for item in fs::read_dir(&folder).map_err(|e| e.to_string())? {
            count += 1;
            if count > 10000 {
                return Err("Resource folders exceed the transaction entry budget".into());
            }
            let item = item.map_err(|e| e.to_string())?;
            let name = item
                .file_name()
                .into_string()
                .map_err(|_| "Non-UTF-8 resource filename cannot be compared safely")?;
            let path = format!("{}/{name}", kind.folder());
            existing.entry(path.to_lowercase()).or_default().push(path);
        }
    }
    for change in operations {
        if let Some(entry) = &change.after {
            let path = entry.actual_path();
            if let Some(collisions) = existing.get(&path.to_lowercase()) {
                for collision in collisions {
                    if !removed.contains(collision) {
                        return Err(format!(
                            "Resource filename conflicts with an existing file; nothing was replaced: {collision}"
                        ));
                    }
                }
            }
        }
    }
    // Retained managed files are present in the new lock without being moved.
    for entry in before {
        if !removed.contains(&entry.actual_path())
            && !after.iter().any(|candidate| candidate == entry)
        {
            return Err("Transaction would discard an unplanned resource".into());
        }
    }
    Ok(())
}

pub(super) fn commit(
    dir: &Path,
    before: &[Entry],
    old_lock: Option<&[u8]>,
    after: &[Entry],
    sources: &BTreeMap<(Kind, String), PathBuf>,
) -> Result<String> {
    if util::safe_join(dir, JOURNAL)?.exists() {
        return Err("An interrupted resource transaction must be recovered first".into());
    }
    let operations = changes(before, after);
    if operations.is_empty() {
        return Err("Empty resource transaction".into());
    }
    preflight(dir, before, old_lock, after, &operations)?;
    let game = game_dir(dir)?;
    let mut stage = Stage::new(dir)?;
    let before_bytes = old_lock.map_or_else(|| lock_json(before).into_bytes(), <[u8]>::to_vec);
    let after_bytes = lock_json(after).into_bytes();
    util::atomic_write(&stage.path.join("before-lock.json"), &before_bytes)?;
    util::atomic_write(&stage.path.join("after-lock.json"), &after_bytes)?;
    for (index, change) in operations.iter().enumerate() {
        if let Some(entry) = &change.after {
            let source = sources
                .get(&entry.key())
                .ok_or("Missing staged resource source")?;
            if !file_matches(source, entry)? {
                return Err("Staged resource source failed its expected hash or size".into());
            }
            let target = stage.path.join(format!("new-{index}.zip"));
            // Active user-facing archives never share a writable inode with the
            // shared download cache or a previous revision.
            store::materialize(source, &target, false)?;
            if !file_matches(&target, entry)? {
                return Err("Staged resource copy failed integrity verification".into());
            }
            File::open(&target)
                .and_then(|file| file.sync_all())
                .map_err(|e| e.to_string())?;
        }
    }
    state(&stage.path, old_lock.is_some(), "prepared")?;
    preflight(dir, before, old_lock, after, &operations)?;
    let journal = util::safe_join(dir, JOURNAL)?;
    util::atomic_write(
        &journal,
        util::object(&[("format", "1".into()), ("id", quote(&stage.id))]).as_bytes(),
    )?;
    stage.retain = true;
    directory_sync(dir)?;
    let applied = (|| {
        for (index, change) in operations.iter().enumerate() {
            if let Some(entry) = &change.before {
                let source = verify_entry(&game, entry)?;
                let previous = stage.path.join(format!("old-{index}.zip"));
                if metadata(&previous)?.is_some() {
                    return Err("Recovery slot unexpectedly exists".into());
                }
                fs::rename(&source, &previous).map_err(|e| e.to_string())?;
                if !file_matches(&previous, entry)? {
                    return Err("Resource changed while moving it into recovery storage".into());
                }
                File::open(&previous)
                    .and_then(|file| file.sync_all())
                    .map_err(|e| e.to_string())?;
                directory_sync(source.parent().ok_or("Missing resource parent")?)?;
                directory_sync(&stage.path)?;
            }
        }
        for (index, change) in operations.iter().enumerate() {
            if let Some(entry) = &change.after {
                let target = util::safe_join(&game, &entry.actual_path())?;
                fs::create_dir_all(target.parent().ok_or("Missing resource parent")?)
                    .map_err(|e| e.to_string())?;
                let source = stage.path.join(format!("new-{index}.zip"));
                // Exclusive and atomic: unlike rename, hard_link cannot replace
                // a manual file that appears after preflight. Staging is local.
                fs::hard_link(&source, &target).map_err(|e| {
                    format!("Cannot publish resource without replacing a file: {e}")
                })?;
                directory_sync(target.parent().ok_or("Missing resource parent")?)?;
            }
        }
        util::atomic_write(&util::safe_join(dir, LOCK)?, &after_bytes)?;
        directory_sync(dir)?;
        state(&stage.path, old_lock.is_some(), "committed")?;
        fs::remove_file(&journal).map_err(|e| e.to_string())?;
        directory_sync(dir)?;
        Ok::<_, String>(())
    })();
    if let Err(error) = applied {
        let recovered = recover(dir);
        return Err(format!(
            "Resource transaction failed: {error}; recovery {}; retained data: {}",
            match recovered {
                Ok(value) => value,
                Err(reason) => format!("requires attention: {reason}"),
            },
            stage.path.display()
        ));
    }
    cleanup_new(&stage.path, &operations);
    Ok(stage.id.clone())
}

fn history_path(dir: &Path, id: &str) -> Result<PathBuf> {
    if !id.starts_with('r')
        || id.len() > 120
        || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        return Err("Invalid resource history identifier".into());
    }
    let path = util::safe_join(dir, &format!("{HISTORY}/{id}"))?;
    if !metadata(&path)?.is_some_and(|meta| meta.is_dir()) {
        return Err("Resource recovery directory is missing".into());
    }
    Ok(path)
}
fn read_state(stage: &Path) -> Result<(bool, String)> {
    with_json(
        &util::read_limited(&util::safe_join(stage, "transaction.json")?, 4096)?,
        |v| {
            if v.get("format").and_then(Value::as_u64) != Some(1) {
                return Err("Unsupported resource transaction format".into());
            }
            let had_lock = v
                .get("had_lock")
                .and_then(Value::as_bool)
                .ok_or("Missing previous lock state")?;
            let state = req_str(v, "state")?;
            if !matches!(state.as_str(), "prepared" | "committed" | "rolled_back") {
                return Err("Invalid resource transaction state".into());
            }
            Ok((had_lock, state))
        },
    )
}
fn snapshot(stage: &Path, name: &str) -> Result<(Vec<Entry>, Vec<u8>)> {
    let bytes = util::read_limited(&util::safe_join(stage, name)?, MAX_LOCK)?;
    Ok((parse_lock(&bytes)?, bytes))
}
fn finish(dir: &Path, stage: &Path, operations: &[Change]) -> Result<()> {
    fs::remove_file(util::safe_join(dir, JOURNAL)?).map_err(|e| e.to_string())?;
    directory_sync(dir)?;
    cleanup_new(stage, operations);
    Ok(())
}
pub(super) fn recover(dir: &Path) -> Result<String> {
    let journal = util::safe_join(dir, JOURNAL)?;
    if !journal.exists() {
        return Ok("{\"recovered\":false}".into());
    }
    let id = with_json(&util::read_limited(&journal, 4096)?, |v| {
        if v.get("format").and_then(Value::as_u64) != Some(1) {
            return Err("Unsupported resource recovery journal".into());
        }
        req_str(v, "id")
    })?;
    let stage = history_path(dir, &id)?;
    let (had_lock, state_value) = read_state(&stage)?;
    let (before, old_bytes) = snapshot(&stage, "before-lock.json")?;
    let (after, new_bytes) = snapshot(&stage, "after-lock.json")?;
    if !had_lock && !before.is_empty() {
        return Err("Resource transaction has inconsistent previous state".into());
    }
    let operations = changes(&before, &after);
    let game = game_dir(dir)?;
    if state_value == "committed" {
        if !compare_lock(dir, Some(&new_bytes))? {
            return Err("Committed resource lock changed; keeping every recovery file".into());
        }
        for change in &operations {
            if let Some(entry) = &change.after {
                verify_entry(&game, entry)?;
            }
        }
        finish(dir, &stage, &operations)?;
        return Ok(util::object(&[
            ("recovered", "true".into()),
            ("state", quote("committed")),
            ("history", quote(&id)),
        ]));
    }
    let expected_old = had_lock.then_some(old_bytes.as_slice());
    if !compare_lock(dir, expected_old)? && !compare_lock(dir, Some(&new_bytes))? {
        return Err(
            "Resource lock was changed outside the transaction; keeping every recovery file".into(),
        );
    }
    if state_value == "rolled_back" {
        if !compare_lock(dir, expected_old)? {
            return Err("Rolled-back resource lock no longer matches its snapshot".into());
        }
        for change in &operations {
            if let Some(entry) = &change.before {
                verify_entry(&game, entry)?;
            }
        }
        finish(dir, &stage, &operations)?;
        return Ok(util::object(&[
            ("recovered", "true".into()),
            ("state", quote("rolled_back")),
            ("history", quote(&id)),
        ]));
    }
    for (index, change) in operations.iter().enumerate().rev() {
        let Some(entry) = &change.after else {
            continue;
        };
        let target = util::safe_join(&game, &entry.actual_path())?;
        if metadata(&target)?.is_none() {
            continue;
        }
        if let Some(previous) = &change.before
            && previous.actual_path() == entry.actual_path()
            && file_matches(&target, previous)?
        {
            let original = util::safe_join(&stage, &format!("old-{index}.zip"))?;
            if metadata(&original)?.is_none() || same_owned_file(&target, &original)? {
                continue; // Never moved, or already restored by an earlier recovery attempt.
            }
        }
        let staged = util::safe_join(&stage, &format!("new-{index}.zip"))?;
        if !file_matches(&target, entry)? || !same_owned_file(&target, &staged)? {
            return Err(format!(
                "Recovery found an ambiguous or manually changed file: {}; it was preserved",
                target.display()
            ));
        }
        fs::remove_file(&target).map_err(|e| e.to_string())?;
        directory_sync(target.parent().ok_or("Missing resource parent")?)?;
    }
    for (index, change) in operations.iter().enumerate() {
        let Some(entry) = &change.before else {
            continue;
        };
        let target = util::safe_join(&game, &entry.actual_path())?;
        let previous = util::safe_join(&stage, &format!("old-{index}.zip"))?;
        if metadata(&previous)?.is_some() {
            if !file_matches(&previous, entry)? {
                return Err(
                    "Retained original pack changed; preserving it for manual recovery".into(),
                );
            }
            if metadata(&target)?.is_some() {
                if !file_matches(&target, entry)? {
                    return Err(
                        "Original resource path is occupied; recovery will not overwrite it".into(),
                    );
                }
            } else {
                fs::create_dir_all(target.parent().ok_or("Missing resource parent")?)
                    .map_err(|e| e.to_string())?;
                fs::hard_link(&previous, &target).map_err(|e| {
                    format!("Cannot restore original without overwriting a file: {e}")
                })?;
                directory_sync(target.parent().ok_or("Missing resource parent")?)?;
            }
        } else {
            verify_entry(&game, entry)?;
        }
    }
    if let Some(bytes) = expected_old {
        util::atomic_write(&util::safe_join(dir, LOCK)?, bytes)?;
    } else if metadata(&util::safe_join(dir, LOCK)?)?.is_some() {
        fs::remove_file(util::safe_join(dir, LOCK)?).map_err(|e| e.to_string())?;
    }
    directory_sync(dir)?;
    state(&stage, had_lock, "rolled_back")?;
    finish(dir, &stage, &operations)?;
    Ok(util::object(&[
        ("recovered", "true".into()),
        ("state", quote("rolled_back")),
        ("history", quote(&id)),
    ]))
}

/// Restore a retained revision only when the complete current managed lock
/// still equals that transaction's result; later changes are never discarded.
pub(super) fn restore(dir: &Path, id: &str) -> Result<(String, Vec<Entry>)> {
    let stage = history_path(dir, id)?;
    let (_, state_value) = read_state(&stage)?;
    if state_value != "committed" {
        return Err("Only a completed resource transaction can be restored".into());
    }
    let (before, _) = snapshot(&stage, "before-lock.json")?;
    let (after, new_bytes) = snapshot(&stage, "after-lock.json")?;
    if !compare_lock(dir, Some(&new_bytes))? {
        return Err("Managed resources changed after this revision; restore the latest matching revision first".into());
    }
    let operations = changes(&before, &after);
    let mut sources = BTreeMap::new();
    for (index, change) in operations.iter().enumerate() {
        if let Some(entry) = &change.before {
            let path = util::safe_join(&stage, &format!("old-{index}.zip"))?;
            if !file_matches(&path, entry)? {
                return Err("Retained pack failed integrity verification".into());
            }
            sources.insert(entry.key(), path);
        }
    }
    let id = commit(dir, &after, Some(&new_bytes), &before, &sources)?;
    Ok((id, before))
}
pub(super) fn history(dir: &Path) -> Result<String> {
    let path = util::safe_join(dir, HISTORY)?;
    if !path.exists() {
        return Ok("[]".into());
    }
    let mut entries = Vec::new();
    for (index, item) in fs::read_dir(&path).map_err(|e| e.to_string())?.enumerate() {
        if index >= 1024 {
            return Err("Resource history exceeds 1,024 transactions".into());
        }
        let item = item.map_err(|e| e.to_string())?;
        if !item.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let id = item
            .file_name()
            .into_string()
            .map_err(|_| "Invalid resource history name")?;
        let path = history_path(dir, &id)?;
        let (state, error) = match read_state(&path) {
            Ok((_, state)) => (state, "null".to_string()),
            Err(error) => ("incomplete".to_string(), quote(&error)),
        };
        entries.push((
            id.clone(),
            util::object(&[
                ("id", quote(&id)),
                ("state", quote(&state)),
                ("path", quote(&path.to_string_lossy())),
                ("error", error),
            ]),
        ));
    }
    entries.sort_by(|a, b| b.0.cmp(&a.0));
    Ok(format!(
        "[{}]",
        entries
            .into_iter()
            .map(|(_, value)| value)
            .collect::<Vec<_>>()
            .join(",")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn setup() -> (PathBuf, Scratch) {
        let path = std::env::temp_dir().join(format!(
            "zmcl-resources-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        fs::create_dir_all(path.join("game/resourcepacks")).unwrap();
        (path.clone(), Scratch(path))
    }
    fn entry(bytes: &[u8], version: &str) -> Entry {
        Entry {
            kind: Kind::ResourcePack,
            project: "Project1".into(),
            slug: "tiny-pack".into(),
            title: "Tiny".into(),
            version: version.into(),
            number: version.into(),
            published: "2026-01-01T00:00:00Z".into(),
            game: "1.21.1".into(),
            filename: "tiny.zip".into(),
            sha1: zmcl_core::hash::sha1_hex(bytes),
            size: bytes.len() as u64,
            url: "https://cdn.modrinth.com/data/Project1/tiny.zip".into(),
            enabled: true,
            loaders: vec!["minecraft".into()],
            requirements: Vec::new(),
        }
    }
    #[test]
    fn update_retains_old_bytes_and_manual_files_then_restores() {
        let (dir, _scratch) = setup();
        let old = entry(b"old-archive", "old");
        let new = entry(b"new-archive", "new");
        let game = game_dir(&dir).unwrap();
        fs::write(game.join(old.actual_path()), b"old-archive").unwrap();
        fs::write(game.join("resourcepacks/manual.zip"), b"manual").unwrap();
        let old_lock = lock_json(std::slice::from_ref(&old));
        util::atomic_write(&dir.join(LOCK), old_lock.as_bytes()).unwrap();
        let input = dir.join("download.zip");
        fs::write(&input, b"new-archive").unwrap();
        let sources = BTreeMap::from([(new.key(), input)]);
        let history = commit(
            &dir,
            std::slice::from_ref(&old),
            Some(old_lock.as_bytes()),
            std::slice::from_ref(&new),
            &sources,
        )
        .unwrap();
        assert_eq!(
            fs::read(game.join(new.actual_path())).unwrap(),
            b"new-archive"
        );
        assert_eq!(
            fs::read(game.join("resourcepacks/manual.zip")).unwrap(),
            b"manual"
        );
        assert_eq!(
            fs::read(dir.join(HISTORY).join(&history).join("old-0.zip")).unwrap(),
            b"old-archive"
        );
        assert!(!dir.join(JOURNAL).exists());
        restore(&dir, &history).unwrap();
        assert_eq!(
            fs::read(game.join(old.actual_path())).unwrap(),
            b"old-archive"
        );
        assert_eq!(
            fs::read(game.join("resourcepacks/manual.zip")).unwrap(),
            b"manual"
        );
    }
    #[test]
    fn manual_collision_and_modified_managed_pack_are_untouched() {
        let (dir, _scratch) = setup();
        let next = entry(b"new-archive", "new");
        let target = dir.join("game/resourcepacks/tiny.zip");
        fs::write(&target, b"manual").unwrap();
        let source = dir.join("source.zip");
        fs::write(&source, b"new-archive").unwrap();
        let sources = BTreeMap::from([(next.key(), source)]);
        assert!(commit(&dir, &[], None, std::slice::from_ref(&next), &sources).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"manual");
        let previous = entry(b"expected", "old");
        let lock = lock_json(std::slice::from_ref(&previous));
        util::atomic_write(&dir.join(LOCK), lock.as_bytes()).unwrap();
        assert!(commit(&dir, &[previous], Some(lock.as_bytes()), &[next], &sources).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"manual");
        assert!(!dir.join(JOURNAL).exists());
    }
    #[test]
    #[cfg(unix)]
    fn interrupted_publication_rolls_back_without_losing_original() {
        let (dir, _scratch) = setup();
        let old = entry(b"old-archive", "old");
        let new = entry(b"new-archive", "new");
        let game = game_dir(&dir).unwrap();
        let target = game.join(old.actual_path());
        fs::write(&target, b"old-archive").unwrap();
        let before = lock_json(std::slice::from_ref(&old));
        let after = lock_json(std::slice::from_ref(&new));
        util::atomic_write(&dir.join(LOCK), before.as_bytes()).unwrap();
        let mut stage = Stage::new(&dir).unwrap();
        util::atomic_write(&stage.path.join("before-lock.json"), before.as_bytes()).unwrap();
        util::atomic_write(&stage.path.join("after-lock.json"), after.as_bytes()).unwrap();
        fs::write(stage.path.join("new-0.zip"), b"new-archive").unwrap();
        state(&stage.path, true, "prepared").unwrap();
        util::atomic_write(
            &dir.join(JOURNAL),
            util::object(&[("format", "1".into()), ("id", quote(&stage.id))]).as_bytes(),
        )
        .unwrap();
        stage.retain = true;
        fs::rename(&target, stage.path.join("old-0.zip")).unwrap();
        fs::hard_link(stage.path.join("new-0.zip"), &target).unwrap();
        recover(&dir).unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"old-archive");
        assert_eq!(fs::read(dir.join(LOCK)).unwrap(), before.as_bytes());
        assert!(!dir.join(JOURNAL).exists());
    }
    #[test]
    #[cfg(unix)]
    fn recovery_resumes_after_an_original_was_already_restored() {
        let (dir, _scratch) = setup();
        let old = entry(b"old-archive", "old");
        let new = entry(b"new-archive", "new");
        let before = lock_json(std::slice::from_ref(&old));
        let after = lock_json(std::slice::from_ref(&new));
        util::atomic_write(&dir.join(LOCK), after.as_bytes()).unwrap();
        let mut stage = Stage::new(&dir).unwrap();
        util::atomic_write(&stage.path.join("before-lock.json"), before.as_bytes()).unwrap();
        util::atomic_write(&stage.path.join("after-lock.json"), after.as_bytes()).unwrap();
        fs::write(stage.path.join("old-0.zip"), b"old-archive").unwrap();
        fs::write(stage.path.join("new-0.zip"), b"new-archive").unwrap();
        fs::hard_link(
            stage.path.join("old-0.zip"),
            dir.join("game").join(old.actual_path()),
        )
        .unwrap();
        state(&stage.path, true, "prepared").unwrap();
        util::atomic_write(
            &dir.join(JOURNAL),
            util::object(&[("format", "1".into()), ("id", quote(&stage.id))]).as_bytes(),
        )
        .unwrap();
        stage.retain = true;
        recover(&dir).unwrap();
        assert_eq!(fs::read(dir.join(LOCK)).unwrap(), before.as_bytes());
        assert_eq!(
            fs::read(dir.join("game").join(old.actual_path())).unwrap(),
            b"old-archive"
        );
        assert!(!dir.join(JOURNAL).exists());
    }
    #[test]
    fn recovery_preserves_a_manual_replacement() {
        let (dir, _scratch) = setup();
        let old = entry(b"old-archive", "old");
        let new = entry(b"new-archive", "new");
        let before = lock_json(std::slice::from_ref(&old));
        let after = lock_json(std::slice::from_ref(&new));
        util::atomic_write(&dir.join(LOCK), before.as_bytes()).unwrap();
        let mut stage = Stage::new(&dir).unwrap();
        util::atomic_write(&stage.path.join("before-lock.json"), before.as_bytes()).unwrap();
        util::atomic_write(&stage.path.join("after-lock.json"), after.as_bytes()).unwrap();
        fs::write(stage.path.join("old-0.zip"), b"old-archive").unwrap();
        fs::write(stage.path.join("new-0.zip"), b"new-archive").unwrap();
        fs::write(
            dir.join("game").join(new.actual_path()),
            b"user replacement",
        )
        .unwrap();
        state(&stage.path, true, "prepared").unwrap();
        util::atomic_write(
            &dir.join(JOURNAL),
            util::object(&[("format", "1".into()), ("id", quote(&stage.id))]).as_bytes(),
        )
        .unwrap();
        stage.retain = true;
        assert!(recover(&dir).is_err());
        assert_eq!(
            fs::read(dir.join("game").join(new.actual_path())).unwrap(),
            b"user replacement"
        );
        assert_eq!(
            fs::read(stage.path.join("old-0.zip")).unwrap(),
            b"old-archive"
        );
        assert!(dir.join(JOURNAL).exists());
    }
}
