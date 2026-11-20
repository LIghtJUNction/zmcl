//! Manifest models, inheritance and Mojang platform rules.
use crate::{
    json::{self, Kind, Value},
    path,
};
use alloc::{
    collections::BTreeMap,
    format,
    string::{String, ToString},
    vec,
    vec::Vec,
};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Download {
    pub url: String,
    pub sha1: Option<String>,
    pub size: Option<u64>,
    pub path: Option<String>,
}
impl Download {
    pub fn parse(value: Value<'_, '_>) -> Result<Self, String> {
        Self::parse_with_local_artifact(value, false)
    }
    fn parse_library_artifact(value: Value<'_, '_>) -> Result<Self, String> {
        Self::parse_with_local_artifact(value, true)
    }
    fn parse_with_local_artifact(value: Value<'_, '_>, allow_local: bool) -> Result<Self, String> {
        object(value, "download")?;
        let raw_url = json::req_str(value, "url")?;
        let url = if allow_local && raw_url.is_empty() {
            String::new()
        } else {
            normalize_url(&raw_url)?
        };
        let sha1 = optional_string(value, "sha1")?;
        if sha1.as_deref().is_some_and(|s| !path::valid_sha1(s)) {
            return Err("Download SHA-1 must have 40 hexadecimal digits".into());
        }
        let size = optional_integer(value, "size")?;
        let file_path = optional_string(value, "path")?;
        if let Some(p) = &file_path {
            path::validate_relative(p)?;
        }
        if url.is_empty() && (sha1.is_none() || size.is_none_or(|n| n == 0) || file_path.is_none())
        {
            return Err("Local processor artifacts require a path, SHA-1 and nonzero size".into());
        }
        Ok(Self {
            url,
            sha1: sha1.map(|s| s.to_ascii_lowercase()),
            size,
            path: file_path,
        })
    }
}

