//! Modrinth content and bounded, staged `.mrpack` import/export.
//! ZIP encoding uses an isolated system Python 3 with its standard library.
use crate::{
    App, net, store,
    util::{self, Lock},
    worlds,
};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
};
use zmcl_core::json::{Kind, Value, get_str, quote, req_str, string, with_json};

type Result<T> = std::result::Result<T, String>;
const API: &str = "https://api.modrinth.com/v2";
const MAX_FILE: u64 = 512 * 1024 * 1024;
const MAX_TOTAL: u64 = 4 * 1024 * 1024 * 1024;
const MAX_FILES: usize = 50_000;
const MAX_LOCK: usize = 8 * 1024 * 1024;
const ZIP_CODEC: &str = include_str!("content/archive.py");
static NEXT: AtomicU64 = AtomicU64::new(0);
mod management;
mod transaction;
pub use management::{install_project_pack, pack_search, remove, update};
pub use transaction::recover;

#[derive(Clone, Debug, PartialEq)]
struct Entry {
    project: String,
    version: String,
    path: String,
    sha1: String,
    sha512: Option<String>,
    url: String,
    size: u64,
    enabled: bool,
    project_slug: String,
    explicit: bool,
    dependencies: Option<Vec<(bool, String)>>,
    incompatible: Vec<(bool, String)>,
}
impl Entry {
    fn actual_path(&self) -> String {
        if self.enabled {
            self.path.clone()
        } else {
            format!("{}.disabled", self.path)
        }
    }
    fn json(&self) -> String {
        util::object(&[
            ("project_id", quote(&self.project)),
            ("version_id", quote(&self.version)),
            ("path", quote(&self.path)),
            ("sha1", quote(&self.sha1)),
            (
                "sha512",
                self.sha512
                    .as_ref()
                    .map_or_else(|| "null".into(), |v| quote(v)),
            ),
            ("url", quote(&self.url)),
            ("size", self.size.to_string()),
            ("enabled", self.enabled.to_string()),
            ("project_slug", quote(&self.project_slug)),
            ("explicit", self.explicit.to_string()),
            (
                "dependencies",
                self.dependencies
                    .as_deref()
                    .map(dependency_json)
                    .unwrap_or_else(|| "null".into()),
            ),
            ("incompatible", dependency_json(&self.incompatible)),
        ])
    }
    fn parse(v: Value<'_, '_>) -> Result<Self> {
        let path = req_str(v, "path")?;
        portable_path(&path)?;
        let sha1 = digest(&req_str(v, "sha1")?, 40)?;
        let sha512 = get_str(v, "sha512").map(|v| digest(&v, 128)).transpose()?;
        let url = req_str(v, "url")?;
        content_url(&url)?;
        let size = v
            .get("size")
            .and_then(Value::as_u64)
            .ok_or("Missing content size")?;
        if size > MAX_FILE {
            return Err("Content file exceeds 512 MiB".into());
        }
        Ok(Self {
            project: get_str(v, "project_id").unwrap_or_default(),
            version: get_str(v, "version_id").unwrap_or_default(),
            path,
            sha1,
            sha512,
            url,
            size,
            enabled: v.get("enabled").and_then(Value::as_bool).unwrap_or(true),
            project_slug: get_str(v, "project_slug").unwrap_or_default(),
            explicit: v.get("explicit").and_then(Value::as_bool).unwrap_or(true),
            dependencies: v
                .get("dependencies")
                .filter(|x| x.kind() == Kind::Array)
                .map(parse_dependencies)
                .transpose()?,
            incompatible: v
                .get("incompatible")
                .filter(|x| x.kind() == Kind::Array)
                .map(parse_dependencies)
                .transpose()?
                .unwrap_or_default(),
        })
    }
}

