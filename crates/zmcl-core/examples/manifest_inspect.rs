//! Inspect trusted local version manifests without downloading or launching a game.
use std::{env, fs, process};
use zmcl_core::{
    Environment, Version,
    plan::{LaunchOptions, install_plan, launch_plan},
};

fn inspect() -> Result<(), String> {
    let files: Vec<_> = env::args().skip(1).collect();
    if files.is_empty() {
        return Err("usage: manifest_inspect VERSION.json [...]".into());
    }
    for file in files {
        let bytes = fs::read(&file).map_err(|e| e.to_string())?;
        let version = Version::parse(&bytes)?;
        for os in ["linux", "windows", "osx"] {
            let mut env = Environment::new(os, "x86_64");
            env.os_version = if os == "windows" {
                "10.0.26100"
            } else {
                "14.0"
            }
            .into();
            let install = install_plan(&version, &env)?;
            let options = LaunchOptions {
                root: "/shared minecraft".into(),
                game_dir: "/instances/test".into(),
                natives_dir: "/instances/test/natives".into(),
                ..Default::default()
            };
            let launch = launch_plan(&version, &env, &options)?;
            println!(
                "{} {os}: {} downloads, {} classpath, {} natives, {} argv, Java {}",
                version.id,
                install.downloads.len(),
                install.classpath.len(),
                install.natives.len(),
                launch.args.len(),
                launch.java_major
            );
        }
    }
    Ok(())
}
fn main() {
    if let Err(error) = inspect() {
        eprintln!("{error}");
        process::exit(1);
    }
}
