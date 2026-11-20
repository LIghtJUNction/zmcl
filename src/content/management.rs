use super::*;
use std::time::SystemTime;

const MAX_PROJECTS: usize = 512;
#[derive(Clone)]
struct Root {
    exact: bool,
    id: String,
    enabled: bool,
    explicit: bool,
}
fn cached_version(entry: &Entry) -> Result<Version> {
    Ok(Version {
        entry: entry.clone(),
        dependencies: entry
            .dependencies
            .clone()
            .ok_or("Missing dependency metadata")?,
        incompatible: entry.incompatible.clone(),
    })
}
fn is_mod(entry: &Entry) -> bool {
    entry.path.starts_with("mods/") && entry.path.to_ascii_lowercase().ends_with(".jar")
}
fn identified(entry: &Entry) -> bool {
    is_mod(entry) && !entry.project.is_empty() && !entry.version.is_empty()
}
fn project_details(mut selected: Version) -> Result<Version> {
    with_json(
        &net::get(&format!("{API}/project/{}", selected.entry.project))?,
        |value| {
            if get_str(value, "project_type").as_deref() != Some("mod") || !client_supported(value)
            {
                return Err("Required project is not a client Mod".into());
            }
            if req_str(value, "id")? != selected.entry.project {
                return Err("Modrinth project identity changed unexpectedly".into());
            }
            selected.entry.project_slug = req_str(value, "slug")?;
            Ok(())
        },
    )?;
    Ok(selected)
}
fn hydrate(entries: &[Entry], game: &str, loader: &str) -> Result<Vec<Entry>> {
    let mut result = Vec::new();
    let mut count = 0;
    for old in entries {
        if !is_mod(old) || old.dependencies.is_some() {
            result.push(old.clone());
            continue;
        }
        count += 1;
        if count > MAX_PROJECTS {
            return Err("Dependency metadata exceeds 512 projects".into());
        }
        let mut selected = if !old.project.is_empty() && !old.version.is_empty() {
            fetch_version(true, &old.version, game, loader)?
        } else if old.project.is_empty() && old.version.is_empty() {
            let response = net::request(
                &format!("{API}/version_file/{}?algorithm=sha1", old.sha1),
                "GET",
                &[],
                None,
            )?;
            if response.status == 404 {
                result.push(old.clone());
                continue;
            }
            if response.status != 200 {
                return Err(format!(
                    "Modrinth hash lookup returned HTTP {}",
                    response.status
                ));
            }
            project_details(with_json(&response.body, |value| {
                version_file(value, game, loader, Some(&old.sha1))
            })?)?
        } else {
            return Err("Incomplete managed Mod identity in lock file".into());
        };
        if (!old.project.is_empty() && selected.entry.project != old.project)
            || selected.entry.sha1 != old.sha1
            || selected.entry.size != old.size
        {
            return Err(format!(
                "Locked bytes do not match Modrinth metadata for {}",
                old.path
            ));
        }
        selected.entry.path = old.path.clone();
        selected.entry.enabled = old.enabled;
        selected.entry.explicit = old.explicit;
        result.push(selected.entry);
    }
    Ok(result)
}
struct Repository<'a> {
    game: &'a str,
    loader: &'a str,
    preferred: BTreeMap<String, Version>,
    cache: BTreeMap<(bool, String), Version>,
    update: BTreeSet<String>,
    update_all: bool,
}
impl<'a> Repository<'a> {
    fn new(
        game: &'a str,
        loader: &'a str,
        entries: &[Entry],
        update: BTreeSet<String>,
        update_all: bool,
    ) -> Result<Self> {
        let mut preferred = BTreeMap::new();
        let mut cache = BTreeMap::new();
        for entry in entries.iter().filter(|entry| identified(entry)) {
            let version = cached_version(entry)?;
            if preferred
                .insert(entry.project.clone(), version.clone())
                .is_some()
            {
                return Err("Two managed files claim the same Modrinth project".into());
            }
            cache.insert((true, entry.version.clone()), version);
        }
        Ok(Self {
            game,
            loader,
            preferred,
            cache,
            update,
            update_all,
        })
    }
    fn fetch(&mut self, exact: bool, id: &str) -> Result<Version> {
        if let Some(version) = self.cache.get(&(exact, id.to_owned())) {
            return Ok(version.clone());
        }
        if !exact
            && !self.update_all
            && !self.update.contains(id)
            && let Some(version) = self.preferred.get(id)
        {
            return Ok(version.clone());
        }
        if self.cache.len() > MAX_PROJECTS * 3 {
            return Err("Mod metadata cache exceeds its budget".into());
        }
        let mut version = fetch_version(exact, id, self.game, self.loader)?;
        if exact && version.entry.version != id {
            return Err("Modrinth returned a different version ID".into());
        }
        if !exact
            && !self.update_all
            && !self.update.contains(&version.entry.project)
            && let Some(existing) = self.preferred.get(&version.entry.project)
        {
            version = existing.clone();
        }
        self.cache.insert((exact, id.to_owned()), version.clone());
        self.cache
            .insert((true, version.entry.version.clone()), version.clone());
        if !exact {
            self.cache
                .insert((false, version.entry.project.clone()), version.clone());
        }
        Ok(version)
    }
}
fn dependency_target<'a>(entries: &'a [Entry], dependency: &(bool, String)) -> Option<&'a Entry> {
    entries.iter().find(|entry| {
        identified(entry)
            && if dependency.0 {
                entry.version == dependency.1
            } else {
                entry.project == dependency.1
            }
    })
}
fn related_projects(entries: &[Entry], project: &str) -> BTreeSet<String> {
    let mut related = BTreeSet::new();
    let mut queue = VecDeque::from([project.to_owned()]);
    while let Some(project) = queue.pop_front() {
        if !related.insert(project.clone()) {
            continue;
        }
        if let Some(entry) = entries.iter().find(|entry| entry.project == project) {
            for dependency in entry.dependencies.iter().flatten() {
                if let Some(target) = dependency_target(entries, dependency) {
                    queue.push_back(target.project.clone());
                }
            }
        }
    }
    related
}
fn roots(entries: &[Entry], updates: &BTreeSet<String>, update_all: bool) -> Vec<Root> {
    let mut reached = BTreeSet::new();
    for entry in entries
        .iter()
        .filter(|entry| identified(entry) && entry.explicit)
    {
        reached.extend(related_projects(entries, &entry.project));
    }
    entries
        .iter()
        .filter(|entry| identified(entry) && (entry.explicit || !reached.contains(&entry.project)))
        .map(|entry| Root {
            exact: !(update_all || updates.contains(&entry.project)),
            id: if update_all || updates.contains(&entry.project) {
                entry.project.clone()
            } else {
                entry.version.clone()
            },
            enabled: entry.enabled,
            explicit: entry.explicit,
        })
        .collect()
}

