#!/usr/bin/env python3
"""Verify repair only in an explicitly selected disposable zmcl acceptance root.
Run after stopping every Java process that uses this private root:
  python3 scripts/test_cache_repair.py --root .test-data/acceptance \
    --binary target/release/zmcl --evidence .test-data/cache-repair-NEW-RUN
This deliberately replaces two directory entries with corrupt test bytes.
Original bytes stay in an ignored .test-data evidence directory and are restored on failure.
Use a new evidence directory for each run. The JSON omits complete command logs.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,os,signal,stat,subprocess,time,urllib.parse

def private_java(root):
    live=[]
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():continue
        try:
            exe=(p/"exe").resolve()
            if exe.name not in ("java","java.exe"):continue
            cmd=(p/"cmdline").read_bytes()
            cwd=(p/"cwd").resolve()
            if os.fsencode(root) in cmd or cwd.is_relative_to(root):live.append(int(p.name))
        except (OSError,ValueError):continue
    return live

def snapshot(path):
    info=path.stat()
    return {"sha1":hashlib.sha1(path.read_bytes()).hexdigest(),"size":info.st_size,
        "mode":oct(stat.S_IMODE(info.st_mode)),"readonly":not bool(info.st_mode&0o222),
        "device":info.st_dev,"inode":info.st_ino,"nlink":info.st_nlink}

def invoke(binary,root,args,timeout=240):
    start=time.monotonic()
    child=subprocess.Popen([str(binary),"--root",str(root),*args],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        stdout,stderr=child.communicate(timeout=timeout)
    except BaseException:
        os.killpg(child.pid,signal.SIGTERM)
        try:child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL);child.communicate()
        raise
    return {"argv":["zmcl","--root","<PRIVATE_TEST_ROOT>",*args],"exit":child.returncode,
        "duration_s":round(time.monotonic()-start,3),
        "stdout":stdout.replace(str(root),"<PRIVATE_TEST_ROOT>"),
        "stderr":stderr.replace(str(root),"<PRIVATE_TEST_ROOT>")}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--binary",required=True,type=Path)
    ap.add_argument("--evidence",required=True,type=Path)
    args=ap.parse_args()
    if os.name!="posix" or not Path("/proc").is_dir():
        raise SystemExit("This validation script requires Linux /proc.")
    os.umask(0o077)
    os.nice(10)
    root=args.root.resolve(strict=True);binary=args.binary.resolve(strict=True)
    evidence=args.evidence.resolve();evidence.mkdir(parents=True,exist_ok=True)
    if root.name!="acceptance" or root.parent.name!=".test-data":
        raise SystemExit("Refusing a root outside the explicitly named .test-data/acceptance fixture.")
    if evidence.is_relative_to(root):raise SystemExit("Evidence must be outside the tested root.")
    if not evidence.is_relative_to(root.parent):raise SystemExit("Evidence and rollback bytes must stay inside .test-data.")
    if private_java(root):raise SystemExit("A Java process still uses the private test root.")
    instance="vanilla-1_21_1"
    metadata=json.loads((root/"shared/versions/1.21.1/1.21.1.json").read_text())
    library=next(x for x in metadata["libraries"] if x["name"]=="com.mojang:blocklist:1.0.10")
    artifact=library["downloads"]["artifact"];relative=Path(artifact["path"])
    origin=urllib.parse.urlsplit(artifact["url"])
    assert origin.scheme=="https" and origin.netloc=="libraries.minecraft.net"
    assert not relative.is_absolute() and ".." not in relative.parts
    target=root/"shared/libraries"/relative
    cas=root/"shared/objects/sha1"/artifact["sha1"]
    assert not target.is_symlink() and not cas.is_symlink()
    assert target.resolve().is_relative_to(root) and cas.resolve().is_relative_to(root)
    before={"target":snapshot(target),"cas":snapshot(cas)}
    for info in before.values():
        assert info["sha1"]==artifact["sha1"] and info["size"]==artifact["size"]
        assert info["readonly"]
    assert (before["target"]["device"],before["target"]["inode"])==(before["cas"]["device"],before["cas"]["inode"])
    original=target.read_bytes();backup=evidence/"original-library.bin"
    with backup.open("xb") as stream:stream.write(original);stream.flush();os.fsync(stream.fileno())
    os.chmod(backup,0o400)
    report={"scope":"one private acceptance data set; includes all instances, providers, caches and retained recovery fixtures",
        "recorded_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "binary_sha256_before_commands":hashlib.sha256(binary.read_bytes()).hexdigest(),
        "minecraft":"1.21.1","instance":instance,"library":library["name"],
        "source_url":artifact["url"],"expected_sha1":artifact["sha1"],"expected_bytes":artifact["size"],
        "shared_relative_path":"libraries/"+artifact["path"],"before":before,
        "replacement_method":"unlink each pathname, then create separate same-length files with different byte flips; never modify original shared inode in place"}
    output=evidence/"cache-repair-result.json"
    def save():
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    changed=False;repaired=False
    try:
        if private_java(root):raise RuntimeError("A private Java process started before corruption.")
        payloads=((target,bytes([original[0]^255])+original[1:]),(cas,original[:-1]+bytes([original[-1]^255])))
        for path,payload in payloads:
            path.unlink();changed=True
            with path.open("xb") as stream:stream.write(payload)
        report["corrupt"]={"target":snapshot(target),"cas":snapshot(cas)}
        assert all(info["sha1"]!=artifact["sha1"] and info["size"]==artifact["size"] for info in report["corrupt"].values())
        assert report["corrupt"]["target"]["inode"]!=report["corrupt"]["cas"]["inode"]
        save()
        result=invoke(binary,root,["install",instance,"--jobs","2"])
        report["repair_command"]={k:result[k] for k in ("argv","exit","duration_s")};save()
        assert result["exit"]==0,result
        assert "Downloading 1 verified files" in result["stderr"],result
        report["repair_command"]["downloaded_verified_files"]=1
        after={"target":snapshot(target),"cas":snapshot(cas)}
        report["after"]=after
        for info in after.values():
            assert info["sha1"]==artifact["sha1"] and info["size"]==artifact["size"]
            assert info["readonly"]
        assert (after["target"]["device"],after["target"]["inode"])==(after["cas"]["device"],after["cas"]["inode"])
        assert target.read_bytes()==original and cas.read_bytes()==original
        report["repair_verified"]=True;repaired=True;save()
        result=invoke(binary,root,["storage","stats"])
        assert result["exit"]==0,result
        report["storage_command"]={k:result[k] for k in ("argv","exit","duration_s")}
        report["storage_stats"]=json.loads(result["stdout"])
        assert report["storage_stats"]["physical_deduplication_measured"] is True
        report["allocation_definition"]="sum(st_blocks * 512) once per distinct (st_dev, st_ino); includes filesystem block rounding, excludes directory entries"
        report["comparison_limit"]="This is the measured acceptance data set, not a general savings ratio or a cold-install benchmark."
        save()
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    except BaseException as error:
        report["error_type"]=type(error).__name__
        if changed and not repaired:
            tmp=cas.parent/(".zmcl-fixture-restore-"+str(os.getpid()))
            with tmp.open("xb") as stream:stream.write(original);stream.flush();os.fsync(stream.fileno())
            os.chmod(tmp,int(before["cas"]["mode"],8));os.replace(tmp,cas)
            link=target.parent/(".zmcl-fixture-restore-"+str(os.getpid()))
            os.link(cas,link);os.replace(link,target)
            report["restored_original_on_failure"]=target.read_bytes()==original and cas.read_bytes()==original
        save()
        raise

if __name__=="__main__":main()
