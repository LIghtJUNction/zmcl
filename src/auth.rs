use crate::{App,net,util::{self,Result}};
use std::{fs,io::Write,path::PathBuf,process::{Command,Stdio},time::{Duration,Instant}};
use zmcl_core::json::{get_str,quote,req_str,with_json};
const LOGIN:&str="https://login.microsoftonline.com/consumers/oauth2/v2.0";
const SCOPE:&str="XboxLive.signin offline_access";
const HELPER:&str=include_str!("auth/secrets.py");
const DEVICE:&str=include_str!("auth/device.py");
pub struct Account{pub uuid:String,pub name:String,pub access_token:String,refresh_token:String,client_id:String,expires_at:u64}
impl Account{
 pub fn public_json(&self)->String{util::object(&[("id",quote(&self.uuid)),("uuid",quote(&self.uuid)),("name",quote(&self.name)),("expires_at",self.expires_at.to_string()),("type",quote("microsoft"))])}
 fn secret_json(&self)->String{util::object(&[("access_token",quote(&self.access_token)),("refresh_token",quote(&self.refresh_token)),("client_id",quote(&self.client_id))])}
}
fn secret(action:&str,id:&str,input:Option<&str>)->Result<String>{
 let mut child=Command::new("python3").args(["-c",HELPER,action,id]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().map_err(|e|format!("System credential helper unavailable: {e}"))?;
 if let Some(data)=input{
  if child.stdin.take().ok_or("Missing credential input")?.write_all(data.as_bytes()).is_err(){let _=child.kill();let _=child.wait();return Err("Cannot write to the system credential store".into());}
 }else{drop(child.stdin.take());}
 let output=child.wait_with_output().map_err(|e|e.to_string())?;
 if !output.status.success(){return Err("System credential store is unavailable or locked. On Linux install secret-tool and unlock a Secret Service keyring. Tokens are never stored in plain-text files.".into());}
 String::from_utf8(output.stdout).map_err(|_|"Invalid credential store response".into())
}
fn path(app:&App,id:&str)->Result<PathBuf>{
 if id.len()!=32||!id.bytes().all(|b|b.is_ascii_hexdigit()){return Err("Invalid account UUID".into());}
 util::safe_join(&app.root,&format!("accounts/{id}.json"))
}
fn save(app:&App,account:&Account)->Result<()>{secret("put",&account.uuid,Some(&account.secret_json()))?;util::atomic_write(&path(app,&account.uuid)?,account.public_json().as_bytes())}
fn read(app:&App,id:&str)->Result<Account>{
 let bytes=util::read_limited(&path(app,id)?,64*1024)?;
 let (uuid,name,expires_at)=with_json(&bytes,|v|Ok((req_str(v,"uuid")?,req_str(v,"name")?,v.get("expires_at").and_then(|x|x.as_u64()).unwrap_or(0))))?;
 if uuid!=id{return Err("Account metadata identity mismatch".into());}
 let tokens=secret("get",id,None)?;
 with_json(tokens.as_bytes(),|v|Ok(Account{uuid,name,expires_at,access_token:req_str(v,"access_token")?,refresh_token:req_str(v,"refresh_token")?,client_id:req_str(v,"client_id")?}))
}
pub fn list(app:&App)->Result<String>{
 let mut out=Vec::new();
 for entry in fs::read_dir(app.root.join("accounts")).map_err(|e|e.to_string())?{
  let entry=entry.map_err(|e|e.to_string())?;
  if entry.path().extension().and_then(|s|s.to_str())!=Some("json"){continue;}
  let bytes=util::read_limited(&entry.path(),64*1024)?;
  out.push(with_json(&bytes,|v|Ok(util::object(&[("id",quote(&req_str(v,"uuid")?)),("uuid",quote(&req_str(v,"uuid")?)),("name",quote(&req_str(v,"name")?)),("type",quote("microsoft"))])))?);
 }
 Ok(format!("{{\"accounts\":[{}]}}",out.join(",")))
}
pub fn selected(app:&App,id:Option<&str>)->Result<Account>{
 let id=if let Some(id)=id{id.to_owned()}else{let bytes=list(app)?;with_json(bytes.as_bytes(),|v|{
  let accounts:Vec<_>=v.get("accounts").ok_or("Missing accounts")?.array().collect();
  if accounts.len()!=1{return Err("Choose an account with --account UUID, sign in, or explicitly use --offline NAME".into());}
  req_str(accounts[0],"uuid")
 })?};
 let account=read(app,&id)?;if account.expires_at<=util::now()+120{refresh(app,&id)}else{Ok(account)}
}
fn auth_error(stage:&str,response:&net::Response)->String{
 let code=with_json(&response.body,|v|Ok(get_str(v,"error").or_else(||get_str(v,"errorCode")).unwrap_or_default())).unwrap_or_default();
 let code=code.chars().filter(|c|c.is_ascii_alphanumeric()||*c=='_').take(80).collect::<String>();
 format!("{stage} failed (HTTP {}, {code}). Check application registration, account eligibility and service availability.",response.status)
}
fn post(url:&str,body:&str,json:bool)->Result<net::Response>{net::request(url,"POST",&[("Content-Type",if json{"application/json"}else{"application/x-www-form-urlencoded"})],Some(body))}
fn exchange(ms_access:&str,refresh_token:String,client_id:String)->Result<Account>{
 let body=format!("{{\"Properties\":{{\"AuthMethod\":\"RPS\",\"SiteName\":\"user.auth.xboxlive.com\",\"RpsTicket\":{}}},\"RelyingParty\":\"http://auth.xboxlive.com\",\"TokenType\":\"JWT\"}}",quote(&format!("d={ms_access}")));
 let response=net::request("https://user.auth.xboxlive.com/user/authenticate","POST",&[("Content-Type","application/json"),("x-xbl-contract-version","1")],Some(&body))?;
 if response.status!=200{return Err(auth_error("Xbox user authentication",&response));}
 let user_token=with_json(&response.body,|v|req_str(v,"Token"))?;
 let prepared=Command::new("python3").args(["-c",DEVICE]).output().map_err(|e|format!("Xbox device signing requires Python 3 and OpenSSL: {e}"))?;
 if !prepared.status.success(){return Err("Cannot create Xbox device proof; ensure system OpenSSL is installed".into());}
 let (body,signature)=with_json(&prepared.stdout,|v|Ok((req_str(v,"body")?,req_str(v,"signature")?)))?;
 let response=net::request("https://device.auth.xboxlive.com/device/authenticate","POST",&[("Content-Type","application/json"),("x-xbl-contract-version","1"),("Signature",&signature)],Some(&body))?;
 if response.status!=200{return Err(auth_error("Xbox device authentication",&response));}
 let device_token=with_json(&response.body,|v|req_str(v,"Token"))?;
 let body=format!("{{\"Properties\":{{\"SandboxId\":\"RETAIL\",\"UserTokens\":[{}],\"DeviceToken\":{}}},\"RelyingParty\":\"rp://api.minecraftservices.com/\",\"TokenType\":\"JWT\"}}",quote(&user_token),quote(&device_token));
 let response=post("https://xsts.auth.xboxlive.com/xsts/authorize",&body,true)?;
 if response.status!=200{return Err(auth_error("Minecraft Xbox authorization",&response));}
 let (token,hash)=with_json(&response.body,|v|{let item=v.get("DisplayClaims").and_then(|d|d.get("xui")).and_then(|x|x.array().next()).ok_or("Missing Xbox claim")?;Ok((req_str(v,"Token")?,req_str(item,"uhs")?))})?;
 let response=post("https://api.minecraftservices.com/authentication/login_with_xbox",&format!("{{\"identityToken\":{}}}",quote(&format!("XBL3.0 x={hash};{token}"))),true)?;
 if response.status!=200{return Err(auth_error("Minecraft authentication",&response));}
 let (access_token,ttl)=with_json(&response.body,|v|Ok((req_str(v,"access_token")?,v.get("expires_in").and_then(|x|x.as_u64()).unwrap_or(0))))?;
 if ttl==0{return Err("Minecraft token has no valid expiration".into());}
 let bearer=format!("Bearer {access_token}");
 let owned=net::request("https://api.minecraftservices.com/entitlements/mcstore","GET",&[("Authorization",&bearer)],None)?;
 if owned.status!=200{return Err(auth_error("Minecraft ownership check",&owned));}
 if !with_json(&owned.body,|v|Ok(v.get("items").is_some_and(|items|items.array().next().is_some())))?{return Err("This account has no Minecraft Java entitlement".into());}
 let profile=net::request("https://api.minecraftservices.com/minecraft/profile","GET",&[("Authorization",&bearer)],None)?;
 if profile.status!=200{return Err(auth_error("Minecraft profile",&profile));}
 with_json(&profile.body,|v|Ok(Account{uuid:req_str(v,"id")?,name:req_str(v,"name")?,access_token,refresh_token,client_id,expires_at:util::now().saturating_add(ttl.min(7*86400))}))
}
pub fn login(app:&App,client_id:Option<&str>)->Result<String>{
 let client=client_id.map(str::to_owned).or_else(||std::env::var("ZMCL_MICROSOFT_CLIENT_ID").ok()).ok_or("Provide --client-id with your registered Microsoft application ID. zmcl does not borrow another launcher's identity.")?;
 if client.len()!=36||!client.bytes().all(|b|b.is_ascii_hexdigit()||b==b'-'){return Err("Invalid Microsoft application ID".into());}
 secret("check","",None)?;let _lock=util::Lock::acquire(&app.root,"accounts")?;
 let form=format!("client_id={}&scope={}",net::encode(&client),net::encode(SCOPE));
 let response=post(&format!("{LOGIN}/devicecode"),&form,false)?;
 if response.status!=200{return Err(auth_error("Microsoft device sign-in",&response));}
 let (device,user,url,ttl,interval)=with_json(&response.body,|v|Ok((req_str(v,"device_code")?,req_str(v,"user_code")?,req_str(v,"verification_uri")?,v.get("expires_in").and_then(|x|x.as_u64()).unwrap_or(900),v.get("interval").and_then(|x|x.as_u64()).unwrap_or(5))))?;
 if !url.starts_with("https://"){return Err("Invalid Microsoft verification URL".into());}
 eprintln!("Open {url}\nEnter code: {user}\nWaiting for your authorization…");
 let deadline=Instant::now()+Duration::from_secs(ttl.min(1800));let mut interval=interval.clamp(1,30);
 while Instant::now()<deadline{
  std::thread::sleep(Duration::from_secs(interval));
  let body=format!("grant_type=urn:ietf:params:oauth:grant-type:device_code&client_id={}&device_code={}",net::encode(&client),net::encode(&device));
  let response=post(&format!("{LOGIN}/token"),&body,false)?;
  if response.status==200{
   let (token,refresh)=with_json(&response.body,|v|Ok((req_str(v,"access_token")?,req_str(v,"refresh_token")?)))?;
   let account=exchange(&token,refresh,client)?;save(app,&account)?;return Ok(account.public_json());
  }
  let error=with_json(&response.body,|v|req_str(v,"error"))?;
  match error.as_str(){"authorization_pending"=>{},"slow_down"=>{interval=(interval+5).min(60);},"authorization_declined"|"access_denied"=>return Err("Microsoft sign-in was declined".into()),"expired_token"=>return Err("Microsoft device code expired".into()),_=>return Err(auth_error("Microsoft sign-in",&response))}
 }
 Err("Microsoft device code expired; start sign-in again".into())
}
pub fn refresh(app:&App,id:&str)->Result<Account>{
 let _lock=util::Lock::acquire(&app.root,"accounts")?;let account=read(app,id)?;
 let body=format!("client_id={}&grant_type=refresh_token&refresh_token={}&scope={}",net::encode(&account.client_id),net::encode(&account.refresh_token),net::encode(SCOPE));
 let response=post(&format!("{LOGIN}/token"),&body,false)?;
 if response.status!=200{return Err(auth_error("Microsoft token refresh",&response));}
 let (token,refresh)=with_json(&response.body,|v|Ok((req_str(v,"access_token")?,get_str(v,"refresh_token").unwrap_or(account.refresh_token))))?;
 let updated=exchange(&token,refresh,account.client_id)?;
 if updated.uuid!=id{return Err("Refreshed account identity mismatch".into());}
 save(app,&updated)?;Ok(updated)
}
pub fn logout(app:&App,id:&str)->Result<()>{let target=path(app,id)?;let _lock=util::Lock::acquire(&app.root,"accounts")?;secret("delete",id,None)?;fs::remove_file(target).map_err(|e|e.to_string())}