/// Rebuilds the graph from its roots on each bounded pin-propagation pass.
/// Exact version dependencies override a latest-version preference. Two actual
/// required versions or declared incompatible projects fail before publication.
fn resolve_roots<F>(roots: &[Root], old: &[Entry], mut fetch: F) -> Result<Vec<Entry>>
where
    F: FnMut(bool, &str) -> Result<Version>,
{
    let mut previous_pins: BTreeMap<String, String> = BTreeMap::new();
    let mut seen_plans = BTreeSet::new();
    for _ in 0..32 {
        let mut selected: BTreeMap<String, Version> = BTreeMap::new();
        let mut constraints: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        let mut root_flags: BTreeMap<String, (bool, bool)> = BTreeMap::new();
        let mut queue = VecDeque::new();
        for root in roots {
            let version = fetch(root.exact, &root.id)?;
            root_flags
                .entry(version.entry.project.clone())
                .and_modify(|flags| {
                    flags.0 |= root.enabled;
                    flags.1 |= root.explicit;
                })
                .or_insert((root.enabled, root.explicit));
            queue.push_back((root.exact, root.id.clone(), 0usize));
        }
        let mut requested = BTreeSet::new();
        while let Some((exact, id, depth)) = queue.pop_front() {
            if !requested.insert((exact, id.clone())) {
                continue;
            }
            if depth > 32 || requested.len() > MAX_PROJECTS * 4 {
                return Err("Mod dependency graph exceeds its budget".into());
            }
            let mut candidate = fetch(exact, &id)?;
            let project = candidate.entry.project.clone();
            if exact {
                constraints
                    .entry(project.clone())
                    .or_default()
                    .insert(candidate.entry.version.clone());
            } else if let Some(pin) = previous_pins.get(&project) {
                candidate = fetch(true, pin)?;
                if candidate.entry.project != project {
                    return Err("Pinned version belongs to another Mod project".into());
                }
            }
            if selected.contains_key(&project) {
                continue;
            }
            if selected.len() >= MAX_PROJECTS {
                return Err("Mod graph exceeds 512 projects".into());
            }
            for (exact, id) in &candidate.dependencies {
                queue.push_back((*exact, id.clone(), depth + 1));
            }
            selected.insert(project, candidate);
        }
        let pins: BTreeMap<_, _> = constraints
            .iter()
            .map(|(project, versions)| {
                let selected = previous_pins
                    .get(project)
                    .filter(|old| versions.contains(*old))
                    .cloned()
                    .unwrap_or_else(|| {
                        versions
                            .iter()
                            .next()
                            .expect("nonempty constraints")
                            .clone()
                    });
                (project.clone(), selected)
            })
            .collect();
        let matched = pins.iter().all(|(project, version)| {
            selected
                .get(project)
                .is_some_and(|selected| selected.entry.version == *version)
        });
        if matched && pins == previous_pins {
            if let Some((project, versions)) =
                constraints.iter().find(|(_, versions)| versions.len() > 1)
            {
                return Err(format!(
                    "Required versions conflict for {project}: {}. Update dependent projects together.",
                    versions.iter().cloned().collect::<Vec<_>>().join(", ")
                ));
            }
            let mut result = Vec::new();
            for (project, mut version) in selected {
                version.entry.dependencies = Some(version.dependencies);
                version.entry.incompatible = version.incompatible;
                if let Some(previous) = old.iter().find(|entry| entry.project == project) {
                    version.entry.enabled = previous.enabled;
                }
                if let Some((enabled, explicit)) = root_flags.get(&project) {
                    version.entry.enabled = *enabled;
                    version.entry.explicit = *explicit;
                } else {
                    version.entry.explicit = false;
                }
                result.push(version.entry);
            }
            for entry in old.iter().filter(|entry| !identified(entry)) {
                result.push(entry.clone());
            }
            validate_graph(&result)?;
            result.sort_by(|a, b| a.path.cmp(&b.path));
            return Ok(result);
        }
        let state = format!("{pins:?}");
        if !seen_plans.insert(state) {
            return Err(
                "Mod version constraints did not converge; the existing installation was preserved"
                    .into(),
            );
        }
        previous_pins = pins;
    }
    Err("Mod dependency pin propagation exceeded 32 passes".into())
}
fn validate_graph(entries: &[Entry]) -> Result<()> {
    let mut paths = BTreeSet::new();
    let mut projects = BTreeSet::new();
    let mut total = 0u64;
    for entry in entries {
        portable_path(&entry.path)?;
        if !paths.insert(entry.actual_path().to_lowercase()) {
            return Err("Managed Mod destinations collide".into());
        }
        total = total
            .checked_add(entry.size)
            .ok_or("Content size overflow")?;
        if total > MAX_TOTAL {
            return Err("Content exceeds 4 GiB".into());
        }
        if !identified(entry) {
            continue;
        }
        if !projects.insert(&entry.project) {
            return Err("Two selected versions claim the same Mod project".into());
        }
        let dependencies = entry
            .dependencies
            .as_ref()
            .ok_or("Missing selected dependency metadata")?;
        for dependency in dependencies {
            let target = dependency_target(entries, dependency).ok_or_else(|| {
                format!(
                    "Missing required dependency {} for {}",
                    dependency.1, entry.project
                )
            })?;
            if entry.enabled && !target.enabled {
                return Err(format!(
                    "Required project {} is disabled; enable it before changing {}",
                    target.project, entry.project
                ));
            }
        }
        if entry.enabled {
            for dependency in &entry.incompatible {
                if let Some(target) = dependency_target(entries, dependency)
                    && target.enabled
                {
                    return Err(format!(
                        "Projects {} and {} declare incompatible versions",
                        entry.project, target.project
                    ));
                }
            }
        }
    }
    Ok(())
}
fn select_entry(entries: &[Entry], selector: &str) -> Result<usize> {
    let matched: Vec<_> = entries
        .iter()
        .enumerate()
        .filter(|(_, entry)| {
            is_mod(entry)
                && (entry.project == selector
                    || (!entry.project_slug.is_empty() && entry.project_slug == selector)
                    || entry.path == selector
                    || entry.path.strip_prefix("mods/") == Some(selector)
                    || entry.actual_path().strip_prefix("mods/") == Some(selector))
        })
        .map(|(index, _)| index)
        .collect();
    if matched.len() == 1 {
        return Ok(matched[0]);
    }
    if matched.len() > 1 {
        return Err("Mod selector matches multiple managed files".into());
    }
    if slug(selector).is_ok() {
        let project = with_json(&net::get(&format!("{API}/project/{selector}"))?, |v| {
            req_str(v, "id")
        })?;
        if let Some(index) = entries
            .iter()
            .position(|entry| is_mod(entry) && entry.project == project)
        {
            return Ok(index);
        }
    }
    Err("Select an installed managed Mod by project ID, slug or filename".into())
}
#[derive(PartialEq)]
struct Stamp {
    directory: bool,
    bytes: u64,
    modified: Option<SystemTime>,
}
fn inventory(root: &Path) -> Result<BTreeMap<PathBuf, Stamp>> {
    let mut result = BTreeMap::new();
    if !root.exists() {
        return Ok(result);
    }
    let mut queue = vec![root.to_path_buf()];
    while let Some(path) = queue.pop() {
        let info = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        if info.file_type().is_symlink() || (!info.is_file() && !info.is_dir()) {
            return Err("Mod directory contains a link or special file".into());
        }
        result.insert(
            path.clone(),
            Stamp {
                directory: info.is_dir(),
                bytes: if info.is_file() { info.len() } else { 0 },
                modified: info.modified().ok(),
            },
        );
        if result.len() > MAX_FILES {
            return Err("Mod directory exceeds its entry budget".into());
        }
        if info.is_dir() {
            for entry in fs::read_dir(&path).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                queue.push(entry.path());
            }
        }
    }
    Ok(result)
}
fn publish(app: &App, id: &str, dir: &Path, old: &[Entry], new: &[Entry]) -> Result<()> {
    publish_with(app, id, dir, old, new, |entry| {
        store::ensure(
            app,
            &format!("content/sha1/{}", entry.sha1),
            &entry.url,
            Some(&entry.sha1),
            None,
            Some(entry.size),
        )
    })
}
fn publish_with<F>(
    app: &App,
    id: &str,
    dir: &Path,
    old: &[Entry],
    new: &[Entry],
    mut obtain: F,
) -> Result<()>
where
    F: FnMut(&Entry) -> Result<PathBuf>,
{
    validate_graph(new)?;
    let game = util::safe_join(dir, "game")?;
    for entry in old.iter().filter(|entry| is_mod(entry)) {
        verify_entry(&game, entry)?;
    }
    let old_paths: BTreeSet<_> = old
        .iter()
        .filter(|entry| is_mod(entry))
        .map(|entry| entry.actual_path().to_lowercase())
        .collect();
    for entry in new.iter().filter(|entry| is_mod(entry)) {
        for path in [&entry.path, &format!("{}.disabled", entry.path)] {
            if util::safe_join(&game, path)?.exists() && !old_paths.contains(&path.to_lowercase()) {
                return Err(format!("Mod filename collides with manual content: {path}"));
            }
        }
    }
    let mut sources = Vec::new();
    for entry in new.iter().filter(|entry| is_mod(entry)) {
        let source = if let Some(existing) = old.iter().find(|existing| {
            is_mod(existing) && existing.sha1 == entry.sha1 && existing.size == entry.size
        }) {
            util::safe_join(&game, &existing.actual_path())?
        } else {
            obtain(entry)?
        };
        let (actual, _, size) = store::digests(&source)?;
        if actual != entry.sha1 || size != entry.size {
            return Err(
                "New Mod artifact failed hash or size verification; active Mods are unchanged"
                    .into(),
            );
        }
        sources.push((entry, source));
    }
    // No mutation of live mods has happened before every new artifact verified.
    let mods = util::safe_join(&game, "mods")?;
    let before = inventory(&mods)?;
    let mut temporary = Temp::new(dir)?;
    let staged = temporary.path.join("mods");
    clone_mods(&mods, &staged)?;
    for entry in old.iter().filter(|entry| is_mod(entry)) {
        let actual = entry.actual_path();
        let relative = actual
            .strip_prefix("mods/")
            .ok_or("Invalid managed Mod path")?;
        fs::remove_file(util::safe_join(&staged, relative)?).map_err(|e| e.to_string())?;
    }
    for (entry, source) in sources {
        let actual = entry.actual_path();
        let relative = actual
            .strip_prefix("mods/")
            .ok_or("Invalid managed Mod path")?;
        let target = util::safe_join(&staged, relative)?;
        if target.exists() {
            return Err("A new Mod would replace manual content in staging".into());
        }
        store::materialize(&source, &target, true)?;
    }
    worlds::assert_stopped(app, id)?;
    if inventory(&mods)? != before {
        return Err("Mod files changed during staging; no update was published".into());
    }
    for entry in old.iter().filter(|entry| is_mod(entry)) {
        verify_entry(&game, entry)?;
    }
    commit_mods(dir, &mut temporary, new)
}
fn context(app: &App, id: &str) -> Result<(crate::app::Instance, PathBuf, Vec<Entry>)> {
    transaction::recover_locked(app, id)?;
    let instance = app.read_instance(id)?;
    if !matches!(
        instance.loader.as_str(),
        "fabric" | "quilt" | "forge" | "neoforge"
    ) {
        return Err("A Mod loader instance is required".into());
    }
    worlds::assert_stopped(app, id)?;
    let dir = app.dir(id)?;
    util::check_pending_transactions(&dir)?;
    let old = read_lock(&dir)?;
    let old = hydrate(&old, &instance.version, &instance.loader)?;
    Ok((instance, dir, old))
}
pub(super) fn add(app: &App, id: &str, project: &str) -> Result<()> {
    util::valid_id(id)?;
    slug(project)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (instance, dir, old) = context(app, id)?;
    let mut repository = Repository::new(
        &instance.version,
        &instance.loader,
        &old,
        BTreeSet::new(),
        false,
    )?;
    let selected = repository.fetch(false, project)?;
    if old
        .iter()
        .any(|entry| entry.project == selected.entry.project && !entry.enabled)
    {
        return Err("This managed project is disabled; enable it before adding it again".into());
    }
    let mut requests = roots(&old, &BTreeSet::new(), false);
    requests.push(Root {
        exact: false,
        id: selected.entry.project,
        enabled: true,
        explicit: true,
    });
    let new = resolve_roots(&requests, &old, |exact, id| repository.fetch(exact, id))?;
    publish(app, id, &dir, &old, &new)
}
/// Updates all explicit Mod roots, or one selected root and its required graph.
/// Exact dependency constraints and declared conflicts are validated as one set.
pub fn update(app: &App, id: &str, project: Option<&str>) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (instance, dir, old) = context(app, id)?;
    let mut updates = BTreeSet::new();
    let selected = if let Some(selector) = project {
        let index = select_entry(&old, selector)?;
        if !identified(&old[index]) {
            return Err(
                "This file is not identified as a Modrinth project; it was preserved".into(),
            );
        }
        updates = related_projects(&old, &old[index].project);
        Some(index)
    } else {
        None
    };
    let mut requests = roots(&old, &updates, project.is_none());
    if let Some(index) = selected
        && !requests
            .iter()
            .any(|request| request.id == old[index].project)
    {
        requests.push(Root {
            exact: false,
            id: old[index].project.clone(),
            enabled: old[index].enabled,
            explicit: old[index].explicit,
        });
    }
    let mut repository = Repository::new(
        &instance.version,
        &instance.loader,
        &old,
        updates,
        project.is_none(),
    )?;
    let new = resolve_roots(&requests, &old, |exact, id| repository.fetch(exact, id))?;
    publish(app, id, &dir, &old, &new)
}
fn without(entries: &[Entry], index: usize) -> Result<Vec<Entry>> {
    let target = &entries[index];
    for (other_index, other) in entries.iter().enumerate() {
        if other_index == index {
            continue;
        }
        for (exact, id) in other.dependencies.iter().flatten() {
            if (*exact && *id == target.version) || (!*exact && *id == target.project) {
                return Err(format!(
                    "Cannot remove {}: required by {}",
                    target.project, other.project
                ));
            }
        }
    }
    Ok(entries
        .iter()
        .enumerate()
        .filter(|(i, _)| *i != index)
        .map(|(_, entry)| entry.clone())
        .collect())
}
/// Removes exactly one managed project after checking every known installed
/// version's required dependencies. Manual files and remaining dependencies stay.
pub fn remove(app: &App, id: &str, project: &str) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (_instance, dir, old) = context(app, id)?;
    let index = select_entry(&old, project)?;
    if !identified(&old[index]) {
        return Err(
            "Cannot prove dependencies for this unidentified file; it was preserved".into(),
        );
    }
    let new = without(&old, index)?;
    publish(app, id, &dir, &old, &new)
}