fn dependency_json(dependencies: &[(bool, String)]) -> String {
    format!(
        "[{}]",
        dependencies
            .iter()
            .map(|(exact, id)| util::object(&[("exact", exact.to_string()), ("id", quote(id))]))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn parse_dependencies(value: Value<'_, '_>) -> Result<Vec<(bool, String)>> {
    let mut dependencies = Vec::new();
    for value in value.array() {
        let exact = value
            .get("exact")
            .and_then(Value::as_bool)
            .ok_or("Invalid cached dependency kind")?;
        let id = req_str(value, "id")?;
        slug(&id)?;
        dependencies.push((exact, id));
        if dependencies.len() > 512 {
            return Err("Cached dependency list exceeds its budget".into());
        }
    }
    Ok(dependencies)
}
fn client_supported(value: Value<'_, '_>) -> bool {
    get_str(value, "client_side").as_deref() != Some("unsupported")
        && !matches!(
            get_str(value, "environment").as_deref(),
            Some("server_only" | "dedicated_server_only")
        )
}

fn portable_path(path: &str) -> Result<()> {
    if path
        .split('/')
        .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err("Empty or relative path component in content".into());
    }
    util::safe_join(Path::new("."), path).map(|_| ())
}
fn digest(value: &str, size: usize) -> Result<String> {
    if value.len() != size || !value.bytes().all(|v| v.is_ascii_hexdigit()) {
        return Err("Invalid content hash".into());
    }
    Ok(value.to_ascii_lowercase())
}
fn slug(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b"-_".contains(&v))
    {
        return Err(
            "Project and version identifiers must contain ASCII letters, digits, '-' or '_'".into(),
        );
    }
    Ok(())
}
fn content_url(url: &str) -> Result<()> {
    let tail = url
        .strip_prefix("https://")
        .ok_or("Content downloads require HTTPS")?;
    let authority = tail.split(['/', '?', '#']).next().unwrap_or("");
    if !matches!(
        authority,
        "cdn.modrinth.com" | "github.com" | "raw.githubusercontent.com" | "gitlab.com"
    ) || url.chars().any(|c| c.is_control() || c.is_whitespace())
        || url.contains('\\')
    {
        return Err("Pack download host is not in the supported Modrinth allowlist".into());
    }
    Ok(())
}

