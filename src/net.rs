use crate::util::{self, Result};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
    process::Stdio,
};

pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}
const MAX_JSON: usize = 16 * 1024 * 1024;

fn config_value(value: &str) -> Result<String> {
    if value.contains('\0') {
        return Err("NUL in HTTP configuration".into());
    }
    Ok(format!(
        "\"{}\"",
        value
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('\n', "\\n")
            .replace('\r', "\\r")
            .replace('\t', "\\t")
    ))
}
fn base(url: &str) -> Result<String> {
    if !url.starts_with("https://") || url.len() > 16384 || url.chars().any(char::is_control) {
        return Err("Only valid HTTPS download URLs are allowed".into());
    }
    let authority = url
        .trim_start_matches("https://")
        .split('/')
        .next()
        .unwrap_or("");
    if authority.is_empty() || authority.contains('@') {
        return Err("Invalid HTTPS authority".into());
    }
    Ok(format!(
        "url = {}\nuser-agent = \"zmcl/{} (https://github.com/LIghtJUNction/zmcl)\"\n",
        config_value(url)?,
        env!("CARGO_PKG_VERSION")
    ))
}
fn curl(config: &str, max_time: u32) -> Result<std::process::Child> {
    let mut child=util::background_command("curl").args(["--disable","--silent","--location","--proto","=https","--proto-redir","=https","--max-redirs","5","--connect-timeout","20","--max-time",&max_time.to_string(),"--config","-"])
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()
        .map_err(|e|format!("Cannot start system curl: {e}. Install curl with your operating system package manager."))?;
    if let Err(e) = child
        .stdin
        .take()
        .ok_or("Missing curl input")?
        .write_all(config.as_bytes())
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e.to_string());
    }
    Ok(child)
}
pub fn request(
    url: &str,
    method: &str,
    headers: &[(&str, &str)],
    body: Option<&str>,
) -> Result<Response> {
    if !matches!(method, "GET" | "POST" | "PUT" | "DELETE") {
        return Err("Unsupported HTTP method".into());
    }
    let mut config = base(url)?;
    config.push_str(&format!(
        "request = {}\nmax-filesize = {}\nwrite-out = \"\\n%{{http_code}}\"\n",
        config_value(method)?,
        MAX_JSON
    ));
    for (name, value) in headers {
        if name.contains(['\n', '\r', ':']) || value.contains(['\n', '\r']) {
            return Err("Invalid HTTP header".into());
        }
        config.push_str(&format!(
            "header = {}\n",
            config_value(&format!("{name}: {value}"))?
        ));
    }
    if let Some(body) = body {
        if body.starts_with('@') {
            return Err("HTTP body must not start with a file reference".into());
        }
        config.push_str(&format!("data = {}\n", config_value(body)?));
    }
    let mut child = curl(&config, 120)?;
    let mut output = Vec::new();
    let read = child
        .stdout
        .take()
        .ok_or("Missing HTTP output")?
        .take(MAX_JSON as u64 + 5)
        .read_to_end(&mut output);
    if read.is_err() || output.len() > MAX_JSON + 4 {
        let _ = child.kill();
        let _ = child.wait();
        return Err("HTTP response exceeded its size limit or could not be read".into());
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!(
            "HTTPS request failed (curl exit {}). Check network, proxy and server availability.",
            status.code().unwrap_or(-1)
        ));
    }
    let position = output
        .iter()
        .rposition(|&b| b == b'\n')
        .ok_or("Missing HTTP status")?;
    let http = std::str::from_utf8(&output[position + 1..])
        .ok()
        .and_then(|s| s.parse().ok())
        .ok_or("Invalid HTTP status")?;
    output.truncate(position);
    Ok(Response {
        status: http,
        body: output,
    })
}
pub fn get(url: &str) -> Result<Vec<u8>> {
    let r = request(url, "GET", &[], None)?;
    if !(200..300).contains(&r.status) {
        return Err(format!("HTTPS server returned status {}", r.status));
    }
    Ok(r.body)
}
pub fn download(url: &str, path: &Path, max_bytes: u64) -> Result<()> {
    if path.exists() {
        return Err("Download destination already exists".into());
    }
    let mut config = base(url)?;
    config.push_str(&format!(
        "fail\nretry = 2\nretry-delay = 1\nmax-filesize = {max_bytes}\noutput = {}\n",
        config_value(&path.to_string_lossy())?
    ));
    let mut child = curl(&config, 900)?;
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        let _ = fs::remove_file(path);
        return Err(format!(
            "Download failed (curl exit {}). Retry to resume verified cached files.",
            status.code().unwrap_or(-1)
        ));
    }
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > max_bytes {
        let _ = fs::remove_file(path);
        return Err("Download exceeds size limit".into());
    }
    Ok(())
}
pub fn encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for b in value.bytes() {
        if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
            out.push(b as char)
        } else {
            use std::fmt::Write;
            let _ = write!(out, "%{b:02X}");
        }
    }
    out
}
pub fn download_batch(transfers: &[(String, std::path::PathBuf, u64)], jobs: usize) -> Result<()> {
    if transfers.is_empty() {
        return Ok(());
    }
    let mut config = String::new();
    for (index, (url, path, limit)) in transfers.iter().enumerate() {
        if index != 0 {
            config.push_str("next\n");
        }
        config.push_str(&base(url)?);
        config.push_str(&format!("silent\nlocation\nproto = \"=https\"\nproto-redir = \"=https\"\nmax-redirs = 5\nconnect-timeout = 20\nmax-time = 300\nfail\nretry = 2\nretry-delay = 1\nmax-filesize = {limit}\noutput = {}\n",config_value(&path.to_string_lossy())?));
    }
    let mut child = util::background_command("curl")
        .args([
            "--disable",
            "--parallel",
            "--parallel-max",
            &jobs.clamp(1, 16).to_string(),
            "--config",
            "-",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    if let Err(e) = child
        .stdin
        .take()
        .ok_or("Missing curl input")?
        .write_all(config.as_bytes())
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e.to_string());
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "Some parallel transfers failed (curl exit {})",
            status.code().unwrap_or(-1)
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_unsafe_transport() {
        for u in [
            "http://example.org",
            "file:///etc/passwd",
            "https://x@y/a",
            "https://x\nheader=x",
        ] {
            assert!(base(u).is_err());
        }
    }
    #[test]
    fn encodes_query_bytes() {
        assert_eq!(encode("a b/中文"), "a%20b%2F%E4%B8%AD%E6%96%87");
    }
}