pub(super) fn toggle(app: &App, id: &str, selector: &str, enabled: bool) -> Result<()> {
    util::valid_id(id)?;
    let _lock = Lock::acquire(&app.root, &format!("instance-{id}"))?;
    let (_, dir, old) = context(app, id)?;
    let index = select_entry(&old, selector)?;
    if old[index].enabled == enabled {
        return Ok(());
    }
    let mut new = old.clone();
    new[index].enabled = enabled;
    publish(app, id, &dir, &old, &new)
}

pub fn pack_search(query: &str, game: Option<&str>) -> Result<String> {
    if query.len() > 512 {
        return Err("Search query exceeds 512 bytes".into());
    }
    let mut facets = vec!["[\"project_type:modpack\"]".to_owned()];
    if let Some(game) = game {
        facets.push(format!("[{}]", quote(&format!("versions:{game}"))));
    }
    let bytes = net::get(&format!(
        "{API}/search?query={}&limit=20&facets={}",
        net::encode(query),
        net::encode(&format!("[{}]", facets.join(",")))
    ))?;
    with_json(&bytes, |value| {
        if value
            .get("hits")
            .is_none_or(|hits| hits.kind() != Kind::Array)
        {
            return Err("Invalid Modrinth pack search response".into());
        }
        Ok(())
    })?;
    String::from_utf8(bytes).map_err(|_| "Pack search response is not UTF-8".into())
}
#[derive(Debug)]
struct PackDownload {
    project: String,
    version: String,
    name: String,
    url: String,
    sha1: String,
    size: u64,
}
fn pack_download(value: Value<'_, '_>, project: &str) -> Result<PackDownload> {
    if req_str(value, "project_id")? != project || !client_supported(value) {
        return Err("Pack version does not belong to the requested client project".into());
    }
    let version = req_str(value, "id")?;
    slug(&version)?;
    let files = value
        .get("files")
        .filter(|v| v.kind() == Kind::Array)
        .ok_or("Pack version has no files")?;
    let candidates: Vec<_> = files
        .array()
        .filter(|file| {
            get_str(*file, "filename")
                .is_some_and(|name| name.to_ascii_lowercase().ends_with(".mrpack"))
        })
        .collect();
    let file = *candidates
        .iter()
        .find(|file| file.get("primary").and_then(Value::as_bool) == Some(true))
        .or_else(|| candidates.first())
        .ok_or("Pack version has no .mrpack file")?;
    let name = req_str(file, "filename")?;
    portable_path(&name)?;
    if name.contains('/') {
        return Err("Pack filename must be one portable component".into());
    }
    let hashes = file.get("hashes").ok_or("Pack file has no hash")?;
    let sha1 = digest(&req_str(hashes, "sha1")?, 40)?;
    let size = file
        .get("size")
        .and_then(Value::as_u64)
        .ok_or("Pack file has no size")?;
    if size == 0 || size > MAX_FILE {
        return Err("Pack archive exceeds its 512 MiB download budget".into());
    }
    let url = req_str(file, "url")?;
    content_url(&url)?;
    Ok(PackDownload {
        project: project.to_owned(),
        version,
        name,
        url,
        sha1,
        size,
    })
}
/// Installs a public Modrinth pack into a new instance. The optional version is
/// a Modrinth version ID. Runtime installation remains the caller's next step.
pub fn install_project_pack(
    app: &App,
    project: &str,
    id: &str,
    version_id: Option<&str>,
) -> Result<()> {
    util::valid_id(id)?;
    slug(project)?;
    if app.dir(id)?.exists() {
        return Err("Pack installation requires a new instance ID".into());
    }
    let canonical = with_json(&net::get(&format!("{API}/project/{project}"))?, |value| {
        if get_str(value, "project_type").as_deref() != Some("modpack") || !client_supported(value)
        {
            return Err("This project is not a client Modrinth modpack".into());
        }
        let id = req_str(value, "id")?;
        slug(&id)?;
        Ok(id)
    })?;
    let download = if let Some(version_id) = version_id {
        slug(version_id)?;
        with_json(
            &net::get(&format!("{API}/version/{version_id}"))?,
            |value| {
                if req_str(value, "id")? != version_id {
                    return Err("Modrinth returned another pack version".into());
                }
                pack_download(value, &canonical)
            },
        )?
    } else {
        with_json(
            &net::get(&format!(
                "{API}/project/{canonical}/version?include_changelog=false"
            ))?,
            |value| {
                if value.kind() != Kind::Array {
                    return Err("Invalid pack version list".into());
                }
                let selected = value
                    .array()
                    .filter(|value| {
                        client_supported(*value)
                            && get_str(*value, "version_type").as_deref() == Some("release")
                    })
                    .max_by_key(|value| get_str(*value, "date_published").unwrap_or_default())
                    .ok_or(
                        "No stable client pack version found; specify an exact Modrinth version ID",
                    )?;
                pack_download(selected, &canonical)
            },
        )?
    };
    let archive = store::ensure(
        app,
        &format!("content/packs/{}/{}", download.sha1, download.name),
        &download.url,
        Some(&download.sha1),
        None,
        Some(download.size),
    )?;
    let provenance = util::object(&[
        ("provider", quote("modrinth")),
        ("project_id", quote(&download.project)),
        ("version_id", quote(&download.version)),
        ("url", quote(&download.url)),
        ("sha1", quote(&download.sha1)),
    ]);
    super::import_pack_inner(app, &archive, id, Some(provenance.as_bytes()))
}

#[cfg(test)]
#[path = "management_tests.rs"]
mod tests;