fn lock_json(entries: &[Entry]) -> String {
    format!(
        "{{\"format\":1,\"entries\":[{}]}}",
        entries
            .iter()
            .map(Entry::json)
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn parse_lock(bytes: &[u8]) -> Result<Vec<Entry>> {
    with_json(bytes, |v| {
        if v.get("format").and_then(Value::as_u64) != Some(1) {
            return Err("Unsupported content lock format".into());
        }
        let values = v.get("entries").ok_or("Missing content lock entries")?;
        if values.kind() != Kind::Array {
            return Err("Content entries must be an array".into());
        }
        let mut entries = Vec::new();
        let mut paths = BTreeSet::new();
        for value in values.array() {
            let entry = Entry::parse(value)?;
            if !paths.insert(entry.path.to_lowercase()) {
                return Err("Duplicate content lock path".into());
            }
            entries.push(entry);
            if entries.len() > MAX_FILES {
                return Err("Content lock exceeds file budget".into());
            }
        }
        Ok(entries)
    })
}
fn read_lock(instance: &Path) -> Result<Vec<Entry>> {
    if instance.join(".content-journal.json").exists() {
        return Err("An interrupted content transaction needs recovery; run zmcl mods recover INSTANCE. Preserve all stage directories until recovery succeeds".into());
    }
    let path = util::safe_join(instance, "mods-lock.json")?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    parse_lock(&util::read_limited(&path, MAX_LOCK)?)
}

/// Searches public Modrinth projects with exact game/loader facets.
pub fn search(query: &str, game: Option<&str>, loader: Option<&str>) -> Result<String> {
    if query.len() > 512 {
        return Err("Search query exceeds 512 bytes".into());
    }
    let mut facets = vec!["[\"project_type:mod\"]".to_string()];
    if let Some(game) = game {
        facets.push(format!("[{}]", quote(&format!("versions:{game}"))));
    }
    if let Some(loader) = loader {
        facets.push(format!("[{}]", quote(&format!("categories:{loader}"))));
    }
    let url = format!(
        "{API}/search?query={}&limit=20&facets={}",
        net::encode(query),
        net::encode(&format!("[{}]", facets.join(",")))
    );
    let body = net::get(&url)?;
    with_json(&body, |v| {
        if v.get("hits").is_none_or(|x| x.kind() != Kind::Array) {
            return Err("Modrinth returned an invalid search response".into());
        }
        Ok(())
    })?;
    String::from_utf8(body).map_err(|_| "Modrinth response is not UTF-8".into())
}

fn supports(v: Value<'_, '_>, game: &str, loader: &str) -> bool {
    let contains = |name, needle| {
        v.get(name).is_some_and(|x| {
            x.array()
                .any(|item| string(item).as_deref() == Some(needle))
        })
    };
    contains("game_versions", game) && contains("loaders", loader)
}

#[derive(Clone, Debug)]
struct Version {
    entry: Entry,
    dependencies: Vec<(bool, String)>,
    incompatible: Vec<(bool, String)>,
}
fn version(v: Value<'_, '_>, game: &str, loader: &str) -> Result<Version> {
    version_file(v, game, loader, None)
}
fn version_file(
    v: Value<'_, '_>,
    game: &str,
    loader: &str,
    file_hash: Option<&str>,
) -> Result<Version> {
    if !supports(v, game, loader) || !client_supported(v) {
        return Err("Required Mod version does not support this game version and loader".into());
    }
    let project = req_str(v, "project_id")?;
    let version = req_str(v, "id")?;
    slug(&project)?;
    slug(&version)?;
    let files = v.get("files").ok_or("Mod version has no files")?;
    let candidates: Vec<_> = files
        .array()
        .filter(|f| {
            get_str(*f, "filename").is_some_and(|n| n.to_ascii_lowercase().ends_with(".jar"))
                && !matches!(
                    get_str(*f, "file_type").as_deref(),
                    Some("sources-jar" | "dev-jar" | "javadoc-jar" | "signature")
                )
                && file_hash.is_none_or(|wanted| {
                    f.get("hashes")
                        .and_then(|h| get_str(h, "sha1"))
                        .is_some_and(|actual| actual.eq_ignore_ascii_case(wanted))
                })
        })
        .collect();
    let file = candidates
        .iter()
        .find(|f| f.get("primary").and_then(Value::as_bool) == Some(true))
        .or_else(|| candidates.first())
        .ok_or("Mod version has no JAR file")?;
    let name = req_str(*file, "filename")?;
    portable_path(&name)?;
    if name.contains('/') {
        return Err("Mod filename must be a single component".into());
    }
    let hashes = file.get("hashes").ok_or("Mod version has no file hashes")?;
    let sha1 = digest(&req_str(hashes, "sha1")?, 40)?;
    let sha512 = get_str(hashes, "sha512")
        .map(|v| digest(&v, 128))
        .transpose()?;
    let url = req_str(*file, "url")?;
    content_url(&url)?;
    let size = file
        .get("size")
        .and_then(Value::as_u64)
        .ok_or("Mod file has no size")?;
    if size > MAX_FILE {
        return Err("Mod file exceeds 512 MiB".into());
    }
    let mut dependencies = Vec::new();
    let mut incompatible = Vec::new();
    if let Some(values) = v.get("dependencies") {
        if values.kind() != Kind::Array {
            return Err("Invalid Mod dependency list".into());
        }
        for dep in values.array() {
            let kind = get_str(dep, "dependency_type").unwrap_or_default();
            if kind != "required" && kind != "incompatible" {
                continue;
            }
            let dependency = if let Some(id) = get_str(dep, "version_id") {
                (true, id)
            } else if let Some(id) = get_str(dep, "project_id") {
                (false, id)
            } else {
                return Err(
                    "A required dependency has no downloadable project/version identifier".into(),
                );
            };
            slug(&dependency.1)?;
            if kind == "required" {
                dependencies.push(dependency);
            } else {
                incompatible.push(dependency);
            }
            if dependencies.len() + incompatible.len() > 512 {
                return Err("Mod version dependency list exceeds its budget".into());
            }
        }
    }
    Ok(Version {
        entry: Entry {
            project,
            version,
            path: format!("mods/{name}"),
            sha1,
            sha512,
            url,
            size,
            enabled: true,
            project_slug: String::new(),
            explicit: false,
            dependencies: Some(dependencies.clone()),
            incompatible: incompatible.clone(),
        },
        dependencies,
        incompatible,
    })
}

fn fetch_version(exact: bool, id: &str, game: &str, loader: &str) -> Result<Version> {
    slug(id)?;
    let mut selected = if exact {
        with_json(&net::get(&format!("{API}/version/{id}"))?, |v| {
            version(v, game, loader)
        })?
    } else {
        let url = format!(
            "{API}/project/{id}/version?include_changelog=false&game_versions={}&loaders={}",
            net::encode(&format!("[{}]", quote(game))),
            net::encode(&format!("[{}]", quote(loader)))
        );
        with_json(&net::get(&url)?, |v| {
            if v.kind() != Kind::Array {
                return Err("Invalid Modrinth versions response".into());
            }
            let values: Vec<_> = v.array().filter(|v| supports(*v, game, loader)).collect();
            let candidate = values
                .iter()
                .filter(|v| get_str(**v, "version_type").as_deref() == Some("release"))
                .max_by_key(|v| get_str(**v, "date_published").unwrap_or_default())
                .or_else(|| {
                    values
                        .iter()
                        .max_by_key(|v| get_str(**v, "date_published").unwrap_or_default())
                })
                .ok_or("No compatible Mod version found")?;
            version(*candidate, game, loader)
        })?
    };
    with_json(
        &net::get(&format!("{API}/project/{}", selected.entry.project))?,
        |v| {
            if get_str(v, "project_type").as_deref() != Some("mod") {
                return Err("This command only installs Mod projects".into());
            }
            if !client_supported(v) {
                return Err("Required Mod is server-only".into());
            }
            if req_str(v, "id")? != selected.entry.project {
                return Err("Modrinth returned inconsistent project metadata".into());
            }
            selected.entry.project_slug = req_str(v, "slug")?;
            Ok(())
        },
    )?;
    Ok(selected)
}

struct Temp {
    path: PathBuf,
    retain: bool,
}
impl Temp {
    fn new(parent: &Path) -> Result<Self> {
        for _ in 0..64 {
            let path = parent.join(format!(
                ".content-stage-{}-{}-{}",
                std::process::id(),
                util::now(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::create_dir(&path) {
                Ok(()) => {
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
                            .map_err(|e| e.to_string())?;
                    }
                    return Ok(Self {
                        path,
                        retain: false,
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e.to_string()),
            }
        }
        Err("Cannot allocate a content staging directory".into())
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        if !self.retain {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

fn clone_mods(source: &Path, target: &Path) -> Result<()> {
    fs::create_dir(target).map_err(|e| e.to_string())?;
    if !source.exists() {
        return Ok(());
    }
    let mut todo = vec![(source.to_path_buf(), target.to_path_buf())];
    let mut count = 0usize;
    while let Some((source, target)) = todo.pop() {
        for entry in fs::read_dir(&source).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| "Non-UTF-8 Mod filename")?;
            portable_path(&name)?;
            let info = entry.file_type().map_err(|e| e.to_string())?;
            let destination = target.join(name);
            if info.is_symlink() {
                return Err("Mod directory contains a symbolic link".into());
            }
            if info.is_dir() {
                fs::create_dir(&destination).map_err(|e| e.to_string())?;
                todo.push((entry.path(), destination));
            } else if info.is_file() {
                store::materialize(&entry.path(), &destination, true)?;
            } else {
                return Err("Mod directory contains a special file".into());
            }
            count += 1;
            if count > MAX_FILES {
                return Err("Mod directory exceeds file budget".into());
            }
        }
    }
    Ok(())
}
fn verify_entry(game: &Path, entry: &Entry) -> Result<()> {
    let path = util::safe_join(game, &entry.actual_path())?;
    let (sha1, _, size) = store::digests(&path)?;
    if sha1 != entry.sha1 || size != entry.size {
        return Err(format!("Managed content changed on disk: {}", entry.path));
    }
    Ok(())
}

/// Commit a complete mods directory and its lock. Failed transactions retain
/// identified staging data; a successful call removes its temporary journal.
fn commit_mods(instance: &Path, temp: &mut Temp, entries: &[Entry]) -> Result<()> {
    transaction::commit(instance, temp, entries)
}

/// Install a Mod and all required dependencies. Unsupported or conflicting
/// dependencies fail before changing the instance's active files.
pub fn add(app: &App, id: &str, project: &str) -> Result<()> {
    management::add(app, id, project)
}

pub fn list(app: &App, id: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    app.read_instance(id)?;
    Ok(lock_json(&read_lock(&app.dir(id)?)?))
}

pub fn toggle(app: &App, id: &str, project_or_filename: &str, enabled: bool) -> Result<()> {
    management::toggle(app, id, project_or_filename, enabled)
}

#[derive(Debug)]
struct Pack {
    name: String,
    minecraft: String,
    loader: String,
    loader_version: String,
    files: Vec<Entry>,
}
fn parse_pack(bytes: &[u8]) -> Result<Pack> {
    with_json(bytes, |v| {
        if v.get("formatVersion").and_then(Value::as_u64) != Some(1)
            || get_str(v, "game").as_deref() != Some("minecraft")
        {
            return Err("Only Minecraft .mrpack formatVersion 1 is supported".into());
        }
        let dependencies = v
            .get("dependencies")
            .ok_or("Pack has no dependency manifest")?;
        let minecraft = req_str(dependencies, "minecraft")?;
        let name = req_str(v, "name")?;
        if name.trim().is_empty() || name.len() > 256 || name.chars().any(char::is_control) {
            return Err("Pack name must be 1–256 bytes without control characters".into());
        }
        let mut loader = "vanilla".to_string();
        let mut loader_version = String::new();
        for (key, value) in dependencies.object() {
            let key = key.as_str().map(str::to_owned).unwrap_or_else(|| {
                String::from_utf8_lossy(&key.bytes().collect::<Vec<_>>()).into_owned()
            });
            if key == "minecraft" {
                continue;
            }
            let mapped = match key.as_str() {
                "fabric-loader" => "fabric",
                "quilt-loader" => "quilt",
                "forge" => "forge",
                "neoforge" => "neoforge",
                _ => {
                    return Err(format!(
                        "Pack dependency '{key}' is not yet supported by this importer"
                    ));
                }
            };
            if loader != "vanilla" {
                return Err("Pack declares more than one Mod loader".into());
            }
            loader = mapped.into();
            loader_version = string(value).ok_or("Invalid loader version")?;
        }
        if loader != "vanilla"
            && (loader_version.is_empty()
                || loader_version.len() > 128
                || !loader_version
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"-_.+".contains(&c)))
        {
            return Err("Invalid Mod loader version in pack".into());
        }
        let source = v.get("files").ok_or("Pack has no files array")?;
        if source.kind() != Kind::Array {
            return Err("Pack files must be an array".into());
        }
        let mut files = Vec::new();
        let mut paths = BTreeSet::new();
        let mut total = 0u64;
        for item in source.array() {
            let path = req_str(item, "path")?;
            portable_path(&path)?;
            if path.split('/').any(|p| p.starts_with(".zmcl-")) {
                return Err("Reserved launcher path in pack".into());
            }
            if !paths.insert(path.to_lowercase()) {
                return Err("Duplicate pack destination".into());
            }
            let hashes = item.get("hashes").ok_or("Pack file has no hashes")?;
            let sha1 = digest(&req_str(hashes, "sha1")?, 40)?;
            let sha512 = Some(digest(&req_str(hashes, "sha512")?, 128)?);
            let size = item
                .get("fileSize")
                .and_then(Value::as_u64)
                .ok_or("Pack file has no size")?;
            if size > MAX_FILE {
                return Err("Pack file exceeds 512 MiB".into());
            }
            if let Some(env) = item.get("env") {
                match get_str(env, "client").as_deref() {
                    Some("unsupported") => continue,
                    Some("optional") => continue, // Explicit deterministic first-version policy.
                    Some("required") | None => (),
                    _ => return Err("Unknown pack client environment requirement".into()),
                }
            }
            let urls = item
                .get("downloads")
                .ok_or("Pack file has no download URLs")?;
            if urls.kind() != Kind::Array {
                return Err("Pack downloads must be an array".into());
            }
            let mut supported = None;
            for value in urls.array() {
                let url = string(value).ok_or("Invalid download URL")?;
                content_url(&url)?;
                if supported.is_none() {
                    supported = Some(url);
                }
            }
            let url = supported.ok_or("Pack file has no download URL")?;
            total = total.checked_add(size).ok_or("Pack size overflow")?;
            if total > MAX_TOTAL || files.len() >= MAX_FILES {
                return Err("Pack exceeds content budget".into());
            }
            files.push(Entry {
                project: String::new(),
                version: String::new(),
                path,
                sha1,
                sha512,
                url,
                size,
                enabled: true,
                project_slug: String::new(),
                explicit: true,
                dependencies: None,
                incompatible: Vec::new(),
            });
        }
        Ok(Pack {
            name,
            minecraft,
            loader,
            loader_version,
            files,
        })
    })
}

fn python(args: &[&std::ffi::OsStr]) -> Result<()> {
    let program = if cfg!(windows) { "python" } else { "python3" };
    let output = Command::new(program)
        .args(["-I", "-c", ZIP_CODEC])
        .args(args)
        .output()
        .map_err(|e| {
            format!("Pack archives require system Python 3 (standard library only): {e}")
        })?;
    if !output.status.success() {
        let message = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "Archive operation failed: {}",
            message.chars().take(2048).collect::<String>().trim()
        ));
    }
    Ok(())
}

/// Import into a new instance using a private staging tree. Existing instances
/// are never replaced. Optional/client-unsupported downloads are omitted.
pub fn import_pack(app: &App, path: &Path, id: &str) -> Result<()> {
    import_pack_inner(app, path, id, None)
}
fn import_pack_inner(app: &App, path: &Path, id: &str, provenance: Option<&[u8]>) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let destination = app.dir(id)?;
    if destination.exists() {
        transaction::recover_locked(app, id)?;
        worlds::assert_stopped(app, id)?;
        return Err("Pack import requires a new instance ID".into());
    }
    let temp = Temp::new(&app.root)?;
    let extracted = temp.path.join("extracted");
    fs::create_dir(&extracted).map_err(|e| e.to_string())?;
    python(&["unpack".as_ref(), path.as_os_str(), extracted.as_os_str()])?;
    let pack = parse_pack(&util::read_limited(
        &extracted.join("modrinth.index.json"),
        MAX_LOCK,
    )?)?;
    let stage_root = temp.path.join("staged");
    fs::create_dir_all(stage_root.join("instances")).map_err(|e| e.to_string())?;
    let staged_app = App { root: stage_root };
    let mut instance =
        staged_app.create_instance(id, &pack.minecraft, &pack.loader, &pack.loader_version)?;
    instance.name = pack.name;
    staged_app.write_instance(&instance)?;
    let staged = staged_app.dir(id)?;
    let game = staged.join("game");
    fs::remove_dir(&game).map_err(|e| e.to_string())?;
    fs::rename(extracted.join("game"), &game).map_err(|e| e.to_string())?;
    for entry in &pack.files {
        let target = util::safe_join(&game, &entry.path)?;
        if target.exists() {
            return Err(format!(
                "Pack override conflicts with a downloaded path: {}",
                entry.path
            ));
        }
        let content = store::ensure(
            app,
            &format!("content/sha1/{}", entry.sha1),
            &entry.url,
            Some(&entry.sha1),
            None,
            Some(entry.size),
        )?;
        let immutable = entry.path.starts_with("mods/")
            || entry.path.starts_with("resourcepacks/")
            || entry.path.starts_with("shaderpacks/");
        store::materialize(&content, &target, immutable)?;
    }
    util::atomic_write(
        &staged.join("mods-lock.json"),
        lock_json(&pack.files).as_bytes(),
    )?;
    if let Some(provenance) = provenance {
        util::atomic_write(&staged.join("pack-source.json"), provenance)?;
    }
    if destination.exists() {
        return Err("Another operation created the destination instance".into());
    }
    fs::rename(staged, destination)
        .map_err(|e| format!("Cannot publish imported instance: {e}"))?;
    Ok(())
}

/// Export a standards-compliant `.mrpack`: URL references for managed content
/// and a conservative set of configuration overrides. Accounts, logs, saves,
/// launcher runtimes, unknown Mod JARs, and secret-bearing configs are excluded.
pub fn export_pack(app: &App, id: &str, path: &Path) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let instance = app.read_instance(id)?;
    transaction::recover_locked(app, id)?;
    worlds::assert_stopped(app, id)?;
    let dir = app.dir(id)?;
    util::check_pending_transactions(&dir)?;
    let game = util::safe_join(&dir, "game")?;
    let entries = read_lock(&dir)?;
    if path.exists() || fs::symlink_metadata(path).is_ok() {
        return Err("Export destination already exists".into());
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let temp = Temp::new(parent)?;
    let mut dependencies = vec![("minecraft", quote(&instance.version))];
    match instance.loader.as_str() {
        "vanilla" => (),
        "fabric" => dependencies.push(("fabric-loader", quote(&instance.loader_version))),
        "quilt" => dependencies.push(("quilt-loader", quote(&instance.loader_version))),
        "forge" => dependencies.push(("forge", quote(&instance.loader_version))),
        "neoforge" => dependencies.push(("neoforge", quote(&instance.loader_version))),
        _ => return Err("This loader is not supported by .mrpack export".into()),
    }
    let mut files = Vec::new();
    for entry in entries.iter().filter(|entry| entry.enabled) {
        verify_entry(&game, entry)?;
        files.push(util::object(&[
            ("path", quote(&entry.path)),
            ("hashes", util::object(&[("sha1", quote(&entry.sha1))])),
            ("downloads", format!("[{}]", quote(&entry.url))),
            ("fileSize", entry.size.to_string()),
        ]));
    }
    let plan = util::object(&[
        ("formatVersion", "1".into()),
        ("game", quote("minecraft")),
        ("versionId", quote("1.0.0")),
        ("name", quote(&instance.name)),
        ("dependencies", util::object(&dependencies)),
        ("files", format!("[{}]", files.join(","))),
    ]);
    let manifest = temp.path.join("plan.json");
    util::atomic_write(&manifest, plan.as_bytes())?;
    let output = temp.path.join("pack.mrpack");
    python(&[
        "export".as_ref(),
        game.as_os_str(),
        manifest.as_os_str(),
        output.as_os_str(),
    ])?;
    // The temporary file shares the destination filesystem. Hard-link creation
    // is an atomic no-replace publication, including for concurrent exporters.
    fs::hard_link(&output, path)
        .map_err(|e| format!("Cannot publish pack without replacing another file: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn download_origins_reject_credentials_ports_and_plaintext() {
        for value in [
            "http://cdn.modrinth.com/a.jar",
            "https://cdn.modrinth.com@evil.test/a",
            "https://cdn.modrinth.com:443/a",
            "https://localhost/a",
            "https://cdn.modrinth.com\\@evil.test/a",
            "https://github.com/white space",
        ] {
            assert!(content_url(value).is_err(), "{value}");
        }
        assert!(content_url("https://cdn.modrinth.com/data/project/version/mod.jar").is_ok());
    }
    #[test]
    fn paths_reject_traversal_and_cross_platform_ambiguity() {
        for value in [
            "../x",
            "/x",
            "a//b",
            "a/./b",
            "a/../../b",
            "C:/x",
            "a\\b",
            "CON.txt",
            "a/COM1",
            "x.",
        ] {
            assert!(portable_path(value).is_err(), "{value}");
        }
        assert!(portable_path("mods/test.jar").is_ok());
    }
    fn entry() -> Entry {
        Entry {
            project: "abc123".into(),
            version: "xyz789".into(),
            path: "mods/a.jar".into(),
            sha1: "0".repeat(40),
            sha512: None,
            url: "https://cdn.modrinth.com/a.jar".into(),
            size: 3,
            enabled: false,
            project_slug: "fixture".into(),
            explicit: true,
            dependencies: Some(Vec::new()),
            incompatible: Vec::new(),
        }
    }
    #[test]
    fn lock_roundtrip_and_duplicate_paths() {
        let item = entry();
        assert_eq!(
            parse_lock(lock_json(std::slice::from_ref(&item)).as_bytes()).unwrap(),
            vec![item.clone()]
        );
        assert!(parse_lock(lock_json(&[item.clone(), item]).as_bytes()).is_err());
    }
    #[test]
    fn pack_dependencies_are_not_silently_dropped() {
        for deps in [
            r#"{"minecraft":"1.20.1","unknown-loader":"1"}"#,
            r#"{"minecraft":"1.20.1","fabric-loader":"1","quilt-loader":"2"}"#,
        ] {
            let body = format!(
                r#"{{"formatVersion":1,"game":"minecraft","name":"test","files":[],"dependencies":{deps}}}"#
            );
            assert!(parse_pack(body.as_bytes()).is_err());
        }
        let pack = parse_pack(br#"{"formatVersion":1,"game":"minecraft","name":"test","files":[],"dependencies":{"minecraft":"1.20.1","fabric-loader":"0.16.0"}}"#).unwrap();
        assert_eq!(pack.loader, "fabric");
    }

    #[test]
    fn toggle_preserves_bytes_and_unmanaged_mods() {
        let temp = Temp::new(&std::env::temp_dir()).unwrap();
        let app = App::new(Some(temp.path.join("app").to_string_lossy().into_owned())).unwrap();
        app.create_instance("toggle", "1.20.1", "fabric", "0.16.0")
            .unwrap();
        let dir = app.dir("toggle").unwrap();
        let game = dir.join("game");
        let body = b"fixture bytes";
        let mut managed = entry();
        managed.enabled = true;
        managed.sha1 = zmcl_core::hash::sha1_hex(body);
        managed.size = body.len() as u64;
        util::atomic_write(&game.join(&managed.path), body).unwrap();
        util::atomic_write(&game.join("mods/unmanaged.jar"), b"preserve").unwrap();
        util::atomic_write(
            &dir.join("mods-lock.json"),
            lock_json(&[managed]).as_bytes(),
        )
        .unwrap();
        toggle(&app, "toggle", "abc123", false).unwrap();
        assert!(!game.join("mods/a.jar").exists());
        assert_eq!(fs::read(game.join("mods/a.jar.disabled")).unwrap(), body);
        assert_eq!(
            fs::read(game.join("mods/unmanaged.jar")).unwrap(),
            b"preserve"
        );
        assert!(!parse_lock(list(&app, "toggle").unwrap().as_bytes()).unwrap()[0].enabled);
        toggle(&app, "toggle", "a.jar", true).unwrap();
        assert_eq!(fs::read(game.join("mods/a.jar")).unwrap(), body);
        assert!(!game.join("mods/a.jar.disabled").exists());
        assert!(!dir.join(".content-journal.json").exists());
        assert!(fs::read_dir(&dir).unwrap().all(|x| {
            !x.unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".content-stage-")
        }));
    }

    #[test]
    fn mrpack_roundtrip_uses_verified_cache_and_preserves_existing_instance() {
        let temp = Temp::new(&std::env::temp_dir()).unwrap();
        let app = App::new(Some(temp.path.join("app").to_string_lossy().into_owned())).unwrap();
        app.create_instance("source", "1.20.1", "fabric", "0.16.0")
            .unwrap();
        let source = app.dir("source").unwrap();
        let game = source.join("game");
        let body = b"offline fixture jar bytes";
        let mut managed = entry();
        managed.enabled = true;
        managed.sha1 = zmcl_core::hash::sha1_hex(body);
        managed.size = body.len() as u64;
        let cache = app.shared().join(format!("content/sha1/{}", managed.sha1));
        util::atomic_write(&cache, body).unwrap();
        store::materialize(&cache, &game.join(&managed.path), true).unwrap();
        util::atomic_write(
            &source.join("mods-lock.json"),
            lock_json(&[managed]).as_bytes(),
        )
        .unwrap();
        util::atomic_write(&game.join("config/demo.toml"), b"show_menu = true").unwrap();
        util::atomic_write(
            &game.join("config/provider.json"),
            br#"{"api_key":"fixture-secret"}"#,
        )
        .unwrap();
        let archive = temp.path.join("fixture.mrpack");
        export_pack(&app, "source", &archive).unwrap();
        assert!(export_pack(&app, "source", &archive).is_err());
        import_pack(&app, &archive, "destination").unwrap();
        let imported = app.dir("destination").unwrap();
        assert_eq!(fs::read(imported.join("game/mods/a.jar")).unwrap(), body);
        assert_eq!(
            fs::read(imported.join("game/config/demo.toml")).unwrap(),
            b"show_menu = true"
        );
        assert!(!imported.join("game/config/provider.json").exists());
        let instance = app.read_instance("destination").unwrap();
        assert_eq!(instance.loader, "fabric");
        assert_eq!(instance.loader_version, "0.16.0");
        assert!(!instance.installed);
        assert!(import_pack(&app, &archive, "destination").is_err());
        assert_eq!(fs::read(imported.join("game/mods/a.jar")).unwrap(), body);
    }
}
