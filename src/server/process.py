"""Linux server lifecycle using verified process identities and pidfd signaling."""
import json, os, pathlib, signal, sys

MAX_RECORD = 65536
def bounded_json(path):
    with path.open('rb') as file: data=file.read(MAX_RECORD+1)
    if len(data)>MAX_RECORD: raise ValueError('Server process record exceeds limit')
    return json.loads(data)

def atomic_record(path, value):
    import tempfile
    if path.is_symlink(): raise ValueError('Symbolic link process record rejected')
    fd,tmp=tempfile.mkstemp(prefix='.process-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,separators=(',',':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
    except BaseException:
        try:os.unlink(tmp)
        except FileNotFoundError:pass
        raise

def supported():
    return sys.platform.startswith('linux') and hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal')

def snapshot(pid):
    process=pathlib.Path('/proc')/str(pid)
    fields=(process/'stat').read_text().rsplit(')',1)[1].split()
    if fields[0]=='Z': raise ProcessLookupError('Process has exited')
    with (process/'cmdline').open('rb') as command_line: args=command_line.read(MAX_RECORD+1)
    if len(args)>MAX_RECORD: raise ValueError('Server command line exceeds limit')
    return {'pid':pid,'start_ticks':int(fields[19]),'group':int(fields[2]),'session':int(fields[3]),
        'uid':process.stat().st_uid,'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'executable':os.path.realpath(os.readlink(process/'exe')),'cwd':os.path.realpath(os.readlink(process/'cwd')),
        'argv':[os.fsdecode(arg) for arg in args.rstrip(b'\0').split(b'\0')]}

def record_for(root):
    path=root/'process.json'
    if not path.exists(): return None
    if path.is_symlink(): raise ValueError('Symbolic link process record rejected')
    record=bounded_json(path)
    if not isinstance(record,dict) or record.get('schema')!=1: raise ValueError('Invalid process record')
    pid=record.get('pid')
    if not isinstance(pid,int) or pid<=1: raise ValueError('Invalid server process ID')
    argv=record.get('argv',[])
    if not isinstance(argv,list) or any(not isinstance(v,str) for v in argv):raise ValueError('Invalid server argv record')
    owner=record.get('owner','')
    if len(owner)!=32 or any(c not in '0123456789abcdef' for c in owner):raise ValueError('Invalid process owner marker')
    if record.get('cwd')!=str(root) or record.get('group')!=pid or record.get('session')!=pid:
        raise ValueError('Server record does not identify an isolated process in this server directory')
    if pathlib.Path(record.get('executable','')).name not in ('java','java.exe'):
        raise ValueError('Server process record is not Java')
    expected_jar=str(root/'server.jar')
    if not any(argv[i:i+2]==['-jar',expected_jar] for i in range(len(argv)-1)):
        raise ValueError('Server process record has a different JAR')
    if '-Dzmcl.server.owner='+owner not in argv:raise ValueError('Server owner marker is missing')
    return record

def same(record,current):
    return all(record.get(key)==current.get(key) for key in
        ('pid','start_ticks','group','session','uid','boot_id','executable','cwd','argv'))

def status(root):
    record=record_for(root)
    if record is None:return {'state':'stopped','running':False,'verified':False,'stop_supported':supported()}
    result={'pid':record['pid'],'log':record.get('log',''),'started':record.get('started',0),'stop_supported':supported()}
    if not sys.platform.startswith('linux'):
        return dict(result,state='unsupported',running=None,verified=False)
    try:
        current=snapshot(record['pid'])
    except (FileNotFoundError,ProcessLookupError):
        return dict(result,state='stopped',running=False,verified=False)
    except PermissionError:
        return dict(result,state='unknown',running=None,verified=False,reason='Process identity is not readable')
    if record.get('boot_id')!=current['boot_id']:
        return dict(result,state='stopped',running=False,verified=False,reason='Recorded process belongs to a previous boot')
    if not same(record,current):
        return dict(result,state='identity_mismatch',running=None,verified=False,reason='PID no longer matches this server')
    return dict(result,state='running',running=True,verified=True)

def eula_accepted(root):
    path=root/'eula.txt'
    if path.is_symlink():raise ValueError('Symbolic link EULA file rejected')
    with path.open('r') as stream: data=stream.read(65537)
    if len(data)>65536:raise ValueError('EULA file exceeds limit')
    answer=False
    for line in data.splitlines():
        line=line.strip()
        if not line or line.startswith(('#','!')):continue
        key,sep,value=line.partition('=')
        if sep and key.strip()=='eula':answer=value.strip()=='true'
    return answer

def start(root, options):
    if not supported():raise RuntimeError('Managed start/stop currently requires Linux pidfd support; install and status ping are cross-platform')
    state=status(root)
    if state['state']!='stopped':raise RuntimeError('Server has an active or uncertain process record; inspect status before starting')
    if not eula_accepted(root):raise RuntimeError('Read the Minecraft EULA and explicitly set eula=true before starting; zmcl will not accept it for you')
    # Status and rejected starts do not need process-launch or archive modules.
    import shutil
    import subprocess
    import time
    import uuid
    java=shutil.which(options.get('java',''))
    if not java:raise RuntimeError('Java executable was not found')
    java=os.path.realpath(java)
    if pathlib.Path(java).name not in ('java','java.exe'):raise ValueError('Managed server requires the Java executable')
    memory=options.get('memory_mib',2048)
    if not isinstance(memory,int) or not 128<=memory<=1048576:raise ValueError('Invalid server memory budget')
    jar=root/'server.jar'
    if jar.is_symlink() or not jar.is_file():raise ValueError('Server JAR is missing or is a symlink')
    logs=root/'logs'
    if logs.is_symlink():raise ValueError('Symbolic link logs directory rejected')
    logs.mkdir(exist_ok=True)
    owner=uuid.uuid4().hex
    logfile=logs/('server-'+str(int(time.time()))+'-'+owner[:12]+'.log')
    fd=os.open(logfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    argv=[java,'-Xmx'+str(memory)+'M','-Dfile.encoding=UTF-8','-Dzmcl.server.owner='+owner,'-jar',str(jar),'nogui']
    child=None; pidfd=None
    try:
        with os.fdopen(fd,'wb') as output:
            child=subprocess.Popen(argv,cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                start_new_session=True,close_fds=True)
        pidfd=os.pidfd_open(child.pid)
        identity=snapshot(child.pid)
        if identity['cwd']!=str(root) or identity['executable']!=java or identity['argv']!=argv or identity['group']!=child.pid or identity['session']!=child.pid:
            raise RuntimeError('New server process identity did not match the launch')
        record=dict(identity,schema=1,owner=owner,log=str(logfile),started=int(time.time()))
        atomic_record(root/'process.json',record)
        # Observe immediate errors without keeping a resident launcher process.
        time.sleep(0.15)
        if child.poll() is not None:raise RuntimeError('Server exited during startup; inspect '+str(logfile))
        return status(root)
    except BaseException:
        if child is not None and child.poll() is None and pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd,signal.SIGTERM)
                child.wait(timeout=15)
            except (ProcessLookupError,subprocess.TimeoutExpired):pass
        raise
    finally:
        if pidfd is not None:os.close(pidfd)

def stop(root):
    if not supported():raise RuntimeError('Verified managed stopping requires Linux pidfd support')
    import select
    record=record_for(root)
    if record is None:return {'state':'stopped','running':False,'verified':False,'stop_supported':True}
    try:fd=os.pidfd_open(record['pid'])
    except ProcessLookupError:return status(root)
    try:
        try:current=snapshot(record['pid'])
        except (FileNotFoundError,ProcessLookupError):return status(root)
        if current['uid']!=os.getuid() or not same(record,current):
            raise RuntimeError('Refusing to signal an unverified process or a reused PID')
        # The pidfd refers to this exact process even if its numeric PID is reused.
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        ready,_,_=select.select([fd],[],[],20)
        if ready:return dict(status(root),state='stopped',running=False,stop_requested=True)
        return {'state':'stopping','running':True,'verified':True,'stop_requested':True,'pid':record['pid'],
            'reason':'Graceful shutdown is still in progress; no forced kill was sent','stop_supported':True}
    finally:os.close(fd)

def main():
    action,directory=sys.argv[1:3]
    original=pathlib.Path(directory)
    if original.is_symlink():raise ValueError('Symbolic link server directory rejected')
    root=original.resolve(strict=True)
    if not root.is_dir():raise ValueError('Server directory is missing')
    if action=='status':return status(root)
    if action=='stop':return stop(root)
    if action=='start':
        data=sys.stdin.buffer.read(MAX_RECORD+1)
        if len(data)>MAX_RECORD:raise ValueError('Launch configuration exceeds limit')
        return start(root,json.loads(data))
    raise ValueError('Unknown process action')

if __name__=='__main__':
    try:print(json.dumps(main(),separators=(',',':')))
    except Exception as error:
        print(json.dumps({'error':str(error)},separators=(',',':')))
        sys.exit(1)
