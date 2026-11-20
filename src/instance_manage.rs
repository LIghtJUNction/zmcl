//! Instance copies publish only after a complete bounded traversal. Removal is
//! a reversible move into launcher-owned trash; shared objects are retained.
use crate::{
    App, Instance,
    util::{self, Result},
    worlds,
};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
use zmcl_core::json::{get_str, quote, req_str, with_json};
static NEXT: AtomicU64 = AtomicU64::new(0);
const MAX_FILES: usize = 50_000;
const MAX_BYTES: u64 = 4 * 1024 * 1024 * 1024;
struct Stage(PathBuf);
impl Drop for Stage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn token(prefix: &str) -> String {
    format!(
        "{prefix}-{}-{}-{}",
        util::now(),
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    )
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(usize, u64)> {
    fs::create_dir(destination).map_err(|e| e.to_string())?;
    let mut pending = vec![(source.to_path_buf(), destination.to_path_buf(), 0usize)];
    let mut files = 0usize;
    let mut total = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    while let Some((from, to, depth)) = pending.pop() {
        if depth > 32 {
            return Err("Instance directory nesting exceeds 32 levels".into());
        }
        for entry in fs::read_dir(&from).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| "Instance filenames must be valid Unicode")?;
            let dest = util::safe_join(&to, &name)?;
            let meta = fs::symlink_metadata(entry.path()).map_err(|e| e.to_string())?;
            files += 1;
            if files > MAX_FILES {
                return Err("Instance copy exceeds 50000 entries".into());
            }
            if meta.is_dir() {
                fs::create_dir(&dest).map_err(|e| e.to_string())?;
                pending.push((entry.path(), dest, depth + 1));
            } else if meta.is_file() {
                if meta.len() > 512 * 1024 * 1024 {
                    return Err("Instance file exceeds 512 MiB".into());
                }
                total = total
                    .checked_add(meta.len())
                    .ok_or("Instance size overflow")?;
                if total > MAX_BYTES {
                    return Err("Instance copy exceeds 4 GiB".into());
                }
                let mut input = fs::File::open(entry.path()).map_err(|e| e.to_string())?;
                let mut options = fs::OpenOptions::new();
                options.write(true).create_new(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.mode(0o600);
                }
                let mut output = options.open(&dest).map_err(|e| e.to_string())?;
                let mut count = 0u64;
                loop {
                    let size = input.read(&mut buffer).map_err(|e| e.to_string())?;
                    if size == 0 {
                        break;
                    }
                    count += size as u64;
                    if count > meta.len() {
                        return Err("Instance data changed during copy".into());
                    }
                    output
                        .write_all(&buffer[..size])
                        .map_err(|e| e.to_string())?;
                }
                output.sync_all().map_err(|e| e.to_string())?;
                let after = fs::symlink_metadata(entry.path()).map_err(|e| e.to_string())?;
                if count != meta.len()
                    || after.len() != meta.len()
                    || after.modified().ok() != meta.modified().ok()
                {
                    return Err("Instance data changed during copy".into());
                }
            } else {
                return Err("Instance copies reject links and special files".into());
            }
        }
    }
    Ok((files, total))
}

/// Clone mutable game data independently; game binaries are re-linked by install.
pub fn clone_instance(app: &App, source_id: &str, destination_id: &str) -> Result<String> {
    util::valid_id(source_id)?;
    util::valid_id(destination_id)?;
    if source_id == destination_id {
        return Err("Source and destination instances must differ".into());
    }
    let mut ids = [source_id, destination_id];
    ids.sort();
    let _first = util::Lock::acquire(&app.root, &format!("instance-{}", ids[0]))?;
    let _second = util::Lock::acquire(&app.root, &format!("instance-{}", ids[1]))?;
    util::check_pending_transactions(&app.dir(source_id)?)?;
    worlds::assert_stopped(app, source_id)?;
    let mut instance = app.read_instance(source_id)?;
    let source = app.dir(source_id)?;
    let destination = app.dir(destination_id)?;
    if destination.exists() {
        return Err("Destination instance already exists".into());
    }
    let stage_path = util::safe_join(&app.root, &format!("instances/{}", token("clone")))?;
    fs::create_dir(&stage_path).map_err(|e| e.to_string())?;
    let stage = Stage(stage_path);
    let (files, bytes) = copy_tree(&source.join("game"), &stage.0.join("game"))?;
    // The copied Modrinth lock describes the copied game/mods contents.
    for file in ["mods-lock.json", "resources-lock.json"] {
        let path = util::safe_join(&source, file)?;
        if path.exists() {
            let data = util::read_limited(&path, 8 * 1024 * 1024)?;
            util::atomic_write(&stage.0.join(file), &data)?;
        }
    }
    instance.id = destination_id.into();
    instance.name = format!("{} (copy)", instance.name);
    instance.installed = false;
    util::atomic_write(&stage.0.join("instance.json"), instance.json().as_bytes())?;
    util::check_pending_transactions(&app.dir(source_id)?)?;
    worlds::assert_stopped(app, source_id)?;
    fs::rename(&stage.0, &destination).map_err(|e| e.to_string())?;
    Ok(util::object(&[
        ("instance", instance.json()),
        ("copied_entries", files.to_string()),
        ("copied_bytes", bytes.to_string()),
        ("requires_install", "true".into()),
    ]))
}

