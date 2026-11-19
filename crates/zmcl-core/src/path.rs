//! Portable path validation. The host must additionally reject symlink traversal.
use alloc::{
    format,
    string::{String, ToString},
    vec::Vec,
};

fn segment(segment: &str) -> bool {
    if segment.is_empty()
        || segment == "."
        || segment == ".."
        || segment.ends_with(['.', ' '])
        || segment
            .chars()
            .any(|c| c.is_control() || "\\/:*?\"<>|".contains(c))
    {
        return false;
    }
    let stem = segment.split('.').next().unwrap_or("").to_ascii_uppercase();
    !(matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CLOCK$")
        || (stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && matches!(stem.as_bytes()[3], b'1'..=b'9')))
}
pub fn validate_id(id: &str) -> Result<(), String> {
    if id.len() > 128
        || !segment(id)
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-+".contains(&b))
    {
        Err("Identifier must be 1..128 portable ASCII letters, digits, dots, underscores, '+' or '-'".into())
    } else {
        Ok(())
    }
}
pub fn validate_relative(path: &str) -> Result<(), String> {
    if path.len() > 4096 || !path.split('/').all(segment) {
        return Err(format!("Unsafe relative path: {path:?}"));
    }
    Ok(())
}
pub fn valid_sha1(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| b.is_ascii_hexdigit())
}
pub fn valid_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// A library coordinate: group:artifact:version[:classifier][@extension].
pub fn maven_path(coordinate: &str) -> Result<String, String> {
    let (base, extension) = coordinate.split_once('@').unwrap_or((coordinate, "jar"));
    let parts: Vec<_> = base.split(':').collect();
    if !(parts.len() == 3 || parts.len() == 4)
        || parts.iter().any(|p| !segment(p))
        || !segment(extension)
        || extension.contains('@')
    {
        return Err(format!("Invalid Maven coordinate: {coordinate:?}"));
    }
    if parts[0].split('.').any(|p| !segment(p)) {
        return Err("Invalid Maven group".into());
    }
    let classifier = if parts.len() == 4 {
        format!("-{}", parts[3])
    } else {
        String::new()
    };
    let path = format!(
        "{}/{}/{}/{}-{}{}.{}",
        parts[0].replace('.', "/"),
        parts[1],
        parts[2],
        parts[1],
        parts[2],
        classifier,
        extension
    );
    validate_relative(&path)?;
    Ok(path)
}
pub fn maven_key(coordinate: &str) -> Result<String, String> {
    let _ = maven_path(coordinate)?;
    let (base, ext) = coordinate.split_once('@').unwrap_or((coordinate, "jar"));
    let parts: Vec<_> = base.split(':').collect();
    Ok(format!(
        "{}:{}:{}@{}",
        parts[0],
        parts[1],
        parts.get(3).unwrap_or(&""),
        ext
    ))
}
pub fn join(root: &str, path: &str) -> String {
    if root.is_empty() {
        return path.to_string();
    }
    format!("{}/{}", root.trim_end_matches(['/', '\\']), path)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn blocks_cross_platform_traversal_and_special_files() {
        for p in [
            "",
            "/tmp/x",
            "../x",
            "a/../x",
            "a//b",
            "./x",
            "C:/x",
            "a\\b",
            "nul",
            "AUX.txt",
            "a/COM1.jar",
            "a/x.",
            "a/x ",
            "a/\0x",
        ] {
            assert!(validate_relative(p).is_err(), "{p:?}");
        }
        for p in ["libraries/a.jar", "资源/声音.ogg", "mods/a-b_1+2.jar"] {
            assert!(validate_relative(p).is_ok(), "{p:?}");
        }
    }
    #[test]
    fn maven_builds_and_distinguishes_classifiers() {
        assert_eq!(
            maven_path("org.lwjgl:lwjgl:3.3.3:natives-linux").unwrap(),
            "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-linux.jar"
        );
        assert_eq!(maven_path("x.y:a:1@zip").unwrap(), "x/y/a/1/a-1.zip");
        assert_eq!(maven_key("x:a:1").unwrap(), maven_key("x:a:2").unwrap());
        assert_ne!(
            maven_key("x:a:1").unwrap(),
            maven_key("x:a:1:other").unwrap()
        );
        assert!(maven_path("x:../a:1").is_err());
    }
}
