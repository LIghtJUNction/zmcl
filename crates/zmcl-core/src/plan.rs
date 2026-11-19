//! Download and launch plans. Paths are portable and argv is never a shell string.
use crate::{
    json,
    model::{Argument, Download, Environment, Version},
    path,
};
use alloc::{
    collections::BTreeMap,
    format,
    string::{String, ToString},
    vec,
    vec::Vec,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Artifact {
    pub path: String,
    pub download: Download,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NativeArchive {
    pub path: String,
    pub excludes: Vec<String>,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct InstallPlan {
    pub downloads: Vec<Artifact>,
    pub classpath: Vec<String>,
    pub natives: Vec<NativeArchive>,
}
fn insert_download(plan: &mut InstallPlan, path: String, download: Download) -> Result<(), String> {
    path::validate_relative(&path)?;
    if let Some(existing) = plan.downloads.iter().find(|d| d.path == path) {
        if existing.download.sha1 != download.sha1 || existing.download.url != download.url {
            return Err(format!("Conflicting downloads target {path}"));
        }
        return Ok(());
    }
    plan.downloads.push(Artifact { path, download });
    Ok(())
}
fn add_classpath(plan: &mut InstallPlan, path: String) {
    if !plan.classpath.contains(&path) {
        plan.classpath.push(path);
    }
}
pub fn install_plan(version: &Version, env: &Environment) -> Result<InstallPlan, String> {
    if version.inherits_from.is_some() {
        return Err("Resolve version inheritance before planning installation".into());
    }
    if !matches!(env.os.as_str(), "linux" | "windows" | "osx") {
        return Err("Unsupported target OS".into());
    }
    path::validate_id(&version.id)?;
    let mut plan = InstallPlan::default();
    for library in &version.libraries {
        if !library.client_required || !env.rules_allow(&library.rules)? {
            continue;
        }
        if let Some(download) = &library.artifact {
            let p = download
                .path
                .clone()
                .unwrap_or(path::maven_path(&library.name)?);
            path::validate_relative(&p)?;
            let p = format!("libraries/{p}");
            insert_download(&mut plan, p.clone(), download.clone())?;
            add_classpath(&mut plan, p.clone());
            // Recent Mojang metadata lists platform-native JARs as normal artifacts.
            let coordinate = library.name.split('@').next().unwrap_or(&library.name);
            if coordinate
                .split(':')
                .nth(3)
                .is_some_and(|c| c.starts_with("natives-"))
            {
                plan.natives.push(NativeArchive {
                    path: p,
                    excludes: library.extract_excludes.clone(),
                });
            }
        }
        if let Some(classifier) = library.natives.get(&env.os) {
            let bits = if env.arch == "x86" { "32" } else { "64" };
            let classifier = classifier.replace("${arch}", bits);
            path::validate_id(&classifier)?;
            let download = library.classifiers.get(&classifier).ok_or_else(|| {
                format!(
                    "Missing native classifier {classifier} for {}",
                    library.name
                )
            })?;
            let relative = download.path.as_ref().ok_or("Native archive has no path")?;
            path::validate_relative(relative)?;
            let p = format!("libraries/{relative}");
            insert_download(&mut plan, p.clone(), download.clone())?;
            if !plan.natives.iter().any(|v| v.path == p) {
                plan.natives.push(NativeArchive {
                    path: p,
                    excludes: library.extract_excludes.clone(),
                });
            }
        }
    }
    let client_id = version.client_id.as_deref().unwrap_or(&version.id);
    path::validate_id(client_id)?;
    let jar = format!("versions/{client_id}/{client_id}.jar");
    if let Some(client) = &version.client {
        insert_download(&mut plan, jar.clone(), client.clone())?;
    }
    add_classpath(&mut plan, jar);
    if let Some(index) = &version.asset_index {
        path::validate_id(&index.id)?;
        insert_download(
            &mut plan,
            format!("assets/indexes/{}.json", index.id),
            index.download.clone(),
        )?;
    }
    if let Some(log) = &version.logging {
        path::validate_relative(&log.id)?;
        insert_download(
            &mut plan,
            format!("assets/log_configs/{}", log.id),
            log.file.clone(),
        )?;
    }
    Ok(plan)
}

#[derive(Clone, Debug)]
pub struct LaunchOptions {
    pub java: String,
    /// Absolute directory containing versions/, libraries/ and assets/.
    pub root: String,
    pub game_dir: String,
    pub natives_dir: String,
    pub username: String,
    pub uuid: String,
    pub access_token: String,
    pub user_type: String,
    pub launcher_name: String,
    pub launcher_version: String,
    pub memory_mib: u32,
    pub width: u32,
    pub height: u32,
    pub extra_jvm: Vec<String>,
    pub additional_variables: BTreeMap<String, String>,
}
impl Default for LaunchOptions {
    fn default() -> Self {
        Self {
            java: "java".into(),
            root: ".".into(),
            game_dir: ".".into(),
            natives_dir: "natives".into(),
            username: "Player".into(),
            uuid: crate::hash::offline_uuid("Player"),
            access_token: "0".into(),
            user_type: "legacy".into(),
            launcher_name: "zmcl".into(),
            launcher_version: env!("CARGO_PKG_VERSION").into(),
            memory_mib: 2048,
            width: 1280,
            height: 720,
            extra_jvm: Vec::new(),
            additional_variables: BTreeMap::new(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LaunchPlan {
    pub java: String,
    pub args: Vec<String>,
    pub working_directory: String,
    pub java_major: u32,
}
impl LaunchPlan {
    /// Safe for diagnostics: redact Minecraft access-token and legacy session values.
    pub fn redacted_args(&self) -> Vec<String> {
        let mut redact_next = false;
        self.args
            .iter()
            .map(|arg| {
                if redact_next {
                    redact_next = false;
                    return "<redacted>".into();
                }
                if matches!(arg.as_str(), "--accessToken" | "--session") {
                    redact_next = true;
                }
                if let Some((key, _)) = arg.split_once('=')
                    && matches!(key, "--accessToken" | "--session")
                {
                    return format!("{key}=<redacted>");
                }
                arg.clone()
            })
            .collect()
    }
}

fn resolve_argument(arg: &str, vars: &BTreeMap<String, String>) -> Result<String, String> {
    let mut out = String::with_capacity(arg.len());
    let mut rest = arg;
    while let Some(start) = rest.find("${") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let end = after.find('}').ok_or("Unterminated launch placeholder")?;
        let key = &after[..end];
        let value = vars
            .get(key)
            .ok_or_else(|| format!("Unknown launch variable: {key}"))?;
        out.push_str(value);
        rest = &after[end + 1..];
        if out.len() > 1024 * 1024 {
            return Err("Launch argument exceeds 1 MiB".into());
        }
    }
    out.push_str(rest);
    if out.contains('\0') {
        return Err("NUL byte in launch argument".into());
    }
    if out.len() > 1024 * 1024 {
        return Err("Launch argument exceeds 1 MiB".into());
    }
    Ok(out)
}
fn append_arguments(
    out: &mut Vec<String>,
    args: &[Argument],
    env: &Environment,
    vars: &BTreeMap<String, String>,
) -> Result<(), String> {
    for arg in args {
        match arg {
            Argument::Plain(s) => out.push(resolve_argument(s, vars)?),
            Argument::Conditional { rules, values } if env.rules_allow(rules)? => {
                for s in values {
                    out.push(resolve_argument(s, vars)?);
                }
            }
            Argument::Conditional { .. } => {}
        }
        if out.len() > 16384 {
            return Err("Too many launch arguments".into());
        }
    }
    Ok(())
}

/// Legacy argument syntax recognizes quotes and escapes but never executes substitutions.
pub fn split_legacy(input: &str) -> Result<Vec<String>, String> {
    let mut result = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;
    let mut started = false;
    for c in input.chars() {
        if c == '\0' {
            return Err("NUL byte in legacy arguments".into());
        }
        if escaped {
            current.push(c);
            escaped = false;
            started = true;
            continue;
        }
        if c == '\\' && quote != Some('\'') {
            escaped = true;
            started = true;
            continue;
        }
        if let Some(q) = quote {
            if c == q {
                quote = None;
            } else {
                current.push(c);
            }
            started = true;
        } else if c == '"' || c == '\'' {
            quote = Some(c);
            started = true;
        } else if c.is_whitespace() {
            if started {
                result.push(core::mem::take(&mut current));
                started = false;
            }
        } else {
            current.push(c);
            started = true;
        }
    }
    if escaped || quote.is_some() {
        return Err("Unterminated quote or escape in legacy arguments".into());
    }
    if started {
        result.push(current);
    }
    Ok(result)
}

pub fn launch_plan(
    version: &Version,
    env: &Environment,
    options: &LaunchOptions,
) -> Result<LaunchPlan, String> {
    let install = install_plan(version, env)?;
    if options.java.is_empty() || options.java.contains('\0') {
        return Err("Java executable is empty or invalid".into());
    }
    if !(128..=1_048_576).contains(&options.memory_mib) {
        return Err("Memory must be 128..1048576 MiB".into());
    }
    if options.username.is_empty()
        || options.username.len() > 16
        || !options
            .username
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Err("Minecraft username must contain 1..16 ASCII letters, digits or '_'".into());
    }
    let compact_uuid = options.uuid.replace('-', "");
    if compact_uuid.len() != 32 || !compact_uuid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid player UUID".into());
    }
    let separator = if env.os == "windows" { ';' } else { ':' };
    for p in [&options.root, &options.game_dir, &options.natives_dir] {
        if p.contains('\0') {
            return Err("NUL byte in launcher directory".into());
        }
    }
    if options.root.contains(separator) {
        return Err("Shared root contains the platform classpath separator".into());
    }
    let cp = install
        .classpath
        .iter()
        .map(|p| path::join(&options.root, p))
        .collect::<Vec<_>>()
        .join(&separator.to_string());
    let assets = path::join(&options.root, "assets");
    let asset_id = version
        .asset_index
        .as_ref()
        .map(|i| i.id.as_str())
        .or(version.asset_id.as_deref())
        .unwrap_or("legacy");
    let mut vars = BTreeMap::from([
        ("auth_player_name".into(), options.username.clone()),
        ("auth_uuid".into(), compact_uuid),
        ("auth_access_token".into(), options.access_token.clone()),
        (
            "auth_session".into(),
            format!("token:{}:{}", options.access_token, options.uuid),
        ),
        ("user_type".into(), options.user_type.clone()),
        ("user_properties".into(), "{}".into()),
        ("version_name".into(), version.id.clone()),
        (
            "version_type".into(),
            version
                .version_type
                .clone()
                .unwrap_or_else(|| "release".into()),
        ),
        ("game_directory".into(), options.game_dir.clone()),
        ("assets_root".into(), assets.clone()),
        ("assets_index_name".into(), asset_id.into()),
        (
            "game_assets".into(),
            path::join(&assets, &format!("virtual/{asset_id}")),
        ),
        ("natives_directory".into(), options.natives_dir.clone()),
        (
            "library_directory".into(),
            path::join(&options.root, "libraries"),
        ),
        ("launcher_name".into(), options.launcher_name.clone()),
        ("launcher_version".into(), options.launcher_version.clone()),
        ("classpath".into(), cp.clone()),
        ("classpath_separator".into(), separator.to_string()),
        ("resolution_width".into(), options.width.to_string()),
        ("resolution_height".into(), options.height.to_string()),
        ("clientid".into(), String::new()),
        ("auth_xuid".into(), String::new()),
    ]);
    for (k, v) in &options.additional_variables {
        if matches!(
            k.as_str(),
            "auth_access_token"
                | "auth_uuid"
                | "classpath"
                | "game_directory"
                | "natives_directory"
        ) {
            return Err(format!("Cannot override protected launch variable {k}"));
        }
        vars.insert(k.clone(), v.clone());
    }
    let mut args = vec![
        format!("-Xmx{}M", options.memory_mib),
        "-Dfile.encoding=UTF-8".into(),
    ];
    if version.jvm_args.is_empty() {
        if env.os == "osx" {
            args.push("-XstartOnFirstThread".into());
        }
        args.push(format!("-Djava.library.path={}", options.natives_dir));
        args.push("-cp".into());
        args.push(cp.clone());
    } else {
        append_arguments(&mut args, &version.jvm_args, env, &vars)?;
        if !args
            .iter()
            .any(|s| matches!(s.as_str(), "-cp" | "-classpath" | "--class-path"))
        {
            args.push("-cp".into());
            args.push(cp);
        }
    }
    if let Some(log) = &version.logging {
        vars.insert(
            "path".into(),
            path::join(&options.root, &format!("assets/log_configs/{}", log.id)),
        );
        args.push(resolve_argument(&log.argument, &vars)?);
        vars.remove("path");
    }
    for extra in &options.extra_jvm {
        if extra.contains('\0') {
            return Err("NUL byte in custom JVM argument".into());
        }
        args.push(extra.clone());
    }
    let main = version
        .main_class
        .clone()
        .ok_or("Version has no mainClass after inheritance resolution")?;
    args.push(main);
    if let Some(legacy) = &version.legacy_args {
        for arg in split_legacy(legacy)? {
            args.push(resolve_argument(&arg, &vars)?);
        }
    }
    append_arguments(&mut args, &version.game_args, env, &vars)?;
    if version.legacy_args.is_none() && version.game_args.is_empty() {
        return Err("Version has no game arguments".into());
    }
    Ok(LaunchPlan {
        java: options.java.clone(),
        args,
        working_directory: options.game_dir.clone(),
        java_major: version.java_major.unwrap_or(8),
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AssetObject {
    pub name: String,
    pub hash: String,
    pub size: u64,
    pub path: String,
    pub url: String,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AssetPlan {
    pub objects: Vec<AssetObject>,
    pub virtual_assets: bool,
    pub map_to_resources: bool,
}
pub fn assets_plan(input: &[u8]) -> Result<AssetPlan, String> {
    json::with_json(input, |root| {
        let objects = root.get("objects").ok_or("Missing asset objects")?;
        if objects.kind() != json::Kind::Object {
            return Err("Asset objects must be an object".into());
        }
        let mut result = AssetPlan {
            virtual_assets: root
                .get("virtual")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            map_to_resources: root
                .get("map_to_resources")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            ..Default::default()
        };
        for (name, value) in objects.object() {
            let name =
                String::from_utf8(name.bytes().collect()).map_err(|_| "Invalid asset name")?;
            path::validate_relative(&name)?;
            let hash = json::req_str(value, "hash")?.to_ascii_lowercase();
            if !path::valid_sha1(&hash) {
                return Err("Invalid asset SHA-1".into());
            }
            let size = value
                .get("size")
                .and_then(|v| v.as_u64())
                .ok_or("Invalid asset size")?;
            let path = format!("assets/objects/{}/{}", &hash[..2], hash);
            let url = format!(
                "https://resources.download.minecraft.net/{}/{}",
                &hash[..2],
                hash
            );
            result.objects.push(AssetObject {
                name,
                hash,
                size,
                path,
                url,
            });
        }
        Ok(result)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Version {
        Version::parse(br#"{
            "id":"1.21.1","mainClass":"net.minecraft.client.main.Main",
            "javaVersion":{"majorVersion":21},
            "downloads":{"client":{"url":"https://example.org/game","size":100}},
            "assetIndex":{"id":"17","url":"https://example.org/assets"},
            "arguments":{
                "jvm":["-Djava.library.path=${natives_directory}","-cp","${classpath}"],
                "game":["--username","${auth_player_name}","--version","${version_name}",
                    "--gameDir","${game_directory}","--accessToken","${auth_access_token}",
                    {"rules":[{"action":"allow","features":{"is_demo_user":true}}],"value":"--demo"}]
            },
            "libraries":[
                {"name":"org.example:lib:1","downloads":{"artifact":{"path":"org/example/lib/1/lib-1.jar","url":"https://example.org/lib"}}},
                {"name":"org.lwjgl:lwjgl:3.3.3:natives-linux","downloads":{"artifact":{"url":"https://example.org/native"}},"rules":[{"action":"allow","os":{"name":"linux"}}]}
            ]
        }"#).unwrap()
    }
    #[test]
    fn launch_preserves_spaces_without_shell_interpretation() {
        let options = LaunchOptions {
            root: "/tmp/shared assets".into(),
            game_dir: "/tmp/my game $(echo nope)".into(),
            natives_dir: "/tmp/my game/natives".into(),
            access_token: "secret-token".into(),
            ..Default::default()
        };
        let plan = launch_plan(&fixture(), &Environment::new("linux", "x86_64"), &options).unwrap();
        assert_eq!(plan.java_major, 21);
        assert!(plan.args.contains(&options.game_dir));
        assert!(
            plan.args
                .contains(&"-Djava.library.path=/tmp/my game/natives".into())
        );
        assert!(
            plan.args
                .iter()
                .any(|s| s.contains("/tmp/shared assets/libraries/"))
        );
        assert!(!plan.args.contains(&"--demo".into()));
        assert!(plan.redacted_args().contains(&"<redacted>".into()));
        assert!(!plan.redacted_args().contains(&"secret-token".into()));
    }
    #[test]
    fn modern_and_legacy_natives_are_planned() {
        let mut version = fixture();
        let old=Version::parse(br#"{"id":"x","libraries":[{"name":"org.lwjgl:lwjgl:2","natives":{"linux":"natives-linux"},"downloads":{"classifiers":{"natives-linux":{"url":"https://example.org/old-native","path":"org/lwjgl/lwjgl/2/lwjgl-2-natives-linux.jar"}}},"extract":{"exclude":["META-INF/"]}}]}"#).unwrap();
        version.libraries.extend(old.libraries);
        let plan = install_plan(&version, &Environment::new("linux", "x86_64")).unwrap();
        assert_eq!(plan.natives.len(), 2);
        assert_eq!(plan.classpath.len(), 3);
        assert!(plan.natives[1].excludes.contains(&"META-INF/".into()));
    }
    #[test]
    fn legacy_parser_does_not_execute_and_handles_quoted_spaces() {
        assert_eq!(
            split_legacy(r#"--name "Hello World" --empty '' --literal '$(whoami)'"#).unwrap(),
            vec![
                "--name",
                "Hello World",
                "--empty",
                "",
                "--literal",
                "$(whoami)"
            ]
        );
        assert!(split_legacy("'unterminated").is_err());
    }
    #[test]
    fn unknown_template_and_conflicting_target_are_errors() {
        let mut version = fixture();
        version
            .game_args
            .push(Argument::Plain("${unknown_variable}".into()));
        assert!(
            launch_plan(
                &version,
                &Environment::new("linux", "x86_64"),
                &LaunchOptions::default()
            )
            .is_err()
        );
        version.libraries.push(version.libraries[0].clone());
        version
            .libraries
            .last_mut()
            .unwrap()
            .artifact
            .as_mut()
            .unwrap()
            .url = "https://example.org/different".into();
        assert!(install_plan(&version, &Environment::new("linux", "x86_64")).is_err());
    }
    #[test]
    fn parses_asset_paths_and_rejects_unsafe_index() {
        let p=assets_plan(br#"{"virtual":true,"objects":{"sounds/a.ogg":{"hash":"1234567890123456789012345678901234567890","size":42}}}"#).unwrap();
        assert!(p.virtual_assets);
        assert_eq!(
            p.objects[0].path,
            "assets/objects/12/1234567890123456789012345678901234567890"
        );
        assert!(assets_plan(br#"{"objects":{"../bad":{"hash":"1234567890123456789012345678901234567890","size":42}}}"#).is_err());
    }
}
