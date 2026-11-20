import contextlib,errno,importlib.util,json,os,pathlib,signal,subprocess,sys,tempfile,unittest
from unittest import mock
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

    def failed_start(self, failure, ignore_term=False, signal_error=False, cleanup_timeout=False):
        (self.root/'eula.txt').write_text('eula=true\n')
        (self.root/'server.jar').write_bytes(b'synthetic fixture, never executed')
        original_popen=subprocess.Popen
        child=None
        launched={}
        try:
            with contextlib.ExitStack() as stack:
                def launch_synthetic(argv, **options):
                    nonlocal child
                    launched['argv']=argv
                    script='import time;time.sleep(30)'
                    if ignore_term:
                        script=('import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                                "print('ready',flush=True);time.sleep(30)")
                        options['stdout']=subprocess.PIPE
                    child=original_popen([sys.executable,'-c',script],**options)
                    if ignore_term:
                        self.assertEqual(child.stdout.readline(),b'ready\n')
                        original_wait=child.wait
                        stack.enter_context(mock.patch.object(
                            child,'wait',side_effect=lambda timeout=None: original_wait(
                                timeout=0.2 if timeout==15 else timeout)))
                    if cleanup_timeout:
                        for action in ['terminate','kill']:
                            stack.enter_context(mock.patch.object(child,action,
                                side_effect=PermissionError('simulated owned signal failure')))
                        def timeout_wait(timeout=None):
                            raise subprocess.TimeoutExpired('synthetic failed-start child',timeout)
                        stack.enter_context(mock.patch.object(child,'wait',side_effect=timeout_wait))
                    return child

                stack.enter_context(mock.patch.object(module,'supported',return_value=True))
                stack.enter_context(mock.patch('shutil.which',return_value='/synthetic/java'))
                stack.enter_context(mock.patch.object(subprocess,'Popen',side_effect=launch_synthetic))
                if signal_error or cleanup_timeout:
                    stack.enter_context(mock.patch.object(module.signal,'pidfd_send_signal',
                        side_effect=OSError(errno.EIO,'simulated pidfd signal failure')))
                if failure=='pidfd':
                    startup_error=OSError(errno.ENOSYS,'simulated pidfd failure')
                    stack.enter_context(mock.patch.object(module.os,'pidfd_open',
                        side_effect=startup_error))
                elif failure=='identity':
                    startup_error=RuntimeError('simulated identity failure')
                    stack.enter_context(mock.patch.object(module,'snapshot',
                        side_effect=startup_error))
                else:
                    def identity(pid):
                        return dict(pid=pid,cwd=str(self.root),executable='/synthetic/java',
                                    argv=launched['argv'],group=pid,session=pid)
                    stack.enter_context(mock.patch.object(module,'snapshot',side_effect=identity))
                    startup_error=PermissionError('simulated record failure')
                    stack.enter_context(mock.patch.object(module,'atomic_record',
                        side_effect=startup_error))
                with self.assertRaisesRegex((OSError,RuntimeError),'simulated '+failure+' failure') as raised:
                    module.start(self.root,{'java':'java','memory_mib':128})
            self.assertIsNotNone(child)
            if cleanup_timeout:
                self.assertIsNone(child.poll())
                self.assertIn('cleanup failed',str(raised.exception))
                self.assertIn('timed out after 5 seconds',str(raised.exception))
                self.assertIs(raised.exception.__cause__,startup_error)
            else:
                self.assertIs(raised.exception,startup_error)
                self.assertIsNotNone(child.poll(),'Failed launch left an owned child running')
            self.assertFalse((self.root/'process.json').exists())
            if ignore_term:
                self.assertEqual(child.returncode,-signal.SIGKILL)
        finally:
            if child is not None:
                if child.poll() is None:child.kill()
                child.wait(timeout=5)
                if child.stdout is not None:child.stdout.close()

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_pidfd_acquisition_failure_reaps_owned_child(self):
        self.failed_start('pidfd')

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_identity_failure_reaps_owned_child(self):
        self.failed_start('identity')

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_record_failure_reaps_owned_child(self):
        self.failed_start('record')

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_failed_start_kills_owned_child_that_ignores_graceful_stop(self):
        for failure in ['pidfd','identity']:
            with self.subTest(failure=failure):self.failed_start(failure,ignore_term=True)

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_failed_start_uses_owned_child_when_pidfd_signals_fail(self):
        for ignore_term in [False,True]:
            with self.subTest(ignore_term=ignore_term):
                self.failed_start('identity',ignore_term=ignore_term,signal_error=True)

    @unittest.skipUnless(sys.platform.startswith('linux') and module.supported(),'Linux pidfd tests')
    def test_failed_start_reports_original_error_and_unconfirmed_cleanup(self):
        self.failed_start('identity',cleanup_timeout=True)
if __name__=='__main__':unittest.main()