pub fn trash(app: &App, id: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    util::check_pending_transactions(&app.dir(id)?)?;
    worlds::assert_stopped(app, id)?;
    let instance = app.read_instance(id)?;
    let base = util::safe_join(&app.root, "trash")?;
    fs::create_dir_all(&base).map_err(|e| e.to_string())?;
    let item_id = token(id);
    let container = util::safe_join(&base, &item_id)?;
    fs::create_dir(&container).map_err(|e| e.to_string())?;
    let record = util::object(&[
        ("trash_id", quote(&item_id)),
        ("instance_id", quote(id)),
        ("name", quote(&instance.name)),
        ("trashed_at", util::now().to_string()),
    ]);
    if let Err(error) = util::atomic_write(&container.join("item.json"), record.as_bytes()) {
        let _ = fs::remove_dir(&container);
        return Err(error);
    }
    if let Err(error) = fs::rename(app.dir(id)?, container.join("instance")) {
        let _ = fs::remove_file(container.join("item.json"));
        let _ = fs::remove_dir(&container);
        return Err(error.to_string());
    }
    Ok(record)
}

pub fn list_trash(app: &App) -> Result<String> {
    let base = util::safe_join(&app.root, "trash")?;
    if !base.exists() {
        return Ok("[]".into());
    }
    let mut records = Vec::new();
    for entry in fs::read_dir(&base).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let path = util::safe_join(&base, &entry.file_name().to_string_lossy())?;
        let bytes = util::read_limited(&path.join("item.json"), 8192)?;
        records.push(with_json(&bytes, |value| {
            Ok(util::object(&[
                ("trash_id", quote(&req_str(value, "trash_id")?)),
                ("instance_id", quote(&req_str(value, "instance_id")?)),
                ("name", quote(&get_str(value, "name").unwrap_or_default())),
                (
                    "trashed_at",
                    value
                        .get("trashed_at")
                        .and_then(|v| v.as_u64())
                        .unwrap_or_default()
                        .to_string(),
                ),
            ]))
        })?);
    }
    records.sort();
    Ok(format!("[{}]", records.join(",")))
}

pub fn restore(app: &App, trash_id: &str, new_id: Option<&str>) -> Result<String> {
    if trash_id.len() > 120
        || trash_id.is_empty()
        || !trash_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("Invalid trash ID".into());
    }
    let container = util::safe_join(&app.root, &format!("trash/{trash_id}"))?;
    let _trash_lock = util::Lock::acquire(&app.root, &format!("trash-{trash_id}"))?;
    let source = util::safe_join(&container, "instance")?;
    let original = util::read_limited(&source.join("instance.json"), 64 * 1024)?;
    let mut instance = Instance::parse(&original)?;
    let id = new_id.unwrap_or(&instance.id).to_owned();
    util::valid_id(&id)?;
    let _instance_lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let destination = app.dir(&id)?;
    if destination.exists() {
        return Err("An instance with the restore ID already exists".into());
    }
    instance.id = id;
    util::atomic_write(&source.join("instance.json"), instance.json().as_bytes())?;
    if let Err(error) = fs::rename(&source, &destination) {
        let _ = util::atomic_write(&source.join("instance.json"), &original);
        return Err(error.to_string());
    }
    let _ = fs::remove_file(container.join("item.json"));
    let _ = fs::remove_dir(container);
    Ok(instance.json())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clone_and_trash_preserve_original_game_bytes() {
        let dir = std::env::temp_dir().join(token("zmcl-instance-test"));
        let app = App::new(Some(dir.to_string_lossy().into_owned())).unwrap();
        app.create_instance("source", "1.21.1", "vanilla", "")
            .unwrap();
        let file = app.dir("source").unwrap().join("game/options.txt");
        fs::write(&file, b"music:0.5").unwrap();
        clone_instance(&app, "source", "copy").unwrap();
        let copy = app.dir("copy").unwrap().join("game/options.txt");
        fs::write(&copy, b"music:1.0").unwrap();
        assert_eq!(fs::read(&file).unwrap(), b"music:0.5");
        assert!(!app.read_instance("copy").unwrap().installed);
        assert!(clone_instance(&app, "source", "copy").is_err());
        let record = trash(&app, "copy").unwrap();
        let id = with_json(record.as_bytes(), |v| req_str(v, "trash_id")).unwrap();
        assert!(app.read_instance("copy").is_err());
        assert!(list_trash(&app).unwrap().contains(&id));
        restore(&app, &id, None).unwrap();
        assert_eq!(fs::read(&copy).unwrap(), b"music:1.0");
        assert_eq!(list_trash(&app).unwrap(), "[]");
        fs::remove_dir_all(dir).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn clone_rejects_symlink_without_publishing_instance() {
        let dir = std::env::temp_dir().join(token("zmcl-instance-link"));
        let app = App::new(Some(dir.to_string_lossy().into_owned())).unwrap();
        app.create_instance("source", "1.21.1", "vanilla", "")
            .unwrap();
        std::os::unix::fs::symlink("/etc", app.dir("source").unwrap().join("game/outside"))
            .unwrap();
        assert!(clone_instance(&app, "source", "copy").is_err());
        assert!(!app.dir("copy").unwrap().exists());
        fs::remove_dir_all(dir).unwrap();
    }
}
