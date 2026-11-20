"""Explicit online CLI acceptance test using two small public Modrinth packs.

Usage: python3 src/resources/test_cli.py target/release/zmcl
Creates a fresh .test-data/resources-e2e-* home. Never uses the user's launcher
home and never starts Minecraft, accepts EULA, or installs a shader loader.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import io


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    repository = Path(__file__).resolve().parents[2]
    home = repository / ".test-data" / ("resources-e2e-" + str(time.time_ns()))
    home.mkdir(parents=True)
    steps = []

    def run(*command, succeeds=True):
        result = subprocess.run([str(binary), "--root", str(home), *command],
                                capture_output=True, text=True, timeout=180)
        if succeeds and result.returncode:
            raise AssertionError(str(command) + ": " + result.stderr[-2000:])
        if not succeeds and result.returncode == 0:
            raise AssertionError("Unexpected success: " + str(command))
        value = json.loads(result.stdout) if result.returncode == 0 else None
        steps.append({"command": list(command), "exit_code": result.returncode,
                      "changed": value.get("changed") if isinstance(value, dict) else None,
                      "error": result.stderr[-1024:] if result.returncode else None})
        return value

    def get(url, maximum=4 * 1024 * 1024):
        request = urllib.request.Request(url, headers={"User-Agent": "LIghtJUNction/zmcl-cli-acceptance"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(maximum + 1)
        assert len(body) <= maximum
        return body

    def digest(path):
        return hashlib.sha1(path.read_bytes()).hexdigest()

    run("instance", "create", "proof", "--version", "1.21.1")
    instance = home / "instances" / "proof"
    game = instance / "game"
    (game / "resourcepacks").mkdir()
    (game / "shaderpacks").mkdir()
    manual = game / "resourcepacks" / "manual.zip"
    manual.write_bytes(b"manually installed sentinel")
    (game / "shaderpacks" / "manual-notes.txt").write_bytes(b"manual shader notes")
    options = game / "options.txt"
    original_options = b"resourcePacks:[]\nlang:en_us\n"
    options.write_bytes(original_options)

    for kind, query in [("resourcepack", "pumpkin"), ("shader", "potato")]:
        found = run("resources", "search", kind, query, "--version", "1.21.1")
        assert found["hits"] and all(hit["project_type"] == kind for hit in found["hits"])
        assert all("1.21.1" in hit["versions"] for hit in found["hits"])

    added = run("resources", "add", "proof", "resourcepack", "no-pumpkin-blur")
    assert added["changed"] == 1 and added["options_changed"] is False
    resource = next(e for e in added["entries"] if e["kind"] == "resourcepack")
    resource_path = game / resource["actual_path"]
    assert digest(resource_path) == resource["sha1"] and resource_path.stat().st_size == resource["size"]
    cached = home / "shared" / "content" / "sha1" / resource["sha1"]
    if os.name == "posix":
        assert resource_path.stat().st_ino != cached.stat().st_ino

    added_shader = run("resources", "add", "proof", "shader", "terders-potato-shader")
    shader = next(e for e in added_shader["entries"] if e["kind"] == "shader")
    assert added_shader["shader_loader_installed_by_this_command"] is False
    assert digest(game / shader["actual_path"]) == shader["sha1"]
    assert not (game / "mods").exists()
    assert not (game / "config" / "iris.properties").exists()
    assert run("resources", "add", "proof", "resourcepack", "no-pumpkin-blur")["changed"] == 0

    listed = run("resources", "list", "proof")
    assert len(listed["entries"]) == 2 and len(listed["unmanaged"]) == 2
    run("resources", "disable", "proof", "resourcepack", "no-pumpkin-blur")
    disabled = Path(str(resource_path) + ".disabled")
    assert disabled.is_file() and not resource_path.exists()
    assert digest(disabled) == resource["sha1"]
    run("resources", "enable", "proof", "resourcepack", "no-pumpkin-blur")
    assert resource_path.is_file() and not disabled.exists()
    assert run("resources", "update", "proof")["changed"] == 0

    # If the provider has an older compatible revision, seed a previous-version
    # lock fixture from its real, hash-verified archive, then exercise CLI update.
    versions_url = "https://api.modrinth.com/v2/project/" + resource["project_id"] + "/version?"
    versions_url += urllib.parse.urlencode({"game_versions": '["1.21.1"]', "include_changelog": "false"})
    versions = json.loads(get(versions_url))
    older = [v for v in versions
             if v["id"] != resource["version_id"] and "1.21.1" in v["game_versions"]
             and "minecraft" in v["loaders"] and v.get("status", "listed") == "listed"
             and v["date_published"] < resource["published"]]
    upgrade = {"available_older_compatible_revision": bool(older)}
    if older:
        selected = max(older, key=lambda v: v["date_published"])
        package = next((f for f in selected["files"] if f["primary"]), selected["files"][0])
        assert package["size"] < 1024 * 1024
        content = get(package["url"], package["size"])
        assert len(content) == package["size"] and hashlib.sha1(content).hexdigest() == package["hashes"]["sha1"]
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.testzip() is None and "pack.mcmeta" in archive.namelist()
        fixture = dict(resource)
        fixture.update(version_id=selected["id"], version_number=selected["version_number"],
                       published=selected["date_published"], filename=package["filename"],
                       sha1=package["hashes"]["sha1"], size=package["size"], url=package["url"],
                       path="resourcepacks/" + package["filename"],
                       actual_path="resourcepacks/" + package["filename"],
                       loaders=selected["loaders"],
                       requirements=[{"project_id": dep.get("project_id"), "version_id": dep.get("version_id")}
                                     for dep in selected["dependencies"] if dep["dependency_type"] == "required"])
        lock = json.loads((instance / "resources-lock.json").read_text())
        lock["entries"] = [fixture if e["project_id"] == resource["project_id"] else e for e in lock["entries"]]
        resource_path.unlink()
        (game / fixture["actual_path"]).write_bytes(content)
        (instance / "resources-lock.json").write_text(json.dumps(lock))
        updated = run("resources", "update", "proof", "resourcepack", "no-pumpkin-blur")
        assert updated["changed"] == 1
        assert digest(resource_path) == resource["sha1"]
        old_files = list((instance / "resources-history" / updated["history"]).glob("old-*.zip"))
        assert any(digest(path) == fixture["sha1"] for path in old_files)
        upgrade.update(from_version=fixture["version_id"], to_version=resource["version_id"],
                       retained_previous_bytes=True)

    removed = run("resources", "remove", "proof", "resourcepack", "no-pumpkin-blur")
    assert removed["changed"] == 1 and not resource_path.exists()
    history = run("resources", "history", "proof")
    assert any(record["id"] == removed["history"] for record in history)
    run("resources", "restore", "proof", removed["history"])
    assert digest(resource_path) == resource["sha1"]
    assert run("resources", "recover", "proof") == {"recovered": False}

    original = resource_path.read_bytes()
    resource_path.write_bytes(b"user edited the managed archive")
    lock_before = (instance / "resources-lock.json").read_bytes()
    run("resources", "remove", "proof", "resourcepack", "no-pumpkin-blur", succeeds=False)
    assert resource_path.read_bytes() == b"user edited the managed archive"
    assert (instance / "resources-lock.json").read_bytes() == lock_before
    assert digest(cached) == resource["sha1"]
    resource_path.write_bytes(original)  # Restore only our synthetic test fixture.

    run("instance", "create", "collision", "--version", "1.21.1")
    collision = home / "instances" / "collision" / "game" / "resourcepacks"
    collision.mkdir()
    (collision / resource["filename"]).write_bytes(b"keep this manual file")
    run("resources", "add", "collision", "resourcepack", "no-pumpkin-blur", succeeds=False)
    assert (collision / resource["filename"]).read_bytes() == b"keep this manual file"

    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(90)"], cwd=game)
    try:
        (instance / "last-launch.json").write_text(json.dumps({"pid": child.pid, "started": int(time.time())}))
        run("resources", "disable", "proof", "resourcepack", "no-pumpkin-blur", succeeds=False)
        assert resource_path.is_file() and not disabled.exists()
    finally:
        child.terminate()
        child.wait(timeout=10)
        (instance / "last-launch.json").unlink()

    assert manual.read_bytes() == b"manually installed sentinel"
    assert options.read_bytes() == original_options
    assert not (instance / ".resources-journal.json").exists()
    final_list = run("resources", "list", "proof")
    assert len(final_list["entries"]) == 2
    report = {"home": str(home), "binary": str(binary), "passed": True,
              "resource": resource, "shader": shader, "upgrade": upgrade,
              "manual_files_unchanged": True, "options_unchanged": True,
              "shader_loader_installed": False, "steps": steps}
    destination = home / "acceptance.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": True, "commands": len(steps), "home": str(home),
                      "report": str(destination), "upgrade": upgrade}, ensure_ascii=False))


if __name__ == "__main__":
    main()
