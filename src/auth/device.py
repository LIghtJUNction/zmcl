# Xbox ProofOfPossession framing; system OpenSSL owns ECDSA, never custom crypto.
import base64,json,os,struct,subprocess,time,uuid
def run(args,data=None):
    p=subprocess.run(['openssl',*args],input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if p.returncode:raise RuntimeError('OpenSSL failed')
    return p.stdout
def b64url(v):return base64.urlsafe_b64encode(v).rstrip(b'=').decode()
key=run(['ecparam','-name','prime256v1','-genkey','-noout'])
public=run(['pkey','-pubout','-outform','DER'],key)
point=public[-65:]
if len(point)!=65 or point[0]!=4:raise ValueError('Unexpected P-256 public key')
body=json.dumps({'Properties':{'DeviceType':'Win32','Id':'{'+str(uuid.uuid4())+'}','AuthMethod':'ProofOfPossession','ProofKey':{'kty':'EC','alg':'ES256','crv':'P-256','use':'sig','x':b64url(point[1:33]),'y':b64url(point[33:65])}},'RelyingParty':'http://auth.xboxlive.com','TokenType':'JWT'},separators=(',',':'))
timestamp=int((time.time()+11644473600)*10000000)
header=struct.pack('>IQ',1,timestamp)
content=struct.pack('>I',1)+b'\0'+struct.pack('>Q',timestamp)+b'\0POST\0/device/authenticate\0\0'+body.encode()+b'\0'
if os.name=='posix':
    r,w=os.pipe()
    os.write(w,key);os.close(w)
    try:
        p=subprocess.run(['openssl','dgst','-sha256','-sign','/dev/fd/'+str(r)],input=content,pass_fds=(r,),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
    finally:os.close(r)
    if p.returncode:raise RuntimeError('Signing failed')
    signature=p.stdout
else:
    import tempfile,pathlib
    with tempfile.TemporaryDirectory(prefix='zmcl-device-') as d:
        path=pathlib.Path(d)/'key.pem';path.write_bytes(key)
        signature=run(['dgst','-sha256','-sign',str(path)],content)
def der_value(data,offset,tag):
    if offset>=len(data) or data[offset]!=tag:raise ValueError('Invalid DER')
    length=data[offset+1];offset+=2
    if length&128:
        n=length&127
        if n>2:raise ValueError('Invalid DER length')
        length=int.from_bytes(data[offset:offset+n],'big');offset+=n
    end=offset+length
    if end>len(data):raise ValueError('Truncated DER')
    return data[offset:end],end
sequence,end=der_value(signature,0,0x30)
if end!=len(signature):raise ValueError('Trailing DER')
r,index=der_value(sequence,0,2);s,index=der_value(sequence,index,2)
if index!=len(sequence):raise ValueError('Trailing signature')
raw=int.from_bytes(r,'big').to_bytes(32,'big')+int.from_bytes(s,'big').to_bytes(32,'big')
print(json.dumps({'body':body,'signature':base64.b64encode(header+raw).decode()}))
