use crate::util::{self, Result};
use std::path::Path;

/// The system Python ZIP reader supplies mature decompression; all paths and limits are checked before writes.
pub fn extract_natives(archive: &Path, dest: &Path, excludes: &[String]) -> Result<()> {
    let script = r#"
import json,os,pathlib,stat,sys,zipfile
source,destination,excluded=sys.argv[1:4]
root=pathlib.Path(destination)
excluded=json.loads(excluded)
root.mkdir(parents=True,exist_ok=True)
total=0
seen=set()
with zipfile.ZipFile(source) as z:
    entries=z.infolist()
    if len(entries)>10000: raise ValueError('Too many archive entries')
    for info in entries:
        name=info.filename
        if '\\' in name or ':' in name or name.startswith('/') or any(ord(c)<32 for c in name):
            raise ValueError('Unsafe native archive path')
        path=pathlib.PurePosixPath(name)
        if '..' in path.parts or not path.parts or any(p.endswith((' ','.')) for p in path.parts):
            raise ValueError('Unsafe native archive path')
        if stat.S_ISLNK(info.external_attr>>16): raise ValueError('Archive symlink rejected')
        key='/'.join(path.parts).casefold()
        if key in seen: raise ValueError('Duplicate native archive path')
        seen.add(key)
        total+=info.file_size
        if total>536870912 or info.file_size>134217728: raise ValueError('Native archive exceeds limits')
    for info in entries:
        if info.is_dir() or info.filename.startswith('META-INF/') or any(info.filename.startswith(x) for x in excluded):continue
        path=pathlib.PurePosixPath(info.filename)
        if path.suffix.lower() not in ('.so','.dll','.dylib','.jnilib'):continue
        target=root.joinpath(*path.parts)
        parent=target.parent
        parent.mkdir(parents=True,exist_ok=True)
        for p in [target,*target.parents]:
            if p==root.parent:break
            if p.is_symlink():raise ValueError('Symlink destination rejected')
        with z.open(info) as src,open(target,'wb') as dst:
            count=0
            while True:
                block=src.read(65536)
                if not block:break
                count+=len(block)
                if count>info.file_size:raise ValueError('Native size mismatch')
                dst.write(block)
"#;
    let excluded = format!(
        "[{}]",
        excludes
            .iter()
            .map(|s| zmcl_core::json::quote(s))
            .collect::<Vec<_>>()
            .join(",")
    );
    let output = util::python_command()?
        .args(["-c", script])
        .arg(archive)
        .arg(dest)
        .arg(excluded)
        .output()
        .map_err(|e| format!("Native extraction requires system Python 3: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "Native extraction failed: {}",
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(1500)
                .collect::<String>()
        ));
    }
    Ok(())
}
