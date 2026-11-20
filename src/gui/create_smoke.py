#!/usr/bin/env python3
"""Exercise instance creation and version selection on a private X11 display.

The cached manifest is a local fixture. The first automatic install deliberately
fails without network access; its saved instance must remain recoverable. An
installed-state fixture exercises the account route without signing in or
launching Minecraft. Use --online-version for real official downloads and a native game launch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--online-version', help='Download, install and launch this real release in explicit offline single-player mode')
    parser.add_argument('--data-root', type=Path, help='Keep a fresh acceptance root and its downloaded cache for later checks')
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for tool in ('Xvfb', 'xdotool', 'import'):
        if not shutil.which(tool):
            parser.error('Missing test utility: ' + tool)
    cases = []
    gui = display = None
    game_pid = None
    with tempfile.TemporaryDirectory(prefix='private-create-', dir=output) as temporary:
        base = Path(temporary)
        data = args.data_root.resolve() if args.data_root else base / 'launcher data'
        if (data / 'instances').is_dir() and any((data / 'instances').iterdir()):
            parser.error('Acceptance requires an empty instance directory')
        (data / 'cache').mkdir(parents=True, exist_ok=True)
        manifest = {'latest': {'release': '1.21.4'}, 'versions': [
            {'id': f'1.21.{n}', 'type': 'release'} for n in range(20, -1, -1)
        ] + [{'id': 'snapshot-fixture', 'type': 'snapshot'}]}
        if not args.online_version:
            (data / 'cache/version-manifest.json').write_text(json.dumps(manifest))
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1', XDG_SESSION_TYPE='x11')
        env.pop('WAYLAND_DISPLAY', None)
        env['XAUTHORITY'] = str(base / 'Xauthority')
        Path(env['XAUTHORITY']).write_bytes(b'')
        read_fd, write_fd = os.pipe()
        try:
            with (output / 'display.log').open('wb') as display_log, (output / 'launcher.log').open('wb') as gui_log:
                display = subprocess.Popen([
                    'Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1280x900x24',
                    '-nolisten', 'tcp', '-noreset', '-auth', env['XAUTHORITY']],
                    pass_fds=(write_fd,), env=env, stdout=subprocess.DEVNULL, stderr=display_log)
                os.close(write_fd)
                write_fd = -1
                assert select.select([read_fd], [], [], 10)[0], 'Private display did not start'
                number = os.read(read_fd, 64).decode().strip()
                assert number.isdigit()
                env['DISPLAY'] = ':' + number

                def xd(*argv):
                    return subprocess.check_output(['xdotool', *map(str, argv)], env=env,
                                                   text=True, timeout=10).strip()

                gui = subprocess.Popen([str(binary), '--root', str(data), 'gui'], env=env,
                                       stdin=subprocess.DEVNULL, stdout=gui_log, stderr=gui_log)
                window = xd('search', '--sync', '--onlyvisible', '--name', '^zmcl').splitlines()[0]
                xd('windowfocus', '--sync', window)

                def idle(timeout=20):
                    quiet = None
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        assert gui.poll() is None, 'GUI exited during acceptance'
                        children = Path(f'/proc/{gui.pid}/task/{gui.pid}/children').read_text().strip()
                        if children:
                            quiet = None
                        elif quiet is None:
                            quiet = time.monotonic()
                        elif time.monotonic() - quiet >= .7:
                            return
                        time.sleep(.1)
                    raise AssertionError('GUI operation did not become idle')

                def geometry():
                    return dict(line.split('=', 1) for line in xd('getwindowgeometry', '--shell', window).splitlines() if '=' in line)

                size = geometry()
                width, height = int(size['WIDTH']), int(size['HEIGHT'])
                assert width == 1120 and height == 760, size
                fx, fy, fw, fh = (width - 600) / 2, (height - 620) / 2, 600, 620

                def click(px, py):
                    xd('mousemove', '--window', window, round(px), round(py))
                    xd('click', 1)
                    time.sleep(.15)

                def field(px, py, value):
                    click(px, py)
                    xd('key', '--window', window, 'ctrl+a')
                    xd('type', '--clearmodifiers', '--window', window, '--delay', 1, value)

                def capture(name):
                    time.sleep(.2)
                    subprocess.run(['import', '-display', env['DISPLAY'], '-window', window,
                                    str(output / name)], env=env, check=True, timeout=10)

                idle()
                capture('01-empty.png')
                click(310, 175)
                idle(timeout=120)
                capture('02-new-defaults.png')
                click(fx + fw - 95, fy + 225)
                field(fx + 180, fy + 106, args.online_version or '1.21.1')
                capture('03-release-picker.png')
                # The exact version is ranked before prefix matches such as 1.21.19.
                click(fx + fw / 2, fy + 168)
                capture('04-release-selected.png')
                click(fx + fw / 2, fy + fh - 46)
                idle(timeout=900 if args.online_version else 20)
                capture('05-install-outcome.png')
                first = data / 'instances/minecraft/instance.json'
                assert first.is_file(), 'Default name was not created'
                instance = json.loads(first.read_text())
                assert instance['version'] == (args.online_version or '1.21.1'), instance
                if args.online_version:
                    assert instance.get('installed') is True, instance
                    cases.append({'case': 'Official release selected and installed through the native creation form', 'version': args.online_version, 'passed': True})
                    click(310, height - 109)
                    capture('06-account-next-step.png')
                    click(95, 150)
                    click(310, height - 169)
                    field(600, height - 169, 'VerifyPlayer')
                    capture('07-offline-launch-ready.png')
                    click(310, height - 109)
                    idle(timeout=120)
                    launch = data / 'instances/minecraft/last-launch.json'
                    assert launch.is_file(), 'GUI launch did not record a game process'
                    game_pid = json.loads(launch.read_text())['pid']
                    assert Path(f'/proc/{game_pid}').is_dir(), 'Game process already exited'
                    deadline = time.monotonic() + 120
                    game_window = None
                    while time.monotonic() < deadline:
                        result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Minecraft'],
                                                env=env, capture_output=True, text=True, timeout=10)
                        if result.returncode == 0 and result.stdout.strip():
                            game_window = result.stdout.splitlines()[0]
                            break
                        assert Path(f'/proc/{game_pid}').is_dir(), 'Game process exited before opening a window'
                        time.sleep(.25)
                    assert game_window, 'Minecraft did not open a native game window'
                    # Allow resource reload and first-run screens to settle, then retain visual evidence.
                    time.sleep(20)
                    subprocess.run(['import', '-display', env['DISPLAY'], '-window', game_window,
                                    str(output / '08-minecraft-window.png')], env=env, check=True, timeout=10)
                    assert Path(f'/proc/{game_pid}').is_dir()
                    cases.append({'case': 'Explicit offline profile launches a live Minecraft native window', 'version': args.online_version, 'passed': True})
                    result = {'scope': 'Real official download, native GUI creation, installation and explicit offline game launch',
                              'binary_sha256': binary_sha256,
                              'cases': cases, 'limits': ['Linux X11 only', 'No Microsoft sign-in', 'Screenshot requires visual inspection']}
                    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result, indent=2))
                    return
                assert instance.get('installed') is False, instance
                # The missing digest/URL forces the normal CLI failure path without downloading.
                capture('05-install-error-retains-instance.png')
                click(1010, height - 36)
                capture('06-install-error-activity.png')
                xd('key', '--window', window, 'Escape')
                cases.append({'case': 'Default name, filtered release selection and failed automatic install retain the saved instance', 'passed': True})

                click(310, 175)
                idle()
                # Create-only is explicitly selected; no installation follows this creation.
                click(fx + 95, fy + fh - 99)
                click(fx + fw / 2, fy + fh - 46)
                idle()
                second = data / 'instances/minecraft-2/instance.json'
                assert second.is_file(), 'Suggested name collided with the first instance'
                assert json.loads(second.read_text()).get('installed') is False
                capture('07-create-only-unique-name.png')
                cases.append({'case': 'Create-only preserves an uninstalled second instance with a unique suggested name', 'passed': True})

                click(310, 175)
                idle()
                field(fx + 180, fy + 142, 'minecraft')
                click(fx + fw / 2, fy + fh - 46)
                idle()
                assert sorted(p.name for p in (data / 'instances').iterdir()) == ['minecraft', 'minecraft-2']
                capture('08-duplicate-name-validation.png')
                xd('key', '--window', window, 'Escape')
                cases.append({'case': 'Duplicate name is explained before submission and preserves both instances', 'passed': True})

                config = json.loads(second.read_text())
                config['installed'] = True
                second.write_text(json.dumps(config))
                click(445, 175)
                idle()
                click(310, height - 109)
                capture('09-account-next-step.png')
                cases.append({'case': 'Installed-state fixture opens the account page from the main action without signing in or launching', 'passed': True})

                click(95, 150)
                xd('windowsize', '--sync', window, 800, 600)
                time.sleep(.3)
                click(270, 175)
                idle()
                capture('10-compact-create.png')
                xd('key', '--window', window, 'Escape')
                click(45, 600 - 38)
                assert gui.wait(timeout=10) == 0
                result = {
                    'scope': 'Real X11 input and CLI file effects with an isolated cached manifest fixture',
                    'binary_sha256': binary_sha256,
                    'cases': cases,
                    'limits': ['No provider downloads', 'No Microsoft sign-in',
                               'Installed state for account routing is synthetic', 'No game launch'],
                }
                (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                print(json.dumps(result, indent=2))
        finally:
            if game_pid is not None:
                try:
                    os.kill(game_pid, 15)
                except ProcessLookupError:
                    pass
            for process in (gui, display):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)


if __name__ == '__main__':
    main()
