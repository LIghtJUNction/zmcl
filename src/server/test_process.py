import importlib.util,json,os,pathlib,subprocess,sys,tempfile,unittest
spec=importlib.util.spec_from_file_location('server_process',pathlib.Path(__file__).with_name('process.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ServerProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='zmcl-server-process-');self.root=pathlib.Path(self.tmp.name).resolve()
    def tearDown(self):self.tmp.cleanup()
    def test_absent_process_is_stopped(self):
        self.assertEqual(module.status(self.root)['state'],'stopped')
    def test_false_eula_blocks_before_java_spawn(self):
        if not module.supported():self.skipTest('pidfd unavailable')
        (self.root/'eula.txt').write_text('eula=false\n')
        with self.assertRaisesRegex(RuntimeError,'explicitly set eula=true'):
            module.start(self.root,{'java':'nonexistent-java-for-test','memory_mib':2048})
        self.assertFalse((self.root/'process.json').exists())
    def test_corrupt_process_record_rejected(self):
        (self.root/'process.json').write_text('{"schema":1,"pid":0}')
        with self.assertRaises(ValueError):module.status(self.root)
    def test_oversized_record_rejected(self):
        (self.root/'process.json').write_bytes(b' '*(module.MAX_RECORD+1))
        with self.assertRaises(ValueError):module.status(self.root)
    def test_reused_or_forged_pid_is_not_signaled(self):
        if not module.supported():self.skipTest('pidfd unavailable')
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            current=module.snapshot(child.pid)
            owner='a'*32
            fake=dict(current,schema=1,cwd=str(self.root),executable='/usr/bin/java',owner=owner,
                argv=['/usr/bin/java','-Dzmcl.server.owner='+owner,'-jar',str(self.root/'server.jar'),'nogui'])
            module.atomic_record(self.root/'process.json',fake)
            self.assertEqual(module.status(self.root)['state'],'identity_mismatch')
            with self.assertRaisesRegex(RuntimeError,'unverified'):
                module.stop(self.root)
            self.assertIsNone(child.poll())
        finally:
            child.terminate();child.wait(timeout=5)
    def test_identity_contains_birth_and_boot(self):
        if not sys.platform.startswith('linux'):self.skipTest('Linux process identity')
        current=module.snapshot(os.getpid())
        self.assertGreater(current['start_ticks'],0)
        self.assertEqual(current['uid'],os.getuid())
        self.assertTrue(current['boot_id'])
if __name__=='__main__':unittest.main()
