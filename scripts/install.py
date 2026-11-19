#!/usr/bin/env python3
"""Idempotent per-user installation; never modifies Minecraft data."""
import argparse,hashlib,json,os,pathlib,shutil,stat,sys,tempfile
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()
def atomic_bytes(path,data,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.zmcl-install-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(temp,mode);os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['install','uninstall'])
    parser.add_argument('--prefix',type=pathlib.Path,default=pathlib.Path.home()/'.local')
    root=pathlib.Path(__file__).resolve().parents[1]
    default_binary=root/('zmcl.exe' if os.name=='nt' else 'zmcl')
    if not default_binary.is_file():default_binary=root/'target/release'/('zmcl.exe' if os.name=='nt' else 'zmcl')
    parser.add_argument('--binary',type=pathlib.Path,default=default_binary)
    args=parser.parse_args()
    prefix=args.prefix.expanduser().resolve()
    if any(c in str(prefix) for c in '\r\n\0'):raise ValueError('Invalid installation path')
    record=prefix/'share/zmcl/install.json'
    if args.action=='uninstall':
        if not record.exists():print('zmcl is not installed at this prefix');return
        entries=json.loads(record.read_text())
        retained=[]
        for entry in entries:
            rel=pathlib.PurePosixPath(entry['path'])
            if rel.is_absolute() or '..' in rel.parts or str(rel) not in ['bin/zmcl','share/icons/hicolor/scalable/apps/zmcl.svg','share/applications/zmcl.desktop']:raise ValueError('Invalid installation manifest')
            path=prefix/rel
            if path.is_symlink():retained.append(entry['path']);continue
            if path.exists():
                if digest(path)!=entry['sha256']:retained.append(entry['path']);continue
                path.unlink()
        if retained:raise RuntimeError('Modified files preserved: '+', '.join(retained))
        record.unlink()
        print('Removed zmcl installation. Minecraft instances, saves and credentials are preserved.')
        return
    source=args.binary.resolve(strict=True)
    if not source.is_file() or source.is_symlink():raise ValueError('Binary must be a regular file')
    executable=prefix/'bin/zmcl'
    quote=str(executable).replace('\\','\\\\').replace('"','\\"').replace('$','\\$').replace('`','\\`').replace('%','%%')
    desktop='[Desktop Entry]\nType=Application\nName=zmcl\nComment=Minecraft, with less overhead\nExec="'+quote+'" gui\nIcon=zmcl\nTerminal=false\nCategories=Game;\nStartupWMClass=zmcl\n'
    resources=[
        ('bin/zmcl',source.read_bytes(),0o755),
        ('share/icons/hicolor/scalable/apps/zmcl.svg',(pathlib.Path(__file__).resolve().parents[1]/'assets/zmcl.svg').read_bytes(),0o644),
        ('share/applications/zmcl.desktop',desktop.encode(),0o644)
    ]
    entries=[]
    for rel,data,mode in resources:
        path=prefix/rel
        if path.is_symlink():raise ValueError('Refusing a symbolic-link install destination')
        atomic_bytes(path,data,mode)
        entries.append({'path':rel,'sha256':hashlib.sha256(data).hexdigest()})
    atomic_bytes(record,json.dumps(entries,indent=2).encode(),0o600)
    print(executable)
if __name__=='__main__':
    try:main()
    except Exception as e:print('zmcl installer:',str(e),file=sys.stderr);sys.exit(1)
