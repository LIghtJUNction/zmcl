//! Vanilla local servers and the Java edition status protocol.
//! Framing reference: Voxelum/minecraft-launcher-core-node packages/client/status.ts.
use crate::{
    App, net, runtime, store,
    util::{self, Result},
};
use std::{
    fs,
    io::{Read, Write},
    net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs},
    path::PathBuf,
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use zmcl_core::{
    json::{self, get_str, quote, req_str, with_json},
    path,
};
const MANIFEST: &str = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json";
const PROCESS: &str = include_str!("server/process.py");
const MAX_PACKET: usize = 1024 * 1024;

#[derive(Clone)]
struct Config {
    id: String,
    version: String,
    memory_mib: u32,
    java: String,
    installed: bool,
    java_major: u32,
    sha1: String,
    jar_size: u64,
}
impl Config {
    fn json(&self) -> String {
        util::object(&[
            ("schema", "1".into()),
            ("id", quote(&self.id)),
            ("version", quote(&self.version)),
            ("memory_mib", self.memory_mib.to_string()),
            ("java", quote(&self.java)),
            ("installed", self.installed.to_string()),
            ("java_major", self.java_major.to_string()),
            ("server_sha1", quote(&self.sha1)),
            ("jar_size", self.jar_size.to_string()),
        ])
    }
}
fn directory(app: &App, id: &str) -> Result<PathBuf> {
    util::valid_id(id)?;
    util::safe_join(&app.root, &format!("servers/{id}"))
}
fn read(app: &App, id: &str) -> Result<Config> {
    let dir = directory(app, id)?;
    let bytes = util::read_limited(&util::safe_join(&dir, "server.json")?, 64 * 1024)?;
    with_json(&bytes, |v| {
        let stored = req_str(v, "id")?;
        if stored != id {
            return Err("Server ID does not match its directory".into());
        }
        let version = req_str(v, "version")?;
        path::validate_id(&version)?;
        let memory = v.get("memory_mib").and_then(|v| v.as_u64()).unwrap_or(2048);
        if !(128..=1_048_576).contains(&memory) {
            return Err("Invalid server memory budget".into());
        }
        let major = v.get("java_major").and_then(|v| v.as_u64()).unwrap_or(0);
        if major > 255 {
            return Err("Invalid server Java requirement".into());
        }
        Ok(Config {
            id: stored,
            version,
            memory_mib: memory as u32,
            java: get_str(v, "java").unwrap_or_default(),
            installed: v
                .get("installed")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            java_major: major as u32,
            sha1: get_str(v, "server_sha1").unwrap_or_default(),
            jar_size: v.get("jar_size").and_then(|v| v.as_u64()).unwrap_or(0),
        })
    })
}
fn save(app: &App, config: &Config) -> Result<()> {
    util::atomic_write(
        &util::safe_join(&directory(app, &config.id)?, "server.json")?,
        config.json().as_bytes(),
    )
}
fn process(app: &App, id: &str, action: &str, body: Option<&str>) -> Result<String> {
    let dir = directory(app, id)?;
    if !matches!(action, "status" | "start" | "stop") {
        return Err("Invalid server operation".into());
    }
    let mut child = Command::new("python3")
        .args(["-c", PROCESS, action])
        .arg(&dir)
        .stdin(if body.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Managed server lifecycle requires system Python 3: {e}"))?;
    if let Some(body) = body
        && child
            .stdin
            .take()
            .ok_or("Missing server helper input")?
            .write_all(body.as_bytes())
            .is_err()
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Cannot send server launch options".into());
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if output.stdout.len() > 64 * 1024 || output.stderr.len() > 16 * 1024 {
        return Err("Server helper response exceeds limit".into());
    }
    if !output.status.success() {
        return Err(with_json(&output.stdout, |v| req_str(v, "error"))
            .unwrap_or_else(|_| "Server lifecycle helper failed".into()));
    }
    with_json(&output.stdout, |v| {
        if v.kind() != json::Kind::Object {
            return Err("Invalid server process response".into());
        }
        Ok(json::raw(v))
    })
}
fn ensure_stopped(app: &App, id: &str) -> Result<()> {
    let state = process(app, id, "status", None)?;
    with_json(state.as_bytes(), |v| {
        if get_str(v, "state").as_deref() != Some("stopped") {
            return Err("Server has an active or uncertain process; inspect status and stop it before changing its files".into());
        }
        Ok(())
    })
}
fn eula_accepted(bytes: &[u8]) -> Result<bool> {
    let text = std::str::from_utf8(bytes).map_err(|_| "eula.txt must be UTF-8")?;
    let mut accepted = false;
    for line in text.lines().map(str::trim) {
        if line.starts_with(['#', '!']) {
            continue;
        }
        if let Some((key, value)) = line.split_once('=')
            && key.trim() == "eula"
        {
            accepted = value.trim() == "true";
        }
    }
    Ok(accepted)
}

/// Create only configuration. The EULA remains explicitly unaccepted.
pub fn create(app: &App, id: &str, version: &str) -> Result<String> {
    util::valid_id(id)?;
    path::validate_id(version)?;
    let _lock = util::Lock::acquire(&app.root, &format!("server-{id}"))?;
    let destination = directory(app, id)?;
    if destination.exists() {
        return Err("Server directory already exists".into());
    }
    let parent = util::safe_join(&app.root, "servers")?;
    fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let stage = util::safe_join(
        &parent,
        &format!(".create-{id}-{}-{nonce}", std::process::id()),
    )?;
    fs::create_dir(&stage).map_err(|e| e.to_string())?;
    let config = Config {
        id: id.into(),
        version: version.into(),
        memory_mib: 2048,
        java: String::new(),
        installed: false,
        java_major: 0,
        sha1: String::new(),
        jar_size: 0,
    };
    let result = (|| {
        util::atomic_write(&stage.join("server.json"), config.json().as_bytes())?;
        util::atomic_write(&stage.join("eula.txt"),b"# Read https://aka.ms/MinecraftEULA before changing this setting.\n# zmcl never accepts the Minecraft EULA for you.\neula=false\n")?;
        util::atomic_write(&stage.join("server.properties"),b"# Local-only default. Edit deliberately to allow remote connections.\nserver-ip=127.0.0.1\nserver-port=25565\nonline-mode=true\nenable-status=true\nenable-rcon=false\nmotd=zmcl local server\n")?;
        fs::rename(&stage, &destination).map_err(|e| e.to_string())?;
        Ok(config.json())
    })();
    if result.is_err() && stage.is_dir() {
        let _ = fs::remove_dir_all(stage);
    }
    result
}

pub fn install(app: &App, id: &str) -> Result<String> {
    let _lock = util::Lock::acquire(&app.root, &format!("server-{id}"))?;
    let mut config = read(app, id)?;
    ensure_stopped(app, id)?;
    let manifest = net::get(MANIFEST)?;
    let (url, sha1) = with_json(&manifest, |v| {
        for entry in v
            .get("versions")
            .ok_or("Missing Mojang version manifest")?
            .array()
        {
            if get_str(entry, "id").as_deref() == Some(&config.version) {
                return Ok((req_str(entry, "url")?, req_str(entry, "sha1")?));
            }
        }
        Err("Minecraft version was not found in the official manifest".into())
    })?;
    if !path::valid_sha1(&sha1) {
        return Err("Invalid official version digest".into());
    }
    let metadata = store::ensure(
        app,
        &format!("versions/{0}/{0}.json", config.version),
        &url,
        Some(&sha1),
        None,
        None,
    )?;
    let bytes = util::read_limited(&metadata, 16 * 1024 * 1024)?;
    let (download, major) = with_json(&bytes, |v| {
        if get_str(v, "id").as_deref() != Some(&config.version) {
            return Err("Server version metadata ID mismatch".into());
        }
        let server = v
            .get("downloads")
            .and_then(|d| d.get("server"))
            .ok_or("This official version has no server download")?;
        let download = zmcl_core::model::Download::parse(server)?;
        if download.sha1.is_none() || download.size.is_none_or(|n| n == 0) {
            return Err("Server download has no trusted digest and size".into());
        }
        let major = v
            .get("javaVersion")
            .and_then(|j| j.get("majorVersion"))
            .and_then(|n| n.as_u64())
            .unwrap_or(8);
        if !(8..=255).contains(&major) {
            return Err("Invalid Java server requirement".into());
        }
        Ok((download, major as u32))
    })?;
    let jar = store::ensure(
        app,
        &format!("servers/jars/{}/server.jar", config.version),
        &download.url,
        download.sha1.as_deref(),
        None,
        download.size,
    )?;
    store::materialize(
        &jar,
        &util::safe_join(&directory(app, id)?, "server.jar")?,
        true,
    )?;
    config.java_major = major;
    config.sha1 = download.sha1.ok_or("Missing server digest")?;
    config.jar_size = download.size.ok_or("Missing server size")?;
    config.installed = true;
    save(app, &config)?;
    Ok(config.json())
}

pub fn start(app: &App, id: &str) -> Result<String> {
    if !cfg!(target_os = "linux") {
        return Err("Managed start/stop currently requires Linux; server installation and status ping are cross-platform".into());
    }
    let _lock = util::Lock::acquire(&app.root, &format!("server-{id}"))?;
    let config = read(app, id)?;
    if !config.installed {
        return Err("Server is not installed; install it before starting".into());
    }
    let dir = directory(app, id)?;
    if !eula_accepted(&util::read_limited(
        &util::safe_join(&dir, "eula.txt")?,
        64 * 1024,
    )?)? {
        return Err("Read the Minecraft EULA and explicitly set eula=true in this server's eula.txt before starting".into());
    }
    ensure_stopped(app, id)?;
    if !path::valid_sha1(&config.sha1) {
        return Err("Server has no trusted installation digest; reinstall it".into());
    }
    let (digest, _, size) = store::digests(&util::safe_join(&dir, "server.jar")?)?;
    if digest != config.sha1 || size != config.jar_size {
        return Err("Server JAR is damaged or changed; reinstall before starting".into());
    }
    let java = runtime::select_java(app, &config.java, config.java_major)?;
    let options = util::object(&[
        ("java", quote(&java)),
        ("memory_mib", config.memory_mib.to_string()),
    ]);
    process(app, id, "start", Some(&options))
}
pub fn stop(app: &App, id: &str) -> Result<String> {
    let _lock = util::Lock::acquire(&app.root, &format!("server-{id}"))?;
    let _ = read(app, id)?;
    process(app, id, "stop", None)
}
pub fn status(app: &App, id: &str) -> Result<String> {
    let config = read(app, id)?;
    let state = process(app, id, "status", None)?;
    Ok(util::object(&[
        ("server", config.json()),
        ("process", state),
    ]))
}
pub fn list(app: &App) -> Result<String> {
    let root = util::safe_join(&app.root, "servers")?;
    if !root.exists() {
        return Ok("[]".into());
    }
    let mut ids = Vec::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())?.take(1024) {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let id = entry.file_name().to_string_lossy().into_owned();
        if util::valid_id(&id).is_ok() {
            ids.push(id);
        }
    }
    ids.sort();
    let results = ids
        .iter()
        .map(|id| status(app, id))
        .collect::<Result<Vec<_>>>()?;
    Ok(format!("[{}]", results.join(",")))
}