/// Only HTTPS URLs are accepted; obsolete first-party HTTP metadata is upgraded.
pub fn normalize_url(url: &str) -> Result<String, String> {
    if url.len() > 8192 || url.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err("Invalid download URL".into());
    }
    let url = if let Some(tail) = url.strip_prefix("http://libraries.minecraft.net/") {
        format!("https://libraries.minecraft.net/{tail}")
    } else if let Some(tail) = url.strip_prefix("http://resources.download.minecraft.net/") {
        format!("https://resources.download.minecraft.net/{tail}")
    } else {
        url.to_string()
    };
    let rest = url
        .strip_prefix("https://")
        .ok_or("Downloads require HTTPS")?;
    let authority = rest.split('/').next().unwrap_or("");
    if authority.is_empty() || authority.contains(['@', '\\', '#', '?']) {
        return Err("Invalid HTTPS authority".into());
    }
    Ok(url)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AssetIndex {
    pub id: String,
    pub download: Download,
    pub total_size: Option<u64>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Logging {
    pub id: String,
    pub argument: String,
    pub file: Download,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Rule {
    pub allow: bool,
    pub os: Option<String>,
    pub arch: Option<String>,
    pub os_version: Option<String>,
    pub features: BTreeMap<String, bool>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Argument {
    Plain(String),
    Conditional {
        rules: Vec<Rule>,
        values: Vec<String>,
    },
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Library {
    pub name: String,
    pub artifact: Option<Download>,
    pub classifiers: BTreeMap<String, Download>,
    pub natives: BTreeMap<String, String>,
    pub extract_excludes: Vec<String>,
    pub rules: Vec<Rule>,
    pub client_required: bool,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Version {
    pub id: String,
    pub inherits_from: Option<String>,
    pub main_class: Option<String>,
    pub java_major: Option<u32>,
    pub client: Option<Download>,
    /// A merged loader keeps the base game's JAR location.
    pub client_id: Option<String>,
    pub asset_index: Option<AssetIndex>,
    pub asset_id: Option<String>,
    pub libraries: Vec<Library>,
    pub jvm_args: Vec<Argument>,
    pub game_args: Vec<Argument>,
    pub legacy_args: Option<String>,
    pub logging: Option<Logging>,
    pub version_type: Option<String>,
}
impl Version {
    pub fn parse(input: &[u8]) -> Result<Self, String> {
        json::with_json(input, Self::from_value)
    }
    pub fn from_value(value: Value<'_, '_>) -> Result<Self, String> {
        object(value, "version")?;
        let id = json::req_str(value, "id")?;
        path::validate_id(&id)?;
        let inherits_from = optional_string(value, "inheritsFrom")?;
        if let Some(s) = &inherits_from {
            path::validate_id(s)?;
        }
        if inherits_from.as_deref() == Some(&id) {
            return Err("Version cannot inherit itself".into());
        }
        let main_class = optional_string(value, "mainClass")?;
        if main_class.as_deref().is_some_and(|s| {
            s.is_empty()
                || s.len() > 512
                || !s
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._$".contains(&b))
        }) {
            return Err("Invalid Java main class".into());
        }
        let java_major = if let Some(v) = value.get("javaVersion") {
            object(v, "javaVersion")?;
            let n = v
                .get("majorVersion")
                .and_then(|v| v.as_u64())
                .ok_or("Invalid javaVersion.majorVersion")?;
            if !(8..=255).contains(&n) {
                return Err("Unsupported Java major version".into());
            }
            Some(n as u32)
        } else {
            None
        };
        let client = if let Some(downloads) = value.get("downloads") {
            object(downloads, "downloads")?;
            downloads.get("client").map(Download::parse).transpose()?
        } else {
            None
        };
        let mut client_id = optional_string(value, "jar")?;
        if client_id.is_none() && client.is_some() {
            client_id = Some(id.clone());
        }
        if let Some(s) = &client_id {
            path::validate_id(s)?;
        }
        let asset_id = optional_string(value, "assets")?;
        if let Some(s) = &asset_id {
            path::validate_id(s)?;
        }
        let asset_index = if let Some(index) = value.get("assetIndex") {
            let id = json::req_str(index, "id")?;
            path::validate_id(&id)?;
            Some(AssetIndex {
                id,
                download: Download::parse(index)?,
                total_size: optional_integer(index, "totalSize")?,
            })
        } else {
            None
        };
        let libraries = match value.get("libraries") {
            Some(v) => {
                array(v, "libraries")?;
                v.array()
                    .map(parse_library)
                    .collect::<Result<Vec<_>, _>>()?
            }
            None => Vec::new(),
        };
        if libraries.len() > 16384 {
            return Err("Too many libraries".into());
        }
        let (jvm_args, game_args) = if let Some(args) = value.get("arguments") {
            object(args, "arguments")?;
            (
                parse_arguments(args.get("jvm"))?,
                parse_arguments(args.get("game"))?,
            )
        } else {
            (Vec::new(), Vec::new())
        };
        let legacy_args = optional_string(value, "minecraftArguments")?;
        let logging = if let Some(log) = value.get("logging") {
            object(log, "logging")?;
            if let Some(client) = log.get("client") {
                object(client, "logging.client")?;
                let file = client.get("file").ok_or("Missing logging file")?;
                let id = json::req_str(file, "id")?;
                path::validate_relative(&id)?;
                if id.contains('/') {
                    return Err("Logging id must be a filename".into());
                }
                Some(Logging {
                    id,
                    argument: json::req_str(client, "argument")?,
                    file: Download::parse(file)?,
                })
            } else {
                None
            }
        } else {
            None
        };
        Ok(Self {
            id,
            inherits_from,
            main_class,
            java_major,
            client,
            client_id,
            asset_index,
            asset_id,
            libraries,
            jvm_args,
            game_args,
            legacy_args,
            logging,
            version_type: optional_string(value, "type")?,
        })
    }
}

fn parse_library(value: Value<'_, '_>) -> Result<Library, String> {
    object(value, "library")?;
    let name = json::req_str(value, "name")?;
    let maven_path = path::maven_path(&name)?;
    let mut classifiers = BTreeMap::new();
    let artifact = if let Some(downloads) = value.get("downloads") {
        object(downloads, "library.downloads")?;
        if let Some(cs) = downloads.get("classifiers") {
            object(cs, "classifiers")?;
            for (key, v) in cs.object() {
                let key =
                    String::from_utf8(key.bytes().collect()).map_err(|_| "Invalid classifier")?;
                path::validate_id(&key)?;
                let mut d = Download::parse(v)?;
                if d.path.is_none() {
                    let (base, ext) = name.split_once('@').unwrap_or((&name, "jar"));
                    let parts: Vec<_> = base.split(':').take(3).collect();
                    d.path = Some(path::maven_path(&format!(
                        "{}:{}@{ext}",
                        parts.join(":"),
                        key
                    ))?);
                }
                classifiers.insert(key, d);
            }
        }
        downloads
            .get("artifact")
            .map(Download::parse_library_artifact)
            .transpose()?
    } else {
        let base = optional_string(value, "url")?
            .unwrap_or_else(|| "https://libraries.minecraft.net/".into());
        Some(Download {
            url: normalize_url(&format!("{}/{}", base.trim_end_matches('/'), maven_path))?,
            path: Some(maven_path.clone()),
            ..Default::default()
        })
    };
    let artifact = artifact.map(|mut d| {
        if d.path.is_none() {
            d.path = Some(maven_path);
        }
        d
    });
    let mut natives = BTreeMap::new();
    if let Some(v) = value.get("natives") {
        object(v, "natives")?;
        for (key, native) in v.object() {
            let key = String::from_utf8(key.bytes().collect()).map_err(|_| "Invalid OS name")?;
            natives.insert(
                key,
                json::string(native).ok_or("Native classifier must be a string")?,
            );
        }
    }
    let mut extract_excludes = Vec::new();
    if let Some(extract) = value.get("extract") {
        object(extract, "extract")?;
        if let Some(excludes) = extract.get("exclude") {
            array(excludes, "extract.exclude")?;
            for v in excludes.array() {
                let s = json::string(v).ok_or("Invalid extract exclusion")?;
                path::validate_relative(s.trim_end_matches('/'))?;
                extract_excludes.push(s);
            }
        }
    }
    let client_required = optional_boolean(value, "clientreq")?.unwrap_or(true);
    Ok(Library {
        name,
        artifact,
        classifiers,
        natives,
        extract_excludes,
        rules: parse_rules(value.get("rules"))?,
        client_required,
    })
}

pub fn parse_rules(value: Option<Value<'_, '_>>) -> Result<Vec<Rule>, String> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    array(value, "rules")?;
    let mut result = Vec::new();
    for r in value.array() {
        object(r, "rule")?;
        let allow = match json::req_str(r, "action")?.as_str() {
            "allow" => true,
            "disallow" => false,
            _ => return Err("Unknown rule action".into()),
        };
        let mut rule = Rule {
            allow,
            ..Default::default()
        };
        if let Some(os) = r.get("os") {
            object(os, "rule.os")?;
            rule.os = optional_string(os, "name")?;
            rule.arch = optional_string(os, "arch")?;
            rule.os_version = optional_string(os, "version")?;
        }
        if let Some(features) = r.get("features") {
            object(features, "rule.features")?;
            for (key, v) in features.object() {
                let key =
                    String::from_utf8(key.bytes().collect()).map_err(|_| "Invalid feature name")?;
                rule.features
                    .insert(key, v.as_bool().ok_or("Feature rule must be boolean")?);
            }
        }
        result.push(rule);
    }
    Ok(result)
}
fn parse_arguments(value: Option<Value<'_, '_>>) -> Result<Vec<Argument>, String> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    array(value, "arguments")?;
    let mut result = Vec::new();
    for v in value.array() {
        if let Some(s) = json::string(v) {
            result.push(Argument::Plain(s));
            continue;
        }
        object(v, "conditional argument")?;
        let value = v.get("value").ok_or("Missing conditional argument value")?;
        let values = if let Some(s) = json::string(value) {
            vec![s]
        } else {
            array(value, "argument.value")?;
            value
                .array()
                .map(|v| json::string(v).ok_or_else(|| "Argument must be a string".to_string()))
                .collect::<Result<_, _>>()?
        };
        result.push(Argument::Conditional {
            rules: parse_rules(v.get("rules"))?,
            values,
        });
    }
    Ok(result)
}
fn object(value: Value<'_, '_>, name: &str) -> Result<(), String> {
    if value.kind() != Kind::Object {
        Err(format!("{name} must be an object"))
    } else {
        Ok(())
    }
}
fn array(value: Value<'_, '_>, name: &str) -> Result<(), String> {
    if value.kind() != Kind::Array {
        Err(format!("{name} must be an array"))
    } else {
        Ok(())
    }
}
fn optional_string(value: Value<'_, '_>, key: &str) -> Result<Option<String>, String> {
    value
        .get(key)
        .map(|v| json::string(v).ok_or_else(|| format!("{key} must be a string")))
        .transpose()
}
fn optional_integer(value: Value<'_, '_>, key: &str) -> Result<Option<u64>, String> {
    value
        .get(key)
        .map(|v| {
            v.as_u64()
                .ok_or_else(|| format!("{key} must be an unsigned integer"))
        })
        .transpose()
}
fn optional_boolean(value: Value<'_, '_>, key: &str) -> Result<Option<bool>, String> {
    value
        .get(key)
        .map(|v| v.as_bool().ok_or_else(|| format!("{key} must be boolean")))
        .transpose()
}

/// Merge one direct parent. The host resolves the chain and rejects repeated IDs.
pub fn merge(mut parent: Version, child: Version) -> Result<Version, String> {
    if let Some(expected) = &child.inherits_from
        && expected != &parent.id
    {
        return Err(format!("Expected parent {expected}, got {}", parent.id));
    }
    if parent.id == child.id {
        return Err("Inheritance cycle".into());
    }
    let old_id = parent.id.clone();
    parent.id = child.id;
    parent.inherits_from = None;
    if child.main_class.is_some() {
        parent.main_class = child.main_class;
    }
    if child.java_major.is_some() {
        parent.java_major = child.java_major;
    }
    if child.client.is_some() {
        parent.client = child.client;
    }
    if child.client_id.is_some() {
        parent.client_id = child.client_id;
    }
    if parent.client_id.is_none() && parent.client.is_some() {
        parent.client_id = Some(old_id);
    }
    if child.asset_index.is_some() {
        parent.asset_index = child.asset_index;
    }
    if child.asset_id.is_some() {
        parent.asset_id = child.asset_id;
    }
    if child.logging.is_some() {
        parent.logging = child.logging;
    }
    if child.version_type.is_some() {
        parent.version_type = child.version_type;
    }
    if child.legacy_args.is_some() {
        parent.legacy_args = child.legacy_args;
        parent.game_args.clear();
    }
    if !child.game_args.is_empty() {
        // Legacy parents carry their base arguments separately; modern child arguments append.
        parent.game_args.extend(child.game_args);
    }
    parent.jvm_args.extend(child.jvm_args);
    for lib in child.libraries {
        let key = path::maven_key(&lib.name)?;
        let matching = parent
            .libraries
            .iter()
            .position(|v| path::maven_key(&v.name).ok().as_ref() == Some(&key));
        if let Some(index) = matching {
            parent.libraries[index] = lib;
        } else {
            parent.libraries.push(lib);
        }
    }
    Ok(parent)
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Environment {
    /// Mojang names: linux, windows, osx.
    pub os: String,
    /// CPU architecture, e.g. x86_64, x86 or aarch64.
    pub arch: String,
    pub os_version: String,
    pub features: BTreeMap<String, bool>,
}
impl Environment {
    pub fn new(os: &str, arch: &str) -> Self {
        let os = match os {
            "macos" => "osx",
            other => other,
        };
        Self {
            os: os.into(),
            arch: canonical_arch(arch).into(),
            ..Default::default()
        }
    }
    pub fn host() -> Self {
        Self::new(
            if cfg!(target_os = "windows") {
                "windows"
            } else if cfg!(target_os = "macos") {
                "osx"
            } else {
                "linux"
            },
            if cfg!(target_arch = "aarch64") {
                "aarch64"
            } else if cfg!(target_arch = "x86") {
                "x86"
            } else {
                "x86_64"
            },
        )
    }
    pub fn rules_allow(&self, rules: &[Rule]) -> Result<bool, String> {
        if rules.is_empty() {
            return Ok(true);
        }
        let mut allow = false;
        for rule in rules {
            if rule.os.as_ref().is_some_and(|s| s != &self.os) {
                continue;
            }
            if rule
                .arch
                .as_deref()
                .is_some_and(|s| canonical_arch(s) != canonical_arch(&self.arch))
            {
                continue;
            }
            if let Some(pattern) = &rule.os_version
                && !version_matches(pattern, &self.os_version)?
            {
                continue;
            }
            if rule.features.iter().any(|(key, required)| {
                self.features.get(key).copied().unwrap_or(false) != *required
            }) {
                continue;
            }
            allow = rule.allow;
        }
        Ok(allow)
    }
}
fn canonical_arch(arch: &str) -> &str {
    match arch {
        "amd64" | "x64" => "x86_64",
        "i386" | "i486" | "i586" | "i686" => "x86",
        "arm64" => "aarch64",
        other => other,
    }
}

/// Bounded regex subset needed by Mojang OS-version rules.
/// Unsupported syntax is an error, never an accidental allow.
fn version_matches(pattern: &str, text: &str) -> Result<bool, String> {
    #[derive(Clone)]
    enum Atom {
        Any,
        Digit,
        Literal(u8),
        Class(Vec<(u8, u8)>),
    }
    impl Atom {
        fn matches(&self, b: u8) -> bool {
            match self {
                Self::Any => true,
                Self::Digit => b.is_ascii_digit(),
                Self::Literal(v) => *v == b,
                Self::Class(ranges) => ranges.iter().any(|(a, z)| (*a..=*z).contains(&b)),
            }
        }
    }
    if pattern.len() > 256 || text.len() > 256 || !pattern.is_ascii() || !text.is_ascii() {
        return Err("OS version rule exceeds supported bounds".into());
    }
    let mut p = pattern.as_bytes();
    let start_anchor = p.first() == Some(&b'^');
    if start_anchor {
        p = &p[1..];
    }
    let end_anchor = p.last() == Some(&b'$') && (p.len() < 2 || p[p.len() - 2] != b'\\');
    if end_anchor {
        p = &p[..p.len() - 1];
    }
    let mut atoms = Vec::new();
    let mut i = 0;
    while i < p.len() {
        let atom = match p[i] {
            b'.' => {
                i += 1;
                Atom::Any
            }
            b'\\' => {
                i += 1;
                let b = *p.get(i).ok_or("Incomplete OS version escape")?;
                i += 1;
                if b == b'd' {
                    Atom::Digit
                } else if b".\\^$[]*+?()-".contains(&b) {
                    Atom::Literal(b)
                } else {
                    return Err("Unsupported OS version escape".into());
                }
            }
            b'[' => {
                i += 1;
                let mut ranges = Vec::new();
                while p.get(i) != Some(&b']') {
                    let a = *p.get(i).ok_or("Unterminated OS version class")?;
                    i += 1;
                    if a == b'^' || a == b'\\' {
                        return Err("Unsupported OS version character class".into());
                    }
                    let z = if p.get(i) == Some(&b'-') && p.get(i + 1) != Some(&b']') {
                        i += 1;
                        let z = *p.get(i).ok_or("Incomplete class")?;
                        i += 1;
                        z
                    } else {
                        a
                    };
                    if a > z {
                        return Err("Invalid character range".into());
                    }
                    ranges.push((a, z));
                }
                i += 1;
                if ranges.is_empty() {
                    return Err("Empty character class".into());
                }
                Atom::Class(ranges)
            }
            b'(' | b')' | b'{' | b'}' | b'|' | b'^' | b'$' | b'*' | b'+' | b'?' => {
                return Err(format!("Unsupported OS version pattern: {pattern}"));
            }
            b => {
                i += 1;
                Atom::Literal(b)
            }
        };
        let repeat = match p.get(i) {
            Some(b'*') => {
                i += 1;
                0
            }
            Some(b'+') => {
                i += 1;
                1
            }
            Some(b'?') => {
                i += 1;
                2
            }
            _ => 3,
        };
        atoms.push((atom, repeat));
    }
    let text = text.as_bytes();
    let mut reachable = vec![false; text.len() + 1];
    if start_anchor {
        reachable[0] = true;
    } else {
        reachable.fill(true);
    }
    for (atom, repeat) in atoms {
        let previous = reachable;
        reachable = vec![false; text.len() + 1];
        for j in 0..=text.len() {
            if repeat == 0 || repeat == 2 {
                reachable[j] |= previous[j];
            }
            if j > 0 && atom.matches(text[j - 1]) {
                reachable[j] |= previous[j - 1];
                if repeat <= 1 {
                    reachable[j] |= reachable[j - 1];
                }
            }
        }
    }
    Ok(if end_anchor {
        reachable[text.len()]
    } else {
        reachable.into_iter().any(|v| v)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ordered_platform_and_feature_rules() {
        let env = Environment::new("linux", "x86_64");
        let rules = vec![
            Rule {
                allow: true,
                ..Default::default()
            },
            Rule {
                allow: false,
                os: Some("windows".into()),
                ..Default::default()
            },
        ];
        assert!(env.rules_allow(&rules).unwrap());
        assert!(
            !Environment::new("windows", "amd64")
                .rules_allow(&rules)
                .unwrap()
        );
        let rules = vec![Rule {
            allow: true,
            features: BTreeMap::from([("is_demo_user".into(), true)]),
            ..Default::default()
        }];
        assert!(!env.rules_allow(&rules).unwrap());
    }
    #[test]
    fn os_regex_is_bounded_and_fail_closed() {
        assert!(version_matches(r"^10\.", "10.0.26100").unwrap());
        assert!(!version_matches(r"^10\.", "110.0").unwrap());
        assert!(version_matches(r"^10\.5\.\d$", "10.5.9").unwrap());
        assert!(!version_matches(r"^10\.5\.\d$", "10.5.10").unwrap());
        assert!(version_matches(r"^1[0-2]+\.", "112.3").unwrap());
        assert!(version_matches("(a|b)", "a").is_err());
    }
    #[test]
    fn merge_replaces_library_version_and_preserves_base_jar() {
        let parent=Version::parse(br#"{"id":"1.21.1","mainClass":"net.minecraft.client.main.Main","downloads":{"client":{"url":"https://example.org/client","sha1":"0000000000000000000000000000000000000000"}},"libraries":[{"name":"a:b:1"}]}"#).unwrap();
        let child=Version::parse(br#"{"id":"fabric-1.21.1","inheritsFrom":"1.21.1","mainClass":"net.fabricmc.loader.impl.launch.knot.KnotClient","libraries":[{"name":"a:b:2","url":"https://maven.fabricmc.net/"}]}"#).unwrap();
        let merged = merge(parent, child).unwrap();
        assert_eq!(merged.client_id.as_deref(), Some("1.21.1"));
        assert_eq!(merged.libraries.len(), 1);
        assert_eq!(merged.libraries[0].name, "a:b:2");
    }
    #[test]
    fn malicious_metadata_is_rejected() {
        for json in [
            r#"{"id":"../evil"}"#,
            r#"{"id":"x","inheritsFrom":"x"}"#,
            r#"{"id":"x","libraries":[{"name":"a:b:1","downloads":{"artifact":{"url":"https://x/a","path":"../../bad"}}}]}"#,
            r#"{"id":"x","downloads":{"client":{"url":"file:///tmp/game.jar"}}}"#,
            r#"{"id":"x","javaVersion":{"majorVersion":null}}"#,
            r#"{"id":"x","arguments":{"game":[{"value":123}]}}"#,
        ] {
            assert!(Version::parse(json.as_bytes()).is_err(), "{json}");
        }
    }
    #[test]
    fn generated_loader_artifacts_require_verified_local_content() {
        let profile = br#"{"id":"forge-fixture","inheritsFrom":"1.21.1","libraries":[{"name":"net.minecraftforge:forge:1.21.1-52.1.20:client","downloads":{"artifact":{"path":"net/minecraftforge/forge/1.21.1-52.1.20/forge-1.21.1-52.1.20-client.jar","url":"","sha1":"28f7acaa0e9b1c2d2a7bf52c07ae16133bd3da4e","size":28056385}}}]}"#;
        let version = Version::parse(profile).unwrap();
        assert_eq!(version.libraries[0].artifact.as_ref().unwrap().url, "");
        for body in [
            r#"{"url":"","sha1":"0000000000000000000000000000000000000000","size":1,"path":"x.jar"}"#,
            r#"{"url":"","size":1,"path":"x.jar"}"#,
        ] {
            assert!(json::with_json(body.as_bytes(), Download::parse).is_err());
        }
        for artifact in [
            r#"{"url":"","size":1,"path":"x.jar"}"#,
            r#"{"url":"","sha1":"0000000000000000000000000000000000000000","size":1}"#,
            r#"{"url":"","sha1":"0000000000000000000000000000000000000000","size":0,"path":"x.jar"}"#,
            r#"{"url":"","sha1":"0000000000000000000000000000000000000000","size":1,"path":"../x.jar"}"#,
        ] {
            assert!(
                json::with_json(artifact.as_bytes(), Download::parse_library_artifact).is_err()
            );
        }
    }
}
