"""Adversarial archive regressions. Run: python3 src/content/test_archive.py"""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
spec = importlib.util.spec_from_file_location('zmcl_archive', Path(__file__).with_name('archive.py'))
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / 'fixture.mrpack'
        self.output = self.root / 'stage'
        self.output.mkdir()
        self.index = {'formatVersion': 1, 'game': 'minecraft', 'name': 'Fixture',
                      'versionId': 'test', 'dependencies': {'minecraft': '1.20.1'}, 'files': []}
    def tearDown(self):
        self.temp.cleanup()
    def write(self, entries, index=None, compression=zipfile.ZIP_STORED):
        with zipfile.ZipFile(self.archive, 'w', compression=compression) as output:
            output.writestr('modrinth.index.json', json.dumps(self.index) if index is None else index)
            for name, body in entries:
                output.writestr(name, body)
    def test_layers_apply_client_after_common_and_ignore_server(self):
        self.write([('overrides/config/example.toml', b'common'),
                    ('client-overrides/config/example.toml', b'client'),
                    ('server-overrides/config/example.toml', b'server')])
        codec.unpack(self.archive, self.output)
        self.assertEqual((self.output / 'game/config/example.toml').read_bytes(), b'client')
    def test_traversal_windows_paths_and_reserved_names_fail_before_extract(self):
        for name in ('overrides/../../escape', '/tmp/escape', 'C:/escape',
                     'overrides/a\\b', 'overrides/config/CON.txt', 'overrides/a//b'):
            with self.subTest(path=name):
                self.write([(name, b'evil')])
                with self.assertRaises(ValueError):
                    codec.unpack(self.archive, self.output)
                self.assertEqual(list(self.output.iterdir()), [])
                self.assertFalse((self.root / 'escape').exists())
    def test_symlink_and_special_files_rejected(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            info = zipfile.ZipInfo('overrides/config/link')
            info.create_system = 3
            info.external_attr = mode << 16
            self.write([(info, b'../../escape')])
            with self.assertRaises(ValueError):
                codec.unpack(self.archive, self.output)
    def test_case_and_unicode_duplicate_paths_rejected(self):
        for names in [('overrides/config/a.cfg', 'overrides/CONFIG/A.cfg'),
                      ('overrides/config/caf\u00e9.cfg', 'overrides/config/cafe\u0301.cfg')]:
            self.write([(name, b'content') for name in names])
            with self.assertRaises(ValueError):
                codec.unpack(self.archive, self.output)
    def test_cross_layer_spelling_conflicts_rejected(self):
        self.write([('overrides/config/a.cfg', b'common'), ('client-overrides/CONFIG/A.cfg', b'client')])
        with self.assertRaises(ValueError):
            codec.unpack(self.archive, self.output)
    def test_duplicate_json_and_zip_bomb_rejected(self):
        self.write([], index='{"files":[],"files":[]}')
        with self.assertRaises(ValueError):
            codec.unpack(self.archive, self.output)
        self.write([('overrides/config/bomb.cfg', b'0' * (2 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED)
        with self.assertRaises(ValueError):
            codec.unpack(self.archive, self.output)
    def test_export_has_sha512_and_excludes_credentials_logs_and_saves(self):
        import hashlib
        game = self.root / 'game'
        for directory in ('mods', 'config', 'logs', 'saves'):
            (game / directory).mkdir(parents=True)
        body = b'fixture-mod-bytes'
        (game / 'mods/fixture.jar').write_bytes(body)
        (game / 'config/safe.toml').write_text('show_ui = true')
        (game / 'config/provider.json').write_text('{"api_key":"private"}')
        (game / 'logs/latest.log').write_text('private log')
        (game / 'saves/world.dat').write_bytes(b'private world')
        self.index['files'] = [{'path': 'mods/fixture.jar', 'hashes': {'sha1': hashlib.sha1(body).hexdigest()},
                               'downloads': ['https://cdn.modrinth.com/fixture.jar'], 'fileSize': len(body)}]
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps(self.index))
        target = self.root / 'result.mrpack'
        codec.export(game, plan, target)
        with zipfile.ZipFile(target) as result:
            self.assertEqual(set(result.namelist()), {'modrinth.index.json', 'overrides/config/safe.toml'})
            index = json.loads(result.read('modrinth.index.json'))
            self.assertEqual(index['files'][0]['hashes']['sha512'], hashlib.sha512(body).hexdigest())
    def test_export_rejects_tampered_managed_file(self):
        game = self.root / 'game'
        (game / 'mods').mkdir(parents=True)
        (game / 'mods/fixture.jar').write_bytes(b'tampered')
        self.index['files'] = [{'path': 'mods/fixture.jar', 'hashes': {'sha1': '0' * 40},
                               'downloads': ['https://cdn.modrinth.com/fixture.jar'], 'fileSize': 8}]
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps(self.index))
        with self.assertRaises(ValueError):
            codec.export(game, plan, self.root / 'result.mrpack')
        self.assertFalse((self.root / 'result.mrpack').exists())

    def test_central_directory_budget_is_checked_before_allocating_index(self):
        import struct
        self.write([])
        data = bytearray(self.archive.read_bytes())
        offset = data.rfind(b'PK\x05\x06')
        struct.pack_into('<I', data, offset + 12, 32 * 1024 * 1024)
        self.archive.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'memory budget'):
            codec.unpack(self.archive, self.output)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
