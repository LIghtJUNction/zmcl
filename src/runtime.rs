use crate::{App,Instance,auth,archive,net,store,util::{self,Result}};
use std::{collections::{BTreeMap,BTreeSet},fs::{self,File},path::PathBuf,process::{Command,Stdio}};
use zmcl_core::{json::{get_str,quote,req_str,with_json},model::{self,Environment,Version},plan::{self,Artifact,LaunchOptions}};
const MANIFEST:&str="https://piston-meta.mojang.com/mc/game/version_manifest_v2.json";

pub fn environment()->Environment{
    let os=match std::env::consts::OS{"macos"=>"osx",x=>x};
    Environment::new(os,std::env::consts::ARCH)
}
pub fn versions(app:&App)->Result<String>{
    let bytes=manifest(app)?;
    String::from_utf8(bytes).map_err(|e|e.to_string())
}
fn manifest(app:&App)->Result<Vec<u8>>{
    let path=util::safe_join(&app.root,"cache/version-manifest.json")?;
    if fs::metadata(&path).ok().and_then(|m|m.modified().ok()).and_then(|t|t.elapsed().ok()).is_some_and(|age|age.as_secs()<3600){
        return util::read_limited(&path,4*1024*1024);
    }
    let bytes=net::get(MANIFEST)?;
    with_json(&bytes,|v|{if v.get("versions").is_none(){Err("Missing official version list".into())}else{Ok(())}})?;
    util::atomic_write(&path,&bytes)?;
    Ok(bytes)
}
fn version_path(app:&App,id:&str)->Result<PathBuf>{
    util::safe_join(&app.shared(),&format!("versions/{id}/{id}.json"))
}
fn download_version(app:&App,id:&str)->Result<Vec<u8>>{
    let bytes=manifest(app)?;
    let (url,hash)=with_json(&bytes,|v|{
        for item in v.get("versions").ok_or("Missing versions")?.array(){
            if get_str(item,"id").as_deref()==Some(id){return Ok((req_str(item,"url")?,req_str(item,"sha1")?));}
        }
        Err(format!("Minecraft version '{id}' was not found in the official manifest"))
    })?;
    let path=store::ensure(app,&format!("versions/{id}/{id}.json"),&url,Some(&hash),None,None)?;
    util::read_limited(&path,16*1024*1024)
}
fn resolve(app:&App,id:&str,online:bool,seen:&mut BTreeSet<String>)->Result<Version>{
    if seen.len()>=16 || !seen.insert(id.into()){return Err("Version inheritance cycle or depth limit".into());}
    let bytes=if online{download_version(app,id)?}else{util::read_limited(&version_path(app,id)?,16*1024*1024)?};
    let child=Version::parse(&bytes)?;
    if child.id!=id {return Err("Version manifest ID mismatch".into());}
    if let Some(parent)=&child.inherits_from{
        let base=resolve(app,parent,online,seen)?;
        model::merge(base,child)
    }else{Ok(child)}
}
fn loader_profile(app:&App,instance:&mut Instance)->Result<Vec<u8>>{
    let (base,api)=match instance.loader.as_str(){
        "fabric"=>("https://meta.fabricmc.net","v2"),
        "quilt"=>("https://meta.quiltmc.org","v3"),
        _=>return Err("Loader profile endpoint unavailable".into())
    };
    if instance.loader_version.is_empty(){
        let bytes=net::get(&format!("{base}/{api}/versions/loader/{}",net::encode(&instance.version)))?;
        instance.loader_version=with_json(&bytes,|v|{
            let entries:Vec<_>=v.array().collect();
            let first=entries.iter().find(|x|x.get("loader").and_then(|l|l.get("stable")).and_then(|s|s.as_bool())==Some(true)).or(entries.first()).ok_or("No compatible loader versions")?;
            req_str(first.get("loader").ok_or("Missing loader")?,"version")
        })?;
    }
    let url=format!("{base}/{api}/versions/loader/{}/{}/profile/json",net::encode(&instance.version),net::encode(&instance.loader_version));
    let bytes=net::get(&url)?;
    let child=Version::parse(&bytes)?;
    if child.inherits_from.as_deref()!=Some(&instance.version){return Err("Loader does not target the selected Minecraft version".into());}
    util::atomic_write(&util::safe_join(&app.dir(&instance.id)?,"loader.json")?,&bytes)?;
    Ok(bytes)
}
fn maven_checksums(artifacts:&mut [Artifact])->Result<()>{
    for item in artifacts{
        if item.download.sha1.is_some(){continue;}
        let url=format!("{}.sha1",item.download.url);
        let bytes=net::get(&url).map_err(|e|format!("Cannot verify library '{}': {e}",item.path))?;
        let first=std::str::from_utf8(&bytes).map_err(|_|"Invalid Maven checksum")?.split_whitespace().next().unwrap_or("");
        if first.len()!=40||!first.bytes().all(|b|b.is_ascii_hexdigit()){return Err("Invalid Maven SHA-1 sidecar".into());}
        item.download.sha1=Some(first.to_ascii_lowercase());
    }
    Ok(())
}
pub fn install(app:&App,id:&str,jobs:usize)->Result<()>{
    if !(1..=16).contains(&jobs){return Err("Download concurrency must be 1–16".into());}
    let _lock=util::Lock::acquire(&app.root,&format!("instance-{id}"))?;
    let mut instance=app.read_instance(id)?;
    instance.installed=false;app.write_instance(&instance)?;
    eprintln!("Resolving Minecraft {}",instance.version);
    let base=resolve(app,&instance.version,true,&mut BTreeSet::new())?;
    let version=match instance.loader.as_str(){
        "vanilla"=>base,
        "fabric"|"quilt"=>{let child=Version::parse(&loader_profile(app,&mut instance)?)?;model::merge(base,child)?}
        "forge"|"neoforge"=>install_forge(app,&mut instance,base,jobs)?,
        _=>return Err("Unsupported loader".into())
    };
    let mut installation=plan::install_plan(&version,&environment())?;
    maven_checksums(&mut installation.downloads)?;
    store::batch(app,&installation.downloads,jobs)?;
    if let Some(index)=&version.asset_index{
        let path=util::safe_join(&app.shared(),&format!("assets/indexes/{}.json",index.id))?;
        let bytes=util::read_limited(&path,32*1024*1024)?;
        let assets=plan::assets_plan(&bytes)?;
        let mut seen=BTreeSet::new();let mut downloads=Vec::new();
        for asset in &assets.objects{
            if !seen.insert(asset.hash.clone()){continue;}
            downloads.push(Artifact{path:asset.path.clone(),download:model::Download{url:asset.url.clone(),sha1:Some(asset.hash.clone()),size:Some(asset.size),path:None}});
        }
        store::batch(app,&downloads,jobs)?;
        if assets.virtual_assets||assets.map_to_resources{
            for asset in &assets.objects{
                let source=util::safe_join(&app.shared(),&asset.path)?;
                if assets.virtual_assets{
                    let target=util::safe_join(&app.shared(),&format!("assets/virtual/{}/{}",index.id,asset.name))?;
                    store::materialize(&source,&target,true)?;
                }
                if assets.map_to_resources{
                    let target=util::safe_join(&app.dir(id)?.join("game/resources"),&asset.name)?;
                    store::materialize(&source,&target,true)?;
                }
            }
        }
    }
    let natives=util::safe_join(&app.dir(id)?,"natives")?;
    fs::create_dir_all(&natives).map_err(|e|e.to_string())?;
    for archive in &installation.natives{
        archive::extract_natives(&util::safe_join(&app.shared(),&archive.path)?,&natives,&archive.excludes)?;
    }
    instance.installed=true;app.write_instance(&instance)?;
    eprintln!("Instance '{}' is installed and verified",instance.name);
    Ok(())
}
fn install_forge(app:&App,instance:&mut Instance,base:Version,jobs:usize)->Result<Version>{
    let _shared_lock=util::Lock::acquire(&app.root,"loader-installer")?;
    let mut vanilla=plan::install_plan(&base,&environment())?;
    maven_checksums(&mut vanilla.downloads)?;store::batch(app,&vanilla.downloads,jobs)?;
    let (name,url)=if instance.loader=="forge"{
        if instance.loader_version.is_empty(){
            let bytes=net::get("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")?;
            instance.loader_version=with_json(&bytes,|v|{
                req_str(v.get("promos").ok_or("Missing Forge promotions")?,&format!("{}-latest",instance.version))
            })?;
        }
        let version=if instance.loader_version.starts_with(&format!("{}-",instance.version)){instance.loader_version.clone()}else{format!("{}-{}",instance.version,instance.loader_version)};
        (format!("forge-{version}"),format!("https://maven.minecraftforge.net/net/minecraftforge/forge/{version}/forge-{version}-installer.jar"))
    }else{
        if instance.loader_version.is_empty(){
            return Err("NeoForge requires --loader-version with the version from its official release list".into());
        }
        let version=&instance.loader_version;
        if !version.bytes().all(|b|b.is_ascii_alphanumeric()||b".-".contains(&b)){return Err("Invalid NeoForge version".into());}
        (format!("neoforge-{version}"),format!("https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"))
    };
    let digest=net::get(&format!("{url}.sha1"))?;
    let digest=std::str::from_utf8(&digest).map_err(|_|"Invalid installer checksum")?.split_whitespace().next().ok_or("Missing installer checksum")?;
    let installer=store::ensure(app,&format!("installers/{name}.jar"),&url,Some(digest),None,None)?;
    let profiles=app.shared().join("launcher_profiles.json");
    if !profiles.exists(){util::atomic_write(&profiles,b"{\"profiles\":{}}")?;}
    let java=select_java(&instance.java,base.java_major.unwrap_or(8))?;
    let log=File::create(app.dir(&instance.id)?.join("loader-install.log")).map_err(|e|e.to_string())?;
    let status=Command::new(&java).args(["-Djava.awt.headless=true","-jar"]).arg(&installer).arg("--installClient").arg(app.shared())
        .stdin(Stdio::null()).stdout(Stdio::from(log.try_clone().map_err(|e|e.to_string())?)).stderr(Stdio::from(log))
        .status().map_err(|e|format!("Cannot start loader installer: {e}"))?;
    if !status.success(){return Err("Official loader installer failed; inspect the instance's loader-install.log".into());}
    let mut candidates=Vec::new();
    for entry in fs::read_dir(app.shared().join("versions")).map_err(|e|e.to_string())?{
        let entry=entry.map_err(|e|e.to_string())?;
        if !entry.file_type().map_err(|e|e.to_string())?.is_dir(){continue;}
        let id=entry.file_name().to_string_lossy().into_owned();
        if !id.contains(&instance.loader_version){continue;}
        let path=version_path(app,&id)?;
        if let Ok(bytes)=util::read_limited(&path,16*1024*1024){
            if let Ok(version)=Version::parse(&bytes){
                if version.inherits_from.as_deref()==Some(&instance.version){candidates.push((id,bytes,version));}
            }
        }
    }
    candidates.sort_by(|a,b|a.0.cmp(&b.0));
    let (_,bytes,child)=candidates.pop().ok_or("Loader installer did not produce a compatible version")?;
    util::atomic_write(&app.dir(&instance.id)?.join("loader.json"),&bytes)?;
    model::merge(base,child)
}
pub fn java_major(path:&str)->Result<u32>{
    let output=Command::new(path).arg("-version").output().map_err(|e|e.to_string())?;
    if !output.status.success(){return Err("Java version command failed".into());}
    let combined=format!("{} {}",String::from_utf8_lossy(&output.stdout),String::from_utf8_lossy(&output.stderr));
    for line in combined.lines(){
        if !(line.contains("version")||line.contains("openjdk")){continue;}
        for part in line.split([' ','"']){
            let part=part.trim();
            if part.is_empty()||!part.as_bytes()[0].is_ascii_digit(){continue;}
            let mut parts=part.split(['.','-','+']);
            let first=parts.next().unwrap_or("").parse::<u32>().ok();
            if let Some(first)=first{return if first==1{parts.next().unwrap_or("").parse::<u32>().map_err(|_|"Invalid Java version".into())}else{Ok(first)};}
        }
    }
    Err("Could not identify Java major version".into())
}
fn java_candidates()->Vec<String>{
    let mut paths=Vec::new();
    if let Some(home)=std::env::var_os("JAVA_HOME"){paths.push(PathBuf::from(home).join("bin").join(if cfg!(windows){"java.exe"}else{"java"}).to_string_lossy().into_owned());}
    paths.push("java".into());
    #[cfg(target_os="linux")]{
        if let Ok(entries)=fs::read_dir("/usr/lib/jvm"){
            for entry in entries.flatten(){let path=entry.path().join("bin/java");if path.is_file(){paths.push(path.to_string_lossy().into_owned());}}
        }
    }
    #[cfg(target_os="macos")]{
        if let Ok(entries)=fs::read_dir("/Library/Java/JavaVirtualMachines"){
            for entry in entries.flatten(){let path=entry.path().join("Contents/Home/bin/java");if path.is_file(){paths.push(path.to_string_lossy().into_owned());}}
        }
    }
    paths
}
pub fn select_java(preferred:&str,major:u32)->Result<String>{
    if !preferred.is_empty(){
        let actual=java_major(preferred)?;
        if actual!=major{return Err(format!("This version requires Java {major}; configured Java is {actual}"));}
        return Ok(preferred.into());
    }
    for path in java_candidates(){if java_major(&path).ok()==Some(major){return Ok(path);}}
    Err(format!("Java {major} is required. Install that runtime and set it using 'zmcl instance set ID --java PATH'."))
}
pub fn java_report()->String{
    let mut seen=BTreeSet::new();
    let mut items=Vec::new();
    for path in java_candidates(){
        if let Ok(major)=java_major(&path){
            if seen.insert((path.clone(),major)){items.push(util::object(&[("path",quote(&path)),("major",major.to_string())]));}
        }
    }
    format!("[{}]",items.join(","))
}
pub fn launch(app:&App,id:&str,offline:Option<&str>,account_id:Option<&str>,dry:bool,wait:bool)->Result<String>{
    let _lock=util::Lock::acquire(&app.root,&format!("instance-{id}"))?;
    let instance=app.read_instance(id)?;
    if !instance.installed{return Err("Instance is not installed. Run 'zmcl install ID' first.".into());}
    let base=resolve(app,&instance.version,false,&mut BTreeSet::new())?;
    let version=if instance.loader=="vanilla"{base}else{
        let bytes=util::read_limited(&app.dir(id)?.join("loader.json"),16*1024*1024)?;
        model::merge(base,Version::parse(&bytes)?)?
    };
    let required=version.java_major.unwrap_or(8);
    let java=select_java(&instance.java,required)?;
    let (name,uuid,token,user_type)=if let Some(name)=offline{
        if name.is_empty()||name.len()>16||!name.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_'){return Err("Offline name must contain 1–16 ASCII letters, digits or '_'".into());}
        (name.to_owned(),zmcl_core::hash::offline_uuid(name),"0".into(),"legacy".into())
    }else{
        let account=auth::selected(app,account_id)?;
        (account.name,account.uuid,account.access_token,"msa".into())
    };
    let dir=app.dir(id)?;let game=util::safe_join(&dir,"game")?;
    let options=LaunchOptions{
        java:java.clone(),root:app.shared().to_string_lossy().into_owned(),game_dir:game.to_string_lossy().into_owned(),
        natives_dir:dir.join("natives").to_string_lossy().into_owned(),username:name,uuid,access_token:token.clone(),user_type,
        launcher_name:"zmcl".into(),launcher_version:env!("CARGO_PKG_VERSION").into(),memory_mib:instance.memory_mib,
        width:1280,height:720,extra_jvm:Vec::new(),additional_variables:BTreeMap::new()
    };
    let plan=plan::launch_plan(&version,&environment(),&options)?;
    if dry{
        let args=plan.args.iter().map(|a|{
            if token!="0"&&!token.is_empty(){quote(&a.replace(&token,"<redacted>"))}else{quote(a)}
        }).collect::<Vec<_>>().join(",");
        return Ok(util::object(&[("java",quote(&plan.java)),("args",format!("[{args}]")),("working_directory",quote(&plan.working_directory)),("java_major",plan.java_major.to_string()),("offline",offline.is_some().to_string())]));
    }
    let logs=util::safe_join(&dir,"logs")?;fs::create_dir_all(&logs).map_err(|e|e.to_string())?;
    let logfile=logs.join(format!("game-{}-{}.log",util::now(),std::process::id()));
    let file=File::create(&logfile).map_err(|e|e.to_string())?;
    let mut command=Command::new(&plan.java);
    command.args(&plan.args).current_dir(&plan.working_directory).stdin(Stdio::null())
        .stdout(Stdio::from(file.try_clone().map_err(|e|e.to_string())?)).stderr(Stdio::from(file));
    #[cfg(unix)] {use std::os::unix::process::CommandExt;command.process_group(0);}
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x00000200);}
    let mut child=command.spawn().map_err(|e|format!("Cannot start Minecraft: {e}"))?;
    let pid=child.id();
    if let Err(error)=util::atomic_write(&dir.join("last-launch.json"),util::object(&[("pid",pid.to_string()),("started",util::now().to_string()),("log",quote(&logfile.to_string_lossy()))]).as_bytes()){let _=child.kill();let _=child.wait();return Err(error);}
    if wait{
        let status=child.wait().map_err(|e|e.to_string())?;
        if !status.success(){return Err(format!("Minecraft exited with code {}; inspect {}",status.code().unwrap_or(-1),logfile.display()));}
    }
    Ok(util::object(&[("pid",pid.to_string()),("log",quote(&logfile.to_string_lossy())),("started",true.to_string()),("waited",wait.to_string())]))
}