fn encode_varint(mut value: u32, out: &mut Vec<u8>) {
    loop {
        let mut byte = (value & 0x7f) as u8;
        value >>= 7;
        if value != 0 {
            byte |= 0x80;
        }
        out.push(byte);
        if value == 0 {
            break;
        }
    }
}
fn decode_varint(input: &[u8], position: &mut usize) -> Result<u32> {
    let mut result = 0u32;
    for index in 0..5 {
        let byte = *input.get(*position).ok_or("Truncated VarInt")?;
        *position += 1;
        if index == 4 && (byte & 0xf0) != 0 {
            return Err("VarInt exceeds 32 bits".into());
        }
        result |= u32::from(byte & 0x7f) << (7 * index);
        if byte & 0x80 == 0 {
            if index > 0 && result < (1u32 << (7 * index)) {
                return Err("Noncanonical VarInt".into());
            }
            return Ok(result);
        }
    }
    Err("VarInt exceeds five bytes".into())
}
fn remaining(deadline: Instant) -> Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|d| !d.is_zero())
        .ok_or_else(|| "Minecraft status query timed out".into())
}
fn read_byte(stream: &mut TcpStream, deadline: Instant) -> Result<u8> {
    stream
        .set_read_timeout(Some(remaining(deadline)?))
        .map_err(|e| e.to_string())?;
    let mut byte = [0];
    stream
        .read_exact(&mut byte)
        .map_err(|e| format!("Minecraft status read failed: {e}"))?;
    Ok(byte[0])
}
fn read_frame(stream: &mut TcpStream, deadline: Instant) -> Result<Vec<u8>> {
    let mut prefix = Vec::with_capacity(5);
    for _ in 0..5 {
        let byte = read_byte(stream, deadline)?;
        prefix.push(byte);
        if byte & 0x80 == 0 {
            break;
        }
    }
    let size = decode_varint(&prefix, &mut 0)? as usize;
    if size == 0 || size > MAX_PACKET {
        return Err("Minecraft status packet exceeds its length limit".into());
    }
    let mut data = vec![0; size];
    let mut done = 0;
    while done < size {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(|e| e.to_string())?;
        let n = stream
            .read(&mut data[done..])
            .map_err(|e| format!("Minecraft status read failed: {e}"))?;
        if n == 0 {
            return Err("Truncated Minecraft status packet".into());
        }
        done += n;
    }
    Ok(data)
}
fn write_frame(stream: &mut TcpStream, payload: &[u8], deadline: Instant) -> Result<()> {
    if payload.len() > MAX_PACKET {
        return Err("Minecraft request exceeds packet limit".into());
    }
    let mut bytes = Vec::with_capacity(payload.len() + 5);
    encode_varint(payload.len() as u32, &mut bytes);
    bytes.extend_from_slice(payload);
    let mut written = 0;
    while written < bytes.len() {
        stream
            .set_write_timeout(Some(remaining(deadline)?))
            .map_err(|e| e.to_string())?;
        let n = stream
            .write(&bytes[written..])
            .map_err(|e| format!("Minecraft status write failed: {e}"))?;
        if n == 0 {
            return Err("Minecraft status connection closed while writing".into());
        }
        written += n;
    }
    Ok(())
}
fn query(mut stream: TcpStream, host: &str, port: u16, deadline: Instant) -> Result<String> {
    stream.set_nodelay(true).map_err(|e| e.to_string())?;
    let mut handshake = vec![0];
    encode_varint(u32::MAX, &mut handshake);
    encode_varint(host.len() as u32, &mut handshake);
    handshake.extend_from_slice(host.as_bytes());
    handshake.extend_from_slice(&port.to_be_bytes());
    handshake.push(1);
    write_frame(&mut stream, &handshake, deadline)?;
    write_frame(&mut stream, &[0], deadline)?;
    let frame = read_frame(&mut stream, deadline)?;
    let mut position = 0;
    if decode_varint(&frame, &mut position)? != 0 {
        return Err("Unexpected Minecraft status packet ID".into());
    }
    let length = decode_varint(&frame, &mut position)? as usize;
    if length > MAX_PACKET || frame.len().checked_sub(position) != Some(length) {
        return Err("Minecraft status JSON length mismatch".into());
    }
    let value = with_json(&frame[position..], |v| {
        if v.kind() != json::Kind::Object {
            return Err("Minecraft status must be a JSON object".into());
        }
        Ok(json::raw(v))
    })?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let mut payload = vec![1];
    payload.extend_from_slice(&nonce.to_be_bytes());
    let start = Instant::now();
    write_frame(&mut stream, &payload, deadline)?;
    let pong = read_frame(&mut stream, deadline)?;
    if pong.len() != 9 || pong[0] != 1 || pong[1..] != nonce.to_be_bytes() {
        return Err("Invalid Minecraft status pong".into());
    }
    Ok(util::object(&[
        ("host", quote(host)),
        ("port", port.to_string()),
        ("latency_ms", start.elapsed().as_millis().to_string()),
        ("status", value),
    ]))
}
/// Java edition Server List Ping, with a total deadline and bounded packet allocations.
pub fn ping(host: &str, port: u16) -> Result<String> {
    let host = host
        .strip_prefix('[')
        .and_then(|s| s.strip_suffix(']'))
        .unwrap_or(host);
    if host.is_empty()
        || host.len() > 255
        || port == 0
        || !host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-_:".contains(&b))
    {
        return Err("Provide a hostname or IP address and a port from 1 to 65535".into());
    }
    let deadline = Instant::now() + Duration::from_secs(8);
    let addresses = if let Ok(ip) = host.parse::<IpAddr>() {
        vec![SocketAddr::new(ip, port)]
    } else {
        let (send, receive) = mpsc::sync_channel(1);
        let owned = host.to_owned();
        thread::spawn(move || {
            let result = (owned.as_str(), port)
                .to_socket_addrs()
                .map(|v| v.take(8).collect::<Vec<_>>())
                .map_err(|e| e.to_string());
            let _ = send.send(result);
        });
        receive
            .recv_timeout(Duration::from_secs(3))
            .map_err(|_| "Minecraft server DNS lookup timed out")??
    };
    let mut error = "No address was found for the server".to_owned();
    for address in addresses {
        let timeout = remaining(deadline)?.min(Duration::from_secs(2));
        match TcpStream::connect_timeout(&address, timeout) {
            Ok(stream) => return query(stream, host, port, deadline),
            Err(e) => error = format!("Cannot connect to Minecraft server: {e}"),
        }
    }
    Err(error)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    #[test]
    fn varint_boundaries_and_malformed_encodings() {
        for value in [0, 1, 127, 128, 255, 16384, u32::MAX] {
            let mut bytes = Vec::new();
            encode_varint(value, &mut bytes);
            let mut offset = 0;
            assert_eq!(decode_varint(&bytes, &mut offset).unwrap(), value);
            assert_eq!(offset, bytes.len());
        }
        for input in [
            &[0x80][..],
            &[0x80, 0],
            &[255, 255, 255, 255, 255],
            &[128, 128, 128, 128, 128, 0],
        ] {
            assert!(decode_varint(input, &mut 0).is_err());
        }
    }
    #[test]
    fn explicit_eula_is_required() {
        for value in [
            "",
            "# eula=true\neula=false\n",
            "eula=true # automatic\n",
            "eula=true\neula=false",
        ] {
            assert!(!eula_accepted(value.as_bytes()).unwrap());
        }
        assert!(eula_accepted(b"# Read the agreement\neula=true\n").unwrap());
    }
    #[test]
    fn local_fake_server_exercises_real_wire_protocol() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let deadline = Instant::now() + Duration::from_secs(5);
            let handshake = read_frame(&mut stream, deadline).unwrap();
            let mut offset = 0;
            assert_eq!(decode_varint(&handshake, &mut offset).unwrap(), 0);
            assert_eq!(decode_varint(&handshake, &mut offset).unwrap(), u32::MAX);
            assert_eq!(read_frame(&mut stream, deadline).unwrap(), vec![0]);
            let json=br#"{"version":{"name":"test","protocol":767},"players":{"max":10,"online":0},"description":{"text":"local fixture"}}"#;
            let mut packet = vec![0];
            encode_varint(json.len() as u32, &mut packet);
            packet.extend_from_slice(json);
            write_frame(&mut stream, &packet, deadline).unwrap();
            let pong = read_frame(&mut stream, deadline).unwrap();
            assert_eq!(pong.len(), 9);
            assert_eq!(pong[0], 1);
            write_frame(&mut stream, &pong, deadline).unwrap();
        });
        let result = ping("127.0.0.1", port).unwrap();
        with_json(result.as_bytes(), |v| {
            assert_eq!(
                v.get("status")
                    .and_then(|x| x.get("players"))
                    .and_then(|x| x.get("online"))
                    .and_then(|x| x.as_u64()),
                Some(0)
            );
            Ok(())
        })
        .unwrap();
        task.join().unwrap();
    }
    #[test]
    fn rejects_oversized_server_packet_before_allocating_it() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let deadline = Instant::now() + Duration::from_secs(5);
            read_frame(&mut stream, deadline).unwrap();
            read_frame(&mut stream, deadline).unwrap();
            let mut prefix = Vec::new();
            encode_varint(MAX_PACKET as u32 + 1, &mut prefix);
            stream.write_all(&prefix).unwrap();
        });
        assert!(
            ping("127.0.0.1", port)
                .unwrap_err()
                .contains("length limit")
        );
        task.join().unwrap();
    }
}
