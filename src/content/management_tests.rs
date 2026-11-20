use super::*;

fn node(
    project: &str,
    version: &str,
    body: &[u8],
    dependencies: Vec<(bool, String)>,
    incompatible: Vec<(bool, String)>,
) -> Version {
    Version {
        entry: Entry {
            project: project.into(),
            version: version.into(),
            path: format!("mods/{project}-{version}.jar"),
            sha1: zmcl_core::hash::sha1_hex(body),
            sha512: None,
            url: format!("https://cdn.modrinth.com/{project}-{version}.jar"),
            size: body.len() as u64,
            enabled: true,
            project_slug: project.to_ascii_lowercase(),
            explicit: false,
            dependencies: Some(dependencies.clone()),
            incompatible: incompatible.clone(),
        },
        dependencies,
        incompatible,
    }
}
fn root(id: &str) -> Root {
    Root {
        exact: false,
        id: id.into(),
        enabled: true,
        explicit: true,
    }
}
fn fetcher(
    versions: Vec<Version>,
    latest: Vec<Version>,
) -> impl FnMut(bool, &str) -> Result<Version> {
    let mut map = BTreeMap::new();
    for version in versions {
        map.insert((true, version.entry.version.clone()), version);
    }
    for version in latest {
        map.insert((true, version.entry.version.clone()), version.clone());
        map.insert((false, version.entry.project.clone()), version);
    }
    move |exact, id| {
        map.get(&(exact, id.to_owned()))
            .cloned()
            .ok_or_else(|| format!("Missing fixture {exact}/{id}"))
    }
}
#[test]
fn dependency_cycles_are_bounded_and_roots_remain_explicit() {
    let a = node("A", "a1", b"a", vec![(false, "B".into())], vec![]);
    let b = node("B", "b1", b"b", vec![(false, "A".into())], vec![]);
    let result = resolve_roots(&[root("A")], &[], fetcher(vec![], vec![a, b])).unwrap();
    assert_eq!(result.len(), 2);
    assert!(
        result
            .iter()
            .find(|entry| entry.project == "A")
            .unwrap()
            .explicit
    );
    assert!(
        !result
            .iter()
            .find(|entry| entry.project == "B")
            .unwrap()
            .explicit
    );
}
#[test]
fn exact_dependency_overrides_a_latest_version_preference() {
    let a = node("A", "a1", b"a", vec![(true, "b1".into())], vec![]);
    let b1 = node("B", "b1", b"b1", vec![], vec![]);
    let b2 = node("B", "b2", b"b2", vec![], vec![]);
    let result =
        resolve_roots(&[root("A"), root("B")], &[], fetcher(vec![b1], vec![a, b2])).unwrap();
    assert_eq!(
        result
            .iter()
            .find(|entry| entry.project == "B")
            .unwrap()
            .version,
        "b1"
    );
}
#[test]
fn two_actual_exact_dependencies_conflict() {
    let a = node("A", "a1", b"a", vec![(true, "b1".into())], vec![]);
    let c = node("C", "c1", b"c", vec![(true, "b2".into())], vec![]);
    let b1 = node("B", "b1", b"b1", vec![], vec![]);
    let b2 = node("B", "b2", b"b2", vec![], vec![]);
    let error = resolve_roots(
        &[root("A"), root("C")],
        &[],
        fetcher(vec![b1, b2], vec![a, c]),
    )
    .unwrap_err();
    assert!(error.contains("Required versions conflict"), "{error}");
}
#[test]
fn replaced_versions_do_not_leave_ghost_dependency_pins() {
    let a = node("A", "a1", b"a", vec![(true, "b1".into())], vec![]);
    let b1 = node("B", "b1", b"b1", vec![], vec![]);
    let b2 = node("B", "b2", b"b2", vec![(true, "c1".into())], vec![]);
    let c1 = node("C", "c1", b"c1", vec![], vec![]);
    let c2 = node("C", "c2", b"c2", vec![], vec![]);
    let result = resolve_roots(
        &[root("A"), root("B"), root("C")],
        &[],
        fetcher(vec![b1, c1], vec![a, b2, c2]),
    )
    .unwrap();
    assert_eq!(
        result
            .iter()
            .find(|entry| entry.project == "B")
            .unwrap()
            .version,
        "b1"
    );
    assert_eq!(
        result
            .iter()
            .find(|entry| entry.project == "C")
            .unwrap()
            .version,
        "c2"
    );
}
#[test]
fn declared_incompatible_projects_are_checked_as_a_set() {
    let a = node("A", "a1", b"a", vec![], vec![(false, "B".into())]);
    let b = node("B", "b1", b"b", vec![], vec![]);
    assert!(
        resolve_roots(
            &[root("A"), root("B")],
            &[],
            fetcher(vec![], vec![a.clone(), b.clone()])
        )
        .unwrap_err()
        .contains("incompatible")
    );
    let mut disabled = root("B");
    disabled.enabled = false;
    let result = resolve_roots(&[root("A"), disabled], &[], fetcher(vec![], vec![a, b])).unwrap();
    assert!(
        !result
            .iter()
            .find(|entry| entry.project == "B")
            .unwrap()
            .enabled
    );
}
#[test]
fn remove_checks_required_dependencies_even_for_a_disabled_dependent() {
    let mut a = node("A", "a1", b"a", vec![(false, "B".into())], vec![]).entry;
    a.enabled = false;
    let b = node("B", "b1", b"b", vec![], vec![]).entry;
    let entries = vec![a, b];
    assert!(without(&entries, 1).unwrap_err().contains("required by A"));
    assert_eq!(without(&entries, 0).unwrap()[0].project, "B");
}
fn fixture() -> (Temp, App, PathBuf, Entry) {
    let temporary = Temp::new(&std::env::temp_dir()).unwrap();
    let app = App::new(Some(
        temporary.path.join("app").to_string_lossy().into_owned(),
    ))
    .unwrap();
    app.create_instance("managed", "1.20.1", "fabric", "0.16.0")
        .unwrap();
    let dir = app.dir("managed").unwrap();
    let mut old = node("A", "a1", b"old", vec![], vec![]).entry;
    old.explicit = true;
    util::atomic_write(&dir.join("game").join(&old.path), b"old").unwrap();
    util::atomic_write(&dir.join("game/mods/manual.jar"), b"manual bytes").unwrap();
    util::atomic_write(&dir.join("game/mods/custom/settings.txt"), b"user settings").unwrap();
    util::atomic_write(
        &dir.join("mods-lock.json"),
        lock_json(std::slice::from_ref(&old)).as_bytes(),
    )
    .unwrap();
    (temporary, app, dir, old)
}
#[test]
fn publication_updates_managed_mods_and_preserves_manual_files() {
    let (_temporary, app, dir, old) = fixture();
    let mut next = node("A", "a2", b"new", vec![(false, "B".into())], vec![]).entry;
    next.explicit = true;
    let dependency = node("B", "b1", b"dep", vec![], vec![]).entry;
    let source = app.root.join("new-fixture");
    util::atomic_write(&source, b"new").unwrap();
    let dep_source = app.root.join("dep-fixture");
    util::atomic_write(&dep_source, b"dep").unwrap();
    publish_with(
        &app,
        "managed",
        &dir,
        std::slice::from_ref(&old),
        &[next.clone(), dependency.clone()],
        |entry| {
            Ok(if entry.project == "A" {
                source.clone()
            } else {
                dep_source.clone()
            })
        },
    )
    .unwrap();
    assert!(!dir.join("game").join(old.path).exists());
    assert_eq!(fs::read(dir.join("game").join(next.path)).unwrap(), b"new");
    assert_eq!(
        fs::read(dir.join("game").join(dependency.path)).unwrap(),
        b"dep"
    );
    assert_eq!(
        fs::read(dir.join("game/mods/manual.jar")).unwrap(),
        b"manual bytes"
    );
    assert_eq!(
        fs::read(dir.join("game/mods/custom/settings.txt")).unwrap(),
        b"user settings"
    );
    assert!(!dir.join(".content-journal.json").exists());
    assert!(
        !dir.join("game/mods/.zmcl-content-transaction.json")
            .exists()
    );
}
#[test]
fn bad_new_artifact_leaves_live_mods_and_lock_unchanged() {
    let (_temporary, app, dir, old) = fixture();
    let before = fs::read(dir.join("mods-lock.json")).unwrap();
    let next = node("A", "a2", b"new", vec![], vec![]).entry;
    let source = app.root.join("corrupt-fixture");
    util::atomic_write(&source, b"wrong").unwrap();
    let error = publish_with(
        &app,
        "managed",
        &dir,
        std::slice::from_ref(&old),
        &[next],
        |_| Ok(source.clone()),
    )
    .unwrap_err();
    assert!(error.contains("hash or size"));
    assert_eq!(fs::read(dir.join("game").join(old.path)).unwrap(), b"old");
    assert_eq!(fs::read(dir.join("mods-lock.json")).unwrap(), before);
    assert!(!dir.join(".content-journal.json").exists());
}
#[test]
fn manual_filename_collision_fails_before_obtaining_new_files() {
    let (_temporary, app, dir, old) = fixture();
    let mut next = node("A", "a2", b"new", vec![], vec![]).entry;
    next.path = "mods/manual.jar".into();
    let error = publish_with(
        &app,
        "managed",
        &dir,
        std::slice::from_ref(&old),
        &[next],
        |_| panic!("collision must be checked before downloading"),
    )
    .unwrap_err();
    assert!(error.contains("manual content"));
    assert_eq!(
        fs::read(dir.join("game/mods/manual.jar")).unwrap(),
        b"manual bytes"
    );
}
#[test]
fn offline_remove_uses_cached_dependency_metadata() {
    let (_temporary, app, dir, old) = fixture();
    remove(&app, "managed", "a").unwrap();
    assert!(!dir.join("game").join(old.path).exists());
    assert_eq!(
        fs::read(dir.join("game/mods/manual.jar")).unwrap(),
        b"manual bytes"
    );
    assert!(
        parse_lock(&fs::read(dir.join("mods-lock.json")).unwrap())
            .unwrap()
            .is_empty()
    );
}
#[test]
fn pack_version_identity_and_primary_mrpack_are_validated() {
    let body = format!(
        r#"{{"id":"version1","project_id":"pack1","environment":"client_and_server","files":[{{"filename":"data.txt","primary":false,"hashes":{{"sha1":"{}"}},"size":1,"url":"https://cdn.modrinth.com/data.txt"}},{{"filename":"pack.mrpack","primary":true,"hashes":{{"sha1":"{}"}},"size":100,"url":"https://cdn.modrinth.com/pack.mrpack"}}]}}"#,
        "a".repeat(40),
        "b".repeat(40)
    );
    let selected = with_json(body.as_bytes(), |value| pack_download(value, "pack1")).unwrap();
    assert_eq!(selected.name, "pack.mrpack");
    assert!(with_json(body.as_bytes(), |value| pack_download(value, "other")).is_err());
}
