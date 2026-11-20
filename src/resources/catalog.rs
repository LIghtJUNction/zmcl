//! Read only a bounded ZIP directory. Pack contents stay compressed; no ZIP
//! extraction or new compression dependency is needed for installing archives.
use super::*;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

fn u16_at(bytes: &[u8], index: usize) -> u16 {
    u16::from_le_bytes([bytes[index], bytes[index + 1]])
}
fn u32_at(bytes: &[u8], index: usize) -> u32 {
    u32::from_le_bytes([
        bytes[index],
        bytes[index + 1],
        bytes[index + 2],
        bytes[index + 3],
    ])
}
pub(super) fn validate(path: &Path, kind: Kind) -> Result<()> {
    let mut source = File::open(path).map_err(|e| e.to_string())?;
    let size = source.metadata().map_err(|e| e.to_string())?.len();
    if !(22..=MAX_FILE).contains(&size) {
        return Err("Pack ZIP exceeds its size budget or is truncated".into());
    }
    let length = size.min(65557) as usize;
    source
        .seek(SeekFrom::End(-(length as i64)))
        .map_err(|e| e.to_string())?;
    let mut tail = vec![0u8; length];
    source.read_exact(&mut tail).map_err(|e| e.to_string())?;
    let position = (0..=length - 22)
        .rev()
        .find(|index| {
            tail[*index..*index + 4] == *b"PK\x05\x06"
                && *index + 22 + usize::from(u16_at(&tail, *index + 20)) == length
        })
        .ok_or("Pack ZIP end record is invalid")?;
    let end = &tail[position..];
    let count = u16_at(end, 10);
    let directory_size = u64::from(u32_at(end, 12));
    let offset = u64::from(u32_at(end, 16));
    let end_offset = size - length as u64 + position as u64;
    if u16_at(end, 4) != 0 || u16_at(end, 6) != 0 || u16_at(end, 8) != count {
        return Err("Multi-volume pack ZIPs are not supported".into());
    }
    if count == u16::MAX || directory_size == u64::from(u32::MAX) || offset == u64::from(u32::MAX) {
        return Err("Pack ZIP64 directories exceed this installer's archive budget".into());
    }
    if count > 50000
        || directory_size > 16 * 1024 * 1024
        || directory_size < u64::from(count) * 46
        || offset
            .checked_add(directory_size)
            .is_none_or(|end| end > end_offset)
    {
        return Err("Pack ZIP central directory exceeds its structural budget".into());
    }
    source
        .seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let directory_end = offset + directory_size;
    let mut names = BTreeSet::new();
    let mut total = 0u64;
    let mut resource_root = false;
    let mut shader_root = false;
    for _ in 0..count {
        let position = source.stream_position().map_err(|e| e.to_string())?;
        if position + 46 > directory_end {
            return Err("Truncated pack ZIP directory".into());
        }
        let mut header = [0u8; 46];
        source.read_exact(&mut header).map_err(|e| e.to_string())?;
        if header[..4] != *b"PK\x01\x02" {
            return Err("Invalid pack ZIP directory entry".into());
        }
        let flags = u16_at(&header, 8);
        let method = u16_at(&header, 10);
        let compressed = u64::from(u32_at(&header, 20));
        let uncompressed = u64::from(u32_at(&header, 24));
        let name_length = usize::from(u16_at(&header, 28));
        let extra_length = u64::from(u16_at(&header, 30));
        let comment_length = u64::from(u16_at(&header, 32));
        let mode = u32_at(&header, 38) >> 16;
        let local = u64::from(u32_at(&header, 42));
        if flags & 1 != 0 || !matches!(method, 0 | 8) || u16_at(&header, 34) != 0 {
            return Err("Encrypted or unsupported pack ZIP entry".into());
        }
        if !matches!(mode & 0o170000, 0 | 0o100000 | 0o040000) {
            return Err("Pack ZIP contains a symbolic link or special file".into());
        }
        if name_length == 0
            || name_length > 4096
            || position + 46 + name_length as u64 + extra_length + comment_length > directory_end
            || local + 30 > offset
            || compressed > size
            || uncompressed > MAX_FILE
        {
            return Err("Pack ZIP entry exceeds its bounds".into());
        }
        total = total
            .checked_add(uncompressed)
            .ok_or("Pack ZIP size overflow")?;
        if total > 4 * 1024 * 1024 * 1024 {
            return Err("Pack ZIP expands beyond 4 GiB".into());
        }
        let mut name = vec![0u8; name_length];
        source.read_exact(&mut name).map_err(|e| e.to_string())?;
        let name = String::from_utf8(name).map_err(|_| "Pack ZIP filename is not UTF-8")?;
        let path = name.strip_suffix('/').unwrap_or(&name);
        zmcl_core::path::validate_relative(path)?;
        if !names.insert(name.clone()) {
            return Err("Pack ZIP contains duplicate entry names".into());
        }
        if name == "pack.mcmeta" && (1..=1024 * 1024).contains(&uncompressed) {
            resource_root = true;
        }
        if name.starts_with("shaders/") && !name.ends_with('/') && uncompressed > 0 {
            shader_root = true;
        }
        source
            .seek(SeekFrom::Current((extra_length + comment_length) as i64))
            .map_err(|e| e.to_string())?;
    }
    if source.stream_position().map_err(|e| e.to_string())? != directory_end {
        return Err("Pack ZIP directory length is inconsistent".into());
    }
    match kind {
        Kind::ResourcePack if !resource_root => Err("Resource ZIP has no root pack.mcmeta".into()),
        Kind::Shader if !shader_root => Err("Shader ZIP has no root shaders/ contents".into()),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn fixture(names: &[&str]) -> Vec<u8> {
        let mut output = Vec::new();
        let mut directory = Vec::new();
        for name in names {
            let offset = output.len() as u32;
            let mut local = [0u8; 30];
            local[..4].copy_from_slice(b"PK\x03\x04");
            local[4..6].copy_from_slice(&20u16.to_le_bytes());
            local[18..22].copy_from_slice(&2u32.to_le_bytes());
            local[22..26].copy_from_slice(&2u32.to_le_bytes());
            local[26..28].copy_from_slice(&(name.len() as u16).to_le_bytes());
            output.extend_from_slice(&local);
            output.extend_from_slice(name.as_bytes());
            output.extend_from_slice(b"{}");
            let mut central = [0u8; 46];
            central[..4].copy_from_slice(b"PK\x01\x02");
            central[20..24].copy_from_slice(&2u32.to_le_bytes());
            central[24..28].copy_from_slice(&2u32.to_le_bytes());
            central[28..30].copy_from_slice(&(name.len() as u16).to_le_bytes());
            central[42..46].copy_from_slice(&offset.to_le_bytes());
            directory.extend_from_slice(&central);
            directory.extend_from_slice(name.as_bytes());
        }
        let offset = output.len() as u32;
        output.extend_from_slice(&directory);
        let mut end = [0u8; 22];
        end[..4].copy_from_slice(b"PK\x05\x06");
        end[8..10].copy_from_slice(&(names.len() as u16).to_le_bytes());
        end[10..12].copy_from_slice(&(names.len() as u16).to_le_bytes());
        end[12..16].copy_from_slice(&(directory.len() as u32).to_le_bytes());
        end[16..20].copy_from_slice(&offset.to_le_bytes());
        output.extend_from_slice(&end);
        output
    }
    fn check(bytes: &[u8], kind: Kind) -> Result<()> {
        let path = std::env::temp_dir().join(format!(
            "zmcl-pack-catalog-{}-{}.zip",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::write(&path, bytes).unwrap();
        let result = validate(&path, kind);
        fs::remove_file(path).unwrap();
        result
    }
    #[test]
    fn distinguishes_root_resource_metadata_and_shader_contents() {
        assert!(
            check(
                &fixture(&["pack.mcmeta", "assets/minecraft/a.txt"]),
                Kind::ResourcePack
            )
            .is_ok()
        );
        assert!(check(&fixture(&["shaders/composite.fsh"]), Kind::Shader).is_ok());
        assert!(check(&fixture(&["wrapper/pack.mcmeta"]), Kind::ResourcePack).is_err());
        assert!(check(&fixture(&["pack.mcmeta"]), Kind::Shader).is_err());
    }
    #[test]
    fn rejects_paths_duplicates_and_unbounded_directories() {
        assert!(check(&fixture(&["pack.mcmeta", "../outside"]), Kind::ResourcePack).is_err());
        assert!(
            check(
                &fixture(&["pack.mcmeta", "pack.mcmeta"]),
                Kind::ResourcePack
            )
            .is_err()
        );
        let mut huge = fixture(&["pack.mcmeta"]);
        let offset = huge.len() - 22 + 12;
        huge[offset..offset + 4].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(check(&huge, Kind::ResourcePack).is_err());
    }
}
