import importlib.util, io, os, pathlib, stat, tarfile, tempfile, unittest, zipfile
spec=importlib.util.spec_from_file_location('java_extract',pathlib.Path(__file__).with_name('extract.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class JavaArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='zmcl-java-extract-')
        self.base=pathlib.Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def tar(self,entries):
        target=self.base/'runtime.tar.gz'
        with tarfile.open(target,'w:gz') as archive:
            for name,kind,value in entries:
                info=tarfile.TarInfo(name);info.mode=0o755
                if kind=='file':
                    data=value.encode();info.size=len(data);archive.addfile(info,io.BytesIO(data))
                else:
                    info.type={'sym':tarfile.SYMTYPE,'hard':tarfile.LNKTYPE,'dir':tarfile.DIRTYPE}[kind]
                    if kind!='dir':info.linkname=value
                    archive.addfile(info)
        return target
    def test_valid_tar_and_internal_links(self):
        path=self.tar([('jdk/bin/java','file','binary'),('jdk/lib/java','sym','../bin/java'),('jdk/bin/alias','hard','jdk/bin/java')])
        out=self.base/'out';module.extract(path,out)
        self.assertEqual((out/'jdk/lib/java').read_text(),'binary')
        self.assertEqual((out/'jdk/bin/alias').read_text(),'binary')
        self.assertTrue((out/'jdk/bin/java').stat().st_mode&stat.S_IXUSR)
    def test_external_link_and_traversal_rejected_before_writes(self):
        cases=[
            [('jdk/bin/java','file','binary'),('jdk/lib/escape','sym','../../../outside')],
            [('../outside','file','bad')],
            [('/absolute','file','bad')],
            [('jdk/CON.dll','file','bad')],
            [('jdk/bin','sym','../real'),('jdk/bin/java','file','bad')],
            [('jdk/a','sym','b'),('jdk/b','sym','a')],
        ]
        for index,entries in enumerate(cases):
            with self.subTest(entries=entries):
                out=self.base/f'out-{index}'
                with self.assertRaises(ValueError):module.extract(self.tar(entries),out)
                self.assertFalse(out.exists())
    def test_duplicate_zip_names_rejected(self):
        path=self.base/'runtime.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('jdk/bin/java.exe','safe');z.writestr('JDK/bin/JAVA.exe','bad')
        out=self.base/'out'
        with self.assertRaises(ValueError):module.extract(path,out)
        self.assertFalse(out.exists())
    def test_valid_zip(self):
        path=self.base/'runtime.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('jdk/bin/java.exe','binary')
        out=self.base/'out';result=module.extract(path,out)
        self.assertEqual(result['unpacked_bytes'],6)
        self.assertEqual((out/'jdk/bin/java.exe').read_text(),'binary')
    def test_existing_destination_is_never_removed(self):
        out=self.base/'out';out.mkdir();(out/'keep').write_text('keep')
        with self.assertRaises(FileExistsError):module.extract(self.tar([('jdk/bin/java','file','binary')]),out)
        self.assertEqual((out/'keep').read_text(),'keep')
    def test_extraction_budget_is_enforced(self):
        path=self.tar([('jdk/bin/java','file','binary')]);out=self.base/'out'
        previous=module.MAX_FILE;module.MAX_FILE=2
        try:
            with self.assertRaises(ValueError):module.extract(path,out)
        finally:module.MAX_FILE=previous
        self.assertFalse(out.exists())
if __name__=='__main__':unittest.main()
