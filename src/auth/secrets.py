import ctypes,os,shutil,subprocess,sys
action,account=sys.argv[1:3]
service='org.project-z.zmcl'
data=sys.stdin.buffer.read(65537)
if len(data)>65536:raise ValueError('Credential too large')
if sys.platform.startswith('linux'):
    if not shutil.which('secret-tool'):raise RuntimeError('Secret Service unavailable')
    if action=='check':sys.exit(0)
    args={'put':['store','--label=zmcl Minecraft account','service',service,'account',account],
          'get':['lookup','service',service,'account',account],
          'delete':['clear','service',service,'account',account]}[action]
    r=subprocess.run(['secret-tool',*args],input=data if action=='put' else None,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60)
    if r.returncode:raise RuntimeError('Credential store operation failed')
    if action=='get':sys.stdout.buffer.write(r.stdout)
elif sys.platform=='darwin':
    sec=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    service=service.encode();account=account.encode()
    find=sec.SecKeychainFindGenericPassword
    find.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p)]
    find.restype=ctypes.c_int32
    sec.SecKeychainItemDelete.argtypes=[ctypes.c_void_p]
    sec.SecKeychainItemFreeContent.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
    sec.SecKeychainAddGenericPassword.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p]
    sec.SecKeychainItemModifyAttributesAndData.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
    cf.CFRelease.argtypes=[ctypes.c_void_p]
    if action=='check':sys.exit(0)
    size=ctypes.c_uint32();pointer=ctypes.c_void_p();item=ctypes.c_void_p()
    status=find(None,len(service),service,len(account),account,ctypes.byref(size),ctypes.byref(pointer),ctypes.byref(item))
    try:
        if action=='get':
            if status:raise RuntimeError('No credential')
            if size.value>65536:raise ValueError('Credential too large')
            sys.stdout.buffer.write(ctypes.string_at(pointer,size.value))
        elif action=='delete':
            if status==0 and sec.SecKeychainItemDelete(item):raise RuntimeError('Delete failed')
        elif action=='put':
            if status==0:r=sec.SecKeychainItemModifyAttributesAndData(item,None,len(data),data)
            else:r=sec.SecKeychainAddGenericPassword(None,len(service),service,len(account),account,len(data),data,None)
            if r:raise RuntimeError('Store failed')
    finally:
        if pointer:sec.SecKeychainItemFreeContent(None,pointer)
        if item:cf.CFRelease(item)
elif os.name=='nt':
    from ctypes import wintypes as w
    class CREDENTIAL(ctypes.Structure):
        _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),('Comment',w.LPWSTR),('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),('CredentialBlob',ctypes.POINTER(w.BYTE)),('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]
    lib=ctypes.WinDLL('Advapi32.dll',use_last_error=True)
    lib.CredWriteW.argtypes=[ctypes.POINTER(CREDENTIAL),w.DWORD]
    lib.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    lib.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD]
    lib.CredFree.argtypes=[ctypes.c_void_p]
    if action=='check':sys.exit(0)
    name=service+':'+account
    if action=='put':
        buf=(w.BYTE*len(data)).from_buffer_copy(data)
        c=CREDENTIAL();c.Type=1;c.TargetName=name;c.CredentialBlobSize=len(data);c.CredentialBlob=buf;c.Persist=2;c.UserName=account
        if not lib.CredWriteW(ctypes.byref(c),0):raise ctypes.WinError(ctypes.get_last_error())
    elif action=='get':
        p=ctypes.POINTER(CREDENTIAL)()
        if not lib.CredReadW(name,1,0,ctypes.byref(p)):raise ctypes.WinError(ctypes.get_last_error())
        try:
            if p.contents.CredentialBlobSize>65536:raise ValueError('Credential too large')
            sys.stdout.buffer.write(ctypes.string_at(p.contents.CredentialBlob,p.contents.CredentialBlobSize))
        finally:lib.CredFree(p)
    elif action=='delete':
        if not lib.CredDeleteW(name,1,0) and ctypes.get_last_error()!=1168:raise ctypes.WinError(ctypes.get_last_error())
else:raise RuntimeError('Unsupported credential store')
