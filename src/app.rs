use crate::util::{self, Result};
use std::{env, fs, path::PathBuf};
use zmcl_core::json::{get_str, quote, req_str, with_json};

#[derive(Clone, Debug)]
pub struct App {
    pub root: PathBuf,
}

#[derive(Clone, Debug)]
pub struct Instance {
    pub id: String,
    pub name: String,
    pub version: String,
    pub loader: String,
    pub loader_version: String,
    pub memory_mib: u32,
    pub java: String,
    pub installed: bool,
}

fn validate_version_id(version: &str) -> Result<()> {
    if version.is_empty()
        || version.len() > 128
        || !version
            .bytes()
            .all(|x| x.is_ascii_alphanumeric() || b"-_. ".contains(&x))
    {
        return Err("Invalid Minecraft version ID".into());
    }
    Ok(())
}

impl Instance {
    pub fn json(&self) -> String {
        util::object(&[
            ("id", quote(&self.id)),
            ("name", quote(&self.name)),
            ("version", quote(&self.version)),
            ("loader", quote(&self.loader)),
            ("loader_version", quote(&self.loader_version)),
            ("memory_mib", self.memory_mib.to_string()),
            ("java", quote(&self.java)),
            ("installed", self.installed.to_string()),
        ])
    }
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        with_json(bytes, |v| {
            let id = req_str(v, "id")?;
            util::valid_id(&id)?;
            let version = req_str(v, "version")?;
            validate_version_id(&version)?;
            let loader = get_str(v, "loader").unwrap_or_else(|| "vanilla".into());
            if !matches!(
                loader.as_str(),
                "vanilla" | "fabric" | "quilt" | "forge" | "neoforge"
            ) {
                return Err("Unknown instance loader".into());
            }
            let memory = v.get("memory_mib").and_then(|x| x.as_u64()).unwrap_or(2048);
            if !(256..=131072).contains(&memory) {
                return Err("Memory must be 256–131072 MiB".into());
            }
            Ok(Self {
                name: get_str(v, "name").unwrap_or_else(|| id.clone()),
                id,
                version,
                loader,
                loader_version: get_str(v, "loader_version").unwrap_or_default(),
                memory_mib: memory as u32,
                java: get_str(v, "java").unwrap_or_default(),
                installed: v
                    .get("installed")
                    .and_then(|x| x.as_bool())
                    .unwrap_or(false),
            })
        })
    }
}
impl App {
    pub fn new(root: Option<String>) -> Result<Self> {
        let path = if let Some(root) = root.or_else(|| env::var("ZMCL_HOME").ok()) {
            PathBuf::from(root)
        } else {
            #[cfg(target_os = "windows")]
            {
                PathBuf::from(env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is unavailable")?)
                    .join("zmcl")
            }
            #[cfg(target_os = "macos")]
            {
                PathBuf::from(env::var_os("HOME").ok_or("HOME is unavailable")?)
                    .join("Library/Application Support/zmcl")
            }
            #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
            {
                env::var_os("XDG_DATA_HOME")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| {
                        PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".local/share")
                    })
                    .join("zmcl")
            }
        };
        fs::create_dir_all(&path).map_err(|e| e.to_string())?;
        let root = fs::canonicalize(&path).map_err(|e| e.to_string())?;
        for name in ["instances", "shared", "cache", "accounts", "locks"] {
            fs::create_dir_all(util::safe_join(&root, name)?).map_err(|e| e.to_string())?;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(root.join("accounts"), fs::Permissions::from_mode(0o700))
                .map_err(|e| e.to_string())?;
        }
        Ok(Self { root })
    }
    pub fn shared(&self) -> PathBuf {
        self.root.join("shared")
    }
    pub fn dir(&self, id: &str) -> Result<PathBuf> {
        util::valid_id(id)?;
        util::safe_join(&self.root, &format!("instances/{id}"))
    }
    pub fn read_instance(&self, id: &str) -> Result<Instance> {
        let path = util::safe_join(&self.dir(id)?, "instance.json")?;
        let instance = Instance::parse(&util::read_limited(&path, 64 * 1024)?)?;
        if instance.id != id {
            return Err("Instance metadata does not match its directory".into());
        }
        Ok(instance)
    }
    pub fn write_instance(&self, instance: &Instance) -> Result<()> {
        let path = util::safe_join(&self.dir(&instance.id)?, "instance.json")?;
        util::atomic_write(&path, instance.json().as_bytes())
    }
    pub fn create_instance(
        &self,
        id: &str,
        version: &str,
        loader: &str,
        loader_version: &str,
    ) -> Result<Instance> {
        if !matches!(
            loader,
            "vanilla" | "fabric" | "quilt" | "forge" | "neoforge"
        ) {
            return Err("Unsupported loader".into());
        }
        validate_version_id(version)?;
        let dir = self.dir(id)?;
        fs::create_dir(&dir).map_err(|e| format!("Cannot create instance '{id}': {e}"))?;
        let instance = Instance {
            id: id.into(),
            name: id.into(),
            version: version.into(),
            loader: loader.into(),
            loader_version: loader_version.into(),
            memory_mib: 2048,
            java: String::new(),
            installed: false,
        };
        let result = (|| {
            fs::create_dir(dir.join("game")).map_err(|e| e.to_string())?;
            self.write_instance(&instance)
        })();
        if let Err(e) = result {
            let _ = fs::remove_dir_all(&dir);
            return Err(e);
        }
        Ok(instance)
    }
    pub fn instances(&self) -> Result<Vec<Instance>> {
        let mut out = Vec::new();
        for entry in fs::read_dir(self.root.join("instances")).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
                continue;
            }
            let Some(id) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if id.starts_with('.') {
                continue;
            }
            out.push(self.read_instance(&id)?);
            if out.len() > 10_000 {
                return Err("Instance count exceeds 10000".into());
            }
        }
        out.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(out)
    }
}
