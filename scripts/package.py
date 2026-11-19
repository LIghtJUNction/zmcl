#!/usr/bin/env python3
"""Build a reproducible local release archive without publishing."""
import argparse,hashlib,json,os,pathlib,platform,tarfile,zipfile
p=argparse.ArgumentParser();p.add_argument('--binary',type=pathlib.Path,default=pathlib.Path('target/release/zmcl'+('.exe' if os.name=='nt' else '')));p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('dist'));a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
version=__import__('tomllib').loads((root/'Cargo.toml').read_text())['package']['version']
binary=a.binary.resolve(strict=True)
name='zmcl-'+version+'-'+platform.system().lower()+'-'+platform.machine().lower()
a.output.mkdir(parents=True,exist_ok=True)
files=[(binary,'zmcl'+('.exe' if os.name=='nt' else '')),(root/'README.md','README.md'),(root/'LICENSE','LICENSE'),(root/'docs/FEATURES.md','docs/FEATURES.md'),(root/'assets/zmcl.svg','assets/zmcl.svg'),(root/'scripts/install.py','scripts/install.py')]
if os.name=='nt':
    target=a.output/(name+'.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for src,dest in files:z.write(src,name+'/'+dest)
else:
    target=a.output/(name+'.tar.gz')
    with tarfile.open(target,'w:gz') as tar:
        for src,dest in files:tar.add(src,arcname=name+'/'+dest,recursive=False)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
target.with_name(target.name+'.sha256').write_text(digest+'  '+target.name+'\n')
print(json.dumps({'archive':str(target),'sha256':digest,'bytes':target.stat().st_size,'binary_bytes':binary.stat().st_size}))
