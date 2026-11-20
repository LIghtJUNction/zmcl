//! Modrinth resource-pack and shader ZIP management. Only explicitly tracked
//! archives are changed; game options and shader-loader installations are left
//! to their own commands and the game's settings.
use crate::{App, net, store, util, worlds};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use zmcl_core::json::{Kind as JsonKind, Value, get_str, quote, req_str, string, with_json};

mod catalog;
mod transaction;

type Result<T> = std::result::Result<T, String>;
const API: &str = "https://api.modrinth.com/v2";
const MAX_FILE: u64 = 512 * 1024 * 1024;
const MAX_LOCK: usize = 4 * 1024 * 1024;
const MAX_ENTRIES: usize = 1024;
const LOCK: &str = "resources-lock.json";
const JOURNAL: &str = ".resources-journal.json";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum Kind {
    ResourcePack,
    Shader,
}
impl Kind {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "resourcepack" => Ok(Self::ResourcePack),
            "shader" => Ok(Self::Shader),
            _ => Err("Resource kind must be 'resourcepack' or 'shader'".into()),
        }
    }
    fn name(self) -> &'static str {
        match self {
            Self::ResourcePack => "resourcepack",
            Self::Shader => "shader",
        }
    }
    fn folder(self) -> &'static str {
        match self {
            Self::ResourcePack => "resourcepacks",
            Self::Shader => "shaderpacks",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Requirement {
    project: Option<String>,
    version: Option<String>,
}
impl Requirement {
    fn json(&self) -> String {
        util::object(&[
            (
                "project_id",
                self.project
                    .as_ref()
                    .map_or_else(|| "null".into(), |s| quote(s)),
            ),
            (
                "version_id",
                self.version
                    .as_ref()
                    .map_or_else(|| "null".into(), |s| quote(s)),
            ),
        ])
    }
    fn parse(value: Value<'_, '_>) -> Result<Self> {
        let project = get_str(value, "project_id");
        let version = get_str(value, "version_id");
        if project.is_none() && version.is_none() {
            return Err("Required dependency has no project or version identifier".into());
        }
        for id in project.iter().chain(version.iter()) {
            slug(id)?;
        }
        Ok(Self { project, version })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Entry {
    kind: Kind,
    project: String,
    slug: String,
    title: String,
    version: String,
    number: String,
    published: String,
    game: String,
    filename: String,
    sha1: String,
    size: u64,
    url: String,
    enabled: bool,
    loaders: Vec<String>,
    requirements: Vec<Requirement>,
}
impl Entry {
    fn key(&self) -> (Kind, String) {
        (self.kind, self.project.clone())
    }
    fn path(&self) -> String {
        format!("{}/{}", self.kind.folder(), self.filename)
    }
    fn actual_path(&self) -> String {
        format!(
            "{}{}",
            self.path(),
            if self.enabled { "" } else { ".disabled" }
        )
    }
    fn json(&self) -> String {
        util::object(&[
            ("kind", quote(self.kind.name())),
            ("project_id", quote(&self.project)),
            ("slug", quote(&self.slug)),
            ("title", quote(&self.title)),
            ("version_id", quote(&self.version)),
            ("version_number", quote(&self.number)),
            ("published", quote(&self.published)),
            ("game_version", quote(&self.game)),
            ("filename", quote(&self.filename)),
            ("path", quote(&self.path())),
            ("actual_path", quote(&self.actual_path())),
            ("sha1", quote(&self.sha1)),
            ("size", self.size.to_string()),
            ("url", quote(&self.url)),
            ("enabled", self.enabled.to_string()),
            ("loaders", strings_json(&self.loaders)),
            (
                "requirements",
                format!(
                    "[{}]",
                    self.requirements
                        .iter()
                        .map(Requirement::json)
                        .collect::<Vec<_>>()
                        .join(",")
                ),
            ),
        ])
    }
    fn parse(v: Value<'_, '_>) -> Result<Self> {
        let kind = Kind::parse(&req_str(v, "kind")?)?;
        let project = req_str(v, "project_id")?;
        let project_slug = req_str(v, "slug")?;
        let version = req_str(v, "version_id")?;
        for id in [&project, &project_slug, &version] {
            slug(id)?;
        }
        let filename = req_str(v, "filename")?;
        valid_filename(&filename)?;
        let sha1 = req_str(v, "sha1")?.to_ascii_lowercase();
        if !zmcl_core::path::valid_sha1(&sha1) {
            return Err("Invalid resource SHA-1".into());
        }
        let size = v
            .get("size")
            .and_then(Value::as_u64)
            .ok_or("Missing resource size")?;
        if size == 0 || size > MAX_FILE {
            return Err("Resource archive must contain 1 byte to 512 MiB".into());
        }
        let url = req_str(v, "url")?;
        content_url(&url)?;
        let title = label(v, "title", 512)?;
        let number = label(v, "version_number", 256)?;
        let published = label(v, "published", 64)?;
        let game = req_str(v, "game_version")?;
        game_version(&game)?;
        let enabled = v
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or("Missing enabled state")?;
        let loaders = string_array(v, "loaders", 32)?;
        let dependencies = v
            .get("requirements")
            .ok_or("Missing resource requirements")?;
        if dependencies.kind() != JsonKind::Array {
            return Err("Resource requirements must be an array".into());
        }
        let mut requirements = Vec::new();
        for dependency in dependencies.array() {
            if requirements.len() >= 128 {
                return Err("Too many required resource dependencies".into());
            }
            requirements.push(Requirement::parse(dependency)?);
        }
        let entry = Self {
            kind,
            project,
            slug: project_slug,
            title,
            version,
            number,
            published,
            game,
            filename,
            sha1,
            size,
            url,
            enabled,
            loaders,
            requirements,
        };
        if req_str(v, "path")? != entry.path() || req_str(v, "actual_path")? != entry.actual_path()
        {
            return Err("Resource lock path does not match its kind and filename".into());
        }
        Ok(entry)
    }
}

fn strings_json(values: &[String]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(|s| quote(s))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn label(v: Value<'_, '_>, name: &str, max: usize) -> Result<String> {
    let value = req_str(v, name)?;
    if value.len() > max || value.chars().any(char::is_control) {
        return Err(format!("Invalid resource {name}"));
    }
    Ok(value)
}
fn string_array(v: Value<'_, '_>, name: &str, max: usize) -> Result<Vec<String>> {
    let values = v.get(name).ok_or_else(|| format!("Missing {name}"))?;
    if values.kind() != JsonKind::Array {
        return Err(format!("{name} must be an array"));
    }
    let mut result = Vec::new();
    for value in values.array() {
        let item = string(value).ok_or_else(|| format!("Invalid {name} value"))?;
        if result.len() >= max || item.len() > 128 || item.chars().any(char::is_control) {
            return Err(format!("{name} exceeds its budget"));
        }
        result.push(item);
    }
    Ok(result)
}
fn slug(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
    {
        return Err(
            "Project identifiers must contain 1–128 ASCII letters, digits, '-' or '_'".into(),
        );
    }
    Ok(())
}
fn game_version(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.+".contains(&b))
    {
        return Err("Invalid Minecraft version filter".into());
    }
    Ok(())
}
fn valid_filename(value: &str) -> Result<()> {
    if value.len() > 240 || value.contains('/') || !value.to_ascii_lowercase().ends_with(".zip") {
        return Err("Managed resources must use a portable, single-component ZIP filename".into());
    }
    zmcl_core::path::validate_relative(value)
}
fn content_url(value: &str) -> Result<()> {
    if !value.starts_with("https://cdn.modrinth.com/data/")
        || value.contains('\\')
        || value.contains('#')
        || value.chars().any(|c| c.is_control() || c.is_whitespace())
    {
        return Err("Resource archives must use the official HTTPS Modrinth CDN".into());
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
    with_json(bytes, |root| {
        if root.get("format").and_then(Value::as_u64) != Some(1) {
            return Err("Unsupported resources lock format".into());
        }
        let values = root
            .get("entries")
            .ok_or("Missing resources lock entries")?;
        if values.kind() != JsonKind::Array {
            return Err("Resources lock entries must be an array".into());
        }
        let mut entries = Vec::new();
        let mut projects = BTreeSet::new();
        let mut paths = BTreeSet::new();
        for value in values.array() {
            if entries.len() >= MAX_ENTRIES {
                return Err("Resources lock exceeds 1,024 managed packs".into());
            }
            let entry = Entry::parse(value)?;
            if !projects.insert(entry.key()) || !paths.insert(entry.path().to_lowercase()) {
                return Err("Duplicate project or path in resources lock".into());
            }
            entries.push(entry);
        }
        Ok(entries)
    })
}
fn read_lock(dir: &Path) -> Result<(Vec<Entry>, Option<Vec<u8>>)> {
    let path = util::safe_join(dir, LOCK)?;
    if !path.exists() {
        return Ok((Vec::new(), None));
    }
    let raw = util::read_limited(&path, MAX_LOCK)?;
    Ok((parse_lock(&raw)?, Some(raw)))
}
fn game_dir(dir: &Path) -> Result<PathBuf> {
    util::safe_join(dir, "game")
}
fn file_matches(path: &Path, entry: &Entry) -> Result<bool> {
    let info = match fs::symlink_metadata(path) {
        Ok(info) => info,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.to_string()),
    };
    if !info.file_type().is_file() || info.len() != entry.size {
        return Ok(false);
    }
    let (sha1, _, size) = store::digests(path)?;
    Ok(sha1 == entry.sha1 && size == entry.size)
}
fn verify_entry(game: &Path, entry: &Entry) -> Result<PathBuf> {
    let path = util::safe_join(game, &entry.actual_path())?;
    if !file_matches(&path, entry)? {
        return Err(format!(
            "Managed resource is missing or was changed by the user: {}; preserve it and resolve the mismatch before changing this pack",
            entry.actual_path()
        ));
    }
    Ok(path)
}
fn same_owned_file(a: &Path, b: &Path) -> Result<bool> {
    let a = fs::symlink_metadata(a).map_err(|e| e.to_string())?;
    let b = fs::symlink_metadata(b).map_err(|e| e.to_string())?;
    if !a.is_file() || !b.is_file() {
        return Ok(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(a.dev() == b.dev() && a.ino() == b.ino())
    }
    #[cfg(not(unix))]
    {
        // Portable metadata alone cannot establish file identity. Recovery will
        // preserve ambiguous files instead of removing a possibly manual file.
        let _ = (a, b);
        Err("Automatic rollback needs an unambiguous file identity; this platform preserves both files for recovery".into())
    }
}

/// Search exact Modrinth project types with an optional Minecraft version facet.
pub fn search(kind: &str, query: &str, game: Option<&str>) -> Result<String> {
    let kind = Kind::parse(kind)?;
    if query.len() > 512 || query.chars().any(char::is_control) {
        return Err("Resource search query exceeds its budget".into());
    }
    let mut facets = vec![format!(
        "[{}]",
        quote(&format!("project_type:{}", kind.name()))
    )];
    if let Some(game) = game {
        game_version(game)?;
        facets.push(format!("[{}]", quote(&format!("versions:{game}"))));
    }
    let url = format!(
        "{API}/search?query={}&limit=20&facets={}",
        net::encode(query),
        net::encode(&format!("[{}]", facets.join(",")))
    );
    let body = net::get(&url)?;
    with_json(&body, |root| {
        let hits = root.get("hits").ok_or("Modrinth search lacks hits")?;
        if hits.kind() != JsonKind::Array {
            return Err("Modrinth search hits must be an array".into());
        }
        for hit in hits.array() {
            if get_str(hit, "project_type").as_deref() != Some(kind.name()) {
                return Err("Modrinth search returned a mismatched project type".into());
            }
            if let Some(game) = game
                && !hit
                    .get("versions")
                    .is_some_and(|v| v.array().any(|x| string(x).as_deref() == Some(game)))
            {
                return Err("Modrinth search returned an incompatible Minecraft version".into());
            }
        }
        Ok(())
    })?;
    String::from_utf8(body).map_err(|_| "Modrinth search returned invalid UTF-8".into())
}

struct Project {
    id: String,
    slug: String,
    title: String,
    kind: Kind,
}
fn project(kind: Kind, selector: &str) -> Result<Project> {
    slug(selector)?;
    with_json(&net::get(&format!("{API}/project/{selector}"))?, |v| {
        if get_str(v, "project_type").as_deref() != Some(kind.name()) {
            return Err(format!("Selected project is not a {}", kind.name()));
        }
        let id = req_str(v, "id")?;
        let project_slug = req_str(v, "slug")?;
        slug(&id)?;
        slug(&project_slug)?;
        Ok(Project {
            id,
            slug: project_slug,
            title: label(v, "title", 512)?,
            kind,
        })
    })
}
fn supports(v: Value<'_, '_>, game: &str, kind: Kind) -> bool {
    let game_ok = v.get("game_versions").is_some_and(|values| {
        values
            .array()
            .any(|value| string(value).as_deref() == Some(game))
    });
    let loader_ok = kind == Kind::Shader
        || v.get("loaders").is_some_and(|values| {
            values
                .array()
                .any(|value| string(value).as_deref() == Some("minecraft"))
        });
    let status_ok = get_str(v, "status").is_none_or(|status| status == "listed");
    game_ok && loader_ok && status_ok
}
fn version(v: Value<'_, '_>, project: &Project, game: &str) -> Result<Entry> {
    if !supports(v, game, project.kind) || req_str(v, "project_id")? != project.id {
        return Err("Selected pack version does not match its project or Minecraft version".into());
    }
    let id = req_str(v, "id")?;
    slug(&id)?;
    let files = v.get("files").ok_or("Pack version has no files")?;
    if files.kind() != JsonKind::Array {
        return Err("Pack files must be an array".into());
    }
    let mut primary = None;
    let mut first = None;
    let mut count = 0;
    for file in files.array() {
        count += 1;
        if count > 128 {
            return Err("Pack version contains too many files".into());
        }
        if first.is_none() {
            first = Some(file);
        }
        if file.get("primary").and_then(Value::as_bool) == Some(true) {
            if primary.is_some() {
                return Err("Pack version contains multiple primary files".into());
            }
            primary = Some(file);
        }
    }
    let file = primary
        .or(first)
        .ok_or("Pack version has no downloadable file")?;
    let filename = req_str(file, "filename")?;
    valid_filename(&filename)?;
    let hashes = file.get("hashes").ok_or("Pack file has no hashes")?;
    let sha1 = req_str(hashes, "sha1")?.to_ascii_lowercase();
    if !zmcl_core::path::valid_sha1(&sha1) {
        return Err("Pack file lacks a trusted SHA-1".into());
    }
    let size = file
        .get("size")
        .and_then(Value::as_u64)
        .ok_or("Pack file has no size")?;
    if size == 0 || size > MAX_FILE {
        return Err("Pack file must contain 1 byte to 512 MiB".into());
    }
    let url = req_str(file, "url")?;
    content_url(&url)?;
    let loaders = string_array(v, "loaders", 32)?;
    if loaders.is_empty() {
        return Err("Pack version has no supported loader tags".into());
    }
    let mut requirements = Vec::new();
    if let Some(dependencies) = v.get("dependencies") {
        if dependencies.kind() != JsonKind::Array {
            return Err("Invalid pack dependency list".into());
        }
        for dep in dependencies.array() {
            if get_str(dep, "dependency_type").as_deref() == Some("required") {
                if requirements.len() >= 128 {
                    return Err("Pack declares too many required dependencies".into());
                }
                requirements.push(Requirement::parse(dep)?);
            }
        }
    }
    Ok(Entry {
        kind: project.kind,
        project: project.id.clone(),
        slug: project.slug.clone(),
        title: project.title.clone(),
        version: id,
        number: label(v, "version_number", 256)?,
        published: label(v, "date_published", 64)?,
        game: game.to_owned(),
        filename,
        sha1,
        size,
        url,
        enabled: true,
        loaders,
        requirements,
    })
}
fn latest(kind: Kind, selector: &str, game: &str) -> Result<Entry> {
    game_version(game)?;
    let project = project(kind, selector)?;
    let mut url = format!(
        "{API}/project/{}/version?game_versions={}&include_changelog=false",
        project.id,
        net::encode(&format!("[{}]", quote(game)))
    );
    if kind == Kind::ResourcePack {
        url.push_str("&loaders=%5B%22minecraft%22%5D");
    }
    with_json(&net::get(&url)?, |root| {
        if root.kind() != JsonKind::Array {
            return Err("Modrinth versions response must be an array".into());
        }
        let selected = root
            .array()
            .filter(|v| supports(*v, game, kind))
            .max_by_key(|v| {
                (
                    get_str(*v, "version_type").as_deref() == Some("release"),
                    get_str(*v, "date_published").unwrap_or_default(),
                )
            })
            .ok_or("No compatible resource version found")?;
        version(selected, &project, game)
    })
}
fn download(app: &App, entry: &Entry) -> Result<PathBuf> {
    let path = store::ensure(
        app,
        &format!("content/sha1/{}", entry.sha1),
        &entry.url,
        Some(&entry.sha1),
        None,
        Some(entry.size),
    )?;
    catalog::validate(&path, entry.kind)?;
    Ok(path)
}
fn find(entries: &[Entry], kind: Kind, selector: &str) -> Result<usize> {
    slug(selector)?;
    let matches: Vec<_> = entries
        .iter()
        .enumerate()
        .filter(|(_, entry)| {
            entry.kind == kind && (entry.project == selector || entry.slug == selector)
        })
        .map(|(index, _)| index)
        .collect();
    if matches.len() != 1 {
        return Err("Select exactly one managed pack by Modrinth project ID or slug".into());
    }
    Ok(matches[0])
}
fn check_other_transactions(dir: &Path) -> Result<()> {
    for (file, recovery) in [
        (".content-journal.json", "content recovery"),
        (".backup-restore.json", "backup restore"),
    ] {
        if util::safe_join(dir, file)?.exists() {
            return Err(format!(
                "An interrupted {recovery} transaction must be resolved before managing resource packs"
            ));
        }
    }
    Ok(())
}
fn begin(app: &App, id: &str) -> Result<(crate::app::Instance, PathBuf)> {
    let instance = app.read_instance(id)?;
    worlds::assert_stopped(app, id)?;
    let dir = app.dir(id)?;
    check_other_transactions(&dir)?;
    transaction::recover(&dir)?;
    Ok((instance, dir))
}
fn result(changed: usize, history: Option<&str>, entries: &[Entry]) -> String {
    util::object(&[
        ("changed", changed.to_string()),
        ("history", history.map_or_else(|| "null".into(), quote)),
        (
            "entries",
            format!(
                "[{}]",
                entries
                    .iter()
                    .map(Entry::json)
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        ),
        ("options_changed", "false".into()),
        ("shader_loader_installed_by_this_command", "false".into()),
        (
            "required_dependencies_installed_by_this_command",
            "false".into(),
        ),
    ])
}

/// List managed packs and untouched manual files. Hashes are checked before
/// mutation; listing uses metadata and reports integrity:"not_checked".
pub fn list(app: &App, id: &str, kind: Option<&str>) -> Result<String> {
    let filter = kind.map(Kind::parse).transpose()?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    app.read_instance(id)?;
    let dir = app.dir(id)?;
    check_other_transactions(&dir)?;
    let game = game_dir(&dir)?;
    let (entries, _) = read_lock(&dir)?;
    let mut rows = Vec::new();
    let mut managed = BTreeSet::new();
    for entry in &entries {
        managed.insert(entry.actual_path());
        if filter.is_some_and(|kind| kind != entry.kind) {
            continue;
        }
        let folder = util::safe_join(&game, entry.kind.folder())?;
        let path = folder.join(format!(
            "{}{}",
            entry.filename,
            if entry.enabled { "" } else { ".disabled" }
        ));
        let integrity = match fs::symlink_metadata(&path) {
            Ok(meta) if !meta.is_file() => "unsafe",
            Ok(meta) if meta.len() != entry.size => "size_mismatch",
            Ok(_) => "not_checked",
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => "missing",
            Err(_) => "unreadable",
        };
        let mut row = entry.json();
        row.pop();
        row.push_str(&format!(
            ",\"managed\":true,\"integrity\":{}}}",
            quote(integrity)
        ));
        rows.push(row);
    }
    let mut manual = Vec::new();
    for kind in [Kind::ResourcePack, Kind::Shader] {
        if filter.is_some_and(|filter| filter != kind) {
            continue;
        }
        let folder = util::safe_join(&game, kind.folder())?;
        if !folder.exists() {
            continue;
        }
        for item in fs::read_dir(&folder).map_err(|e| e.to_string())? {
            if manual.len() >= 10000 {
                return Err("Resource folder exceeds the 10,000 entry listing budget".into());
            }
            let item = item.map_err(|e| e.to_string())?;
            let path = format!("{}/{}", kind.folder(), item.file_name().to_string_lossy());
            if managed.contains(&path) {
                continue;
            }
            let metadata = fs::symlink_metadata(item.path()).map_err(|e| e.to_string())?;
            manual.push(util::object(&[
                ("kind", quote(kind.name())),
                ("path", quote(&path)),
                ("managed", "false".into()),
                ("directory", metadata.is_dir().to_string()),
                ("symlink", metadata.file_type().is_symlink().to_string()),
                (
                    "size",
                    if metadata.is_file() {
                        metadata.len().to_string()
                    } else {
                        "null".into()
                    },
                ),
            ]));
        }
    }
    Ok(util::object(&[
        ("format", "1".into()),
        ("entries", format!("[{}]", rows.join(","))),
        ("unmanaged", format!("[{}]", manual.join(","))),
        (
            "recovery_pending",
            util::safe_join(&dir, JOURNAL)?.exists().to_string(),
        ),
    ]))
}

/// Add one verified archive. Required project dependencies are reported in the
/// result; this command neither installs shader engines nor edits game options.
pub fn add(app: &App, id: &str, kind: &str, selector: &str) -> Result<String> {
    let kind = Kind::parse(kind)?;
    slug(selector)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (instance, dir) = begin(app, id)?;
    let (old, bytes) = read_lock(&dir)?;
    let selected = latest(kind, selector, &instance.version)?;
    if let Some(existing) = old.iter().find(|entry| entry.key() == selected.key()) {
        verify_entry(&game_dir(&dir)?, existing)?;
        return Ok(result(0, None, &old));
    }
    let mut new = old.clone();
    new.push(selected.clone());
    let sources = BTreeMap::from([(selected.key(), download(app, &selected)?)]);
    worlds::assert_stopped(app, id)?;
    let history = transaction::commit(&dir, &old, bytes.as_deref(), &new, &sources)?;
    Ok(result(1, Some(&history), &new))
}

/// Update managed packs to the newest compatible release (or newest beta/alpha
/// when no release exists). A compatible installed version is never downgraded.
pub fn update(app: &App, id: &str, kind: Option<&str>, selector: Option<&str>) -> Result<String> {
    let filter = kind.map(Kind::parse).transpose()?;
    if let Some(selector) = selector {
        slug(selector)?;
    }
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (instance, dir) = begin(app, id)?;
    let (old, bytes) = read_lock(&dir)?;
    let selected: Vec<_> = old
        .iter()
        .enumerate()
        .filter(|(_, entry)| {
            filter.is_none_or(|kind| entry.kind == kind)
                && selector
                    .is_none_or(|selector| entry.project == selector || entry.slug == selector)
        })
        .map(|(index, _)| index)
        .collect();
    if selector.is_some() && selected.len() != 1 {
        return Err("Select exactly one managed resource project".into());
    }
    let game = game_dir(&dir)?;
    let mut new = old.clone();
    let mut sources = BTreeMap::new();
    for index in selected {
        let existing = &old[index];
        verify_entry(&game, existing)?;
        let mut next = latest(existing.kind, &existing.project, &instance.version)?;
        if next.version == existing.version
            || (existing.game == instance.version && next.published <= existing.published)
        {
            continue;
        }
        next.enabled = existing.enabled;
        sources.insert(next.key(), download(app, &next)?);
        new[index] = next;
    }
    if sources.is_empty() {
        return Ok(result(0, None, &old));
    }
    worlds::assert_stopped(app, id)?;
    let history = transaction::commit(&dir, &old, bytes.as_deref(), &new, &sources)?;
    Ok(result(sources.len(), Some(&history), &new))
}

/// Remove only an unchanged managed archive, retaining its previous bytes and
/// lock snapshot in resources-history for recovery.
pub fn remove(app: &App, id: &str, kind: &str, selector: &str) -> Result<String> {
    let kind = Kind::parse(kind)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (_, dir) = begin(app, id)?;
    let (old, bytes) = read_lock(&dir)?;
    let index = find(&old, kind, selector)?;
    verify_entry(&game_dir(&dir)?, &old[index])?;
    let mut new = old.clone();
    new.remove(index);
    worlds::assert_stopped(app, id)?;
    let history = transaction::commit(&dir, &old, bytes.as_deref(), &new, &BTreeMap::new())?;
    Ok(result(1, Some(&history), &new))
}

/// Toggle archive availability by adding/removing ".disabled". This does not
/// change Minecraft's selected resource-pack list or shader configuration.
pub fn toggle(app: &App, id: &str, kind: &str, selector: &str, enabled: bool) -> Result<String> {
    let kind = Kind::parse(kind)?;
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (_, dir) = begin(app, id)?;
    let (old, bytes) = read_lock(&dir)?;
    let index = find(&old, kind, selector)?;
    let source = verify_entry(&game_dir(&dir)?, &old[index])?;
    if old[index].enabled == enabled {
        return Ok(result(0, None, &old));
    }
    let mut new = old.clone();
    new[index].enabled = enabled;
    let sources = BTreeMap::from([(new[index].key(), source)]);
    worlds::assert_stopped(app, id)?;
    let history = transaction::commit(&dir, &old, bytes.as_deref(), &new, &sources)?;
    Ok(result(1, Some(&history), &new))
}

/// Resolve only this module's interrupted transaction. Ambiguous files are
/// retained with the journal instead of being overwritten or deleted.
pub fn recover(app: &App, id: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    app.read_instance(id)?;
    worlds::assert_stopped(app, id)?;
    let dir = app.dir(id)?;
    check_other_transactions(&dir)?;
    transaction::recover(&dir)
}

/// List retained resource revisions and their recovery directories.
pub fn history(app: &App, id: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    app.read_instance(id)?;
    let dir = app.dir(id)?;
    check_other_transactions(&dir)?;
    transaction::history(&dir)
}

/// Undo a completed resource revision while preserving the revision it
/// replaces. Refuses if later managed changes would otherwise be discarded.
pub fn restore(app: &App, id: &str, revision: &str) -> Result<String> {
    util::valid_id(id)?;
    let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (_, dir) = begin(app, id)?;
    let (old, _) = read_lock(&dir)?;
    let (history, restored) = transaction::restore(&dir, revision)?;
    let keys: BTreeSet<_> = old.iter().chain(restored.iter()).map(Entry::key).collect();
    let changed = keys
        .iter()
        .filter(|key| {
            old.iter().find(|entry| entry.key() == **key)
                != restored.iter().find(|entry| entry.key() == **key)
        })
        .count();
    Ok(result(changed, Some(&history), &restored))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn project_fixture() -> Project {
        Project {
            id: "Project1".into(),
            slug: "tiny-pack".into(),
            title: "Tiny pack".into(),
            kind: Kind::ResourcePack,
        }
    }
    fn version_json(filename: &str, game: &str, project: &str, url: &str) -> String {
        format!(
            r#"{{"id":"Version1","project_id":{},"game_versions":[{}],"loaders":["minecraft"],"status":"listed","version_type":"release","version_number":"1.0","date_published":"2026-01-01T00:00:00Z","dependencies":[],"files":[{{"primary":true,"filename":{},"size":100,"hashes":{{"sha1":"0123456789012345678901234567890123456789"}},"url":{}}}]}}"#,
            quote(project),
            quote(game),
            quote(filename),
            quote(url)
        )
    }
    #[test]
    fn compatible_pack_version_and_lock_roundtrip() {
        let raw = version_json(
            "tiny.zip",
            "1.21.1",
            "Project1",
            "https://cdn.modrinth.com/data/Project1/tiny.zip",
        );
        let entry = with_json(raw.as_bytes(), |value| {
            version(value, &project_fixture(), "1.21.1")
        })
        .unwrap();
        let encoded = lock_json(std::slice::from_ref(&entry));
        assert_eq!(parse_lock(encoded.as_bytes()).unwrap(), vec![entry]);
    }
    #[test]
    fn rejects_mismatched_projects_games_hosts_and_paths() {
        for (name, game, project, url) in [
            (
                "../tiny.zip",
                "1.21.1",
                "Project1",
                "https://cdn.modrinth.com/data/x",
            ),
            (
                "tiny.zip",
                "1.20.1",
                "Project1",
                "https://cdn.modrinth.com/data/x",
            ),
            (
                "tiny.zip",
                "1.21.1",
                "Other",
                "https://cdn.modrinth.com/data/x",
            ),
            (
                "tiny.zip",
                "1.21.1",
                "Project1",
                "https://cdn.modrinth.com.evil.test/data/x",
            ),
            (
                "tiny.jar",
                "1.21.1",
                "Project1",
                "https://cdn.modrinth.com/data/x",
            ),
        ] {
            let raw = version_json(name, game, project, url);
            assert!(
                with_json(raw.as_bytes(), |value| version(
                    value,
                    &project_fixture(),
                    "1.21.1"
                ))
                .is_err()
            );
        }
    }
    #[test]
    fn rejects_duplicate_lock_ownership_and_unsafe_identifiers() {
        for value in ["", "../a", "a/b", "a.zip", "a?x=1"] {
            assert!(slug(value).is_err());
        }
        let raw = version_json(
            "tiny.zip",
            "1.21.1",
            "Project1",
            "https://cdn.modrinth.com/data/x",
        );
        let entry = with_json(raw.as_bytes(), |value| {
            version(value, &project_fixture(), "1.21.1")
        })
        .unwrap();
        assert!(parse_lock(lock_json(&[entry.clone(), entry]).as_bytes()).is_err());
    }
}
