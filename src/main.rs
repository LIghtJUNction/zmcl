mod app;
mod archive;
mod auth;
mod backup;
mod content;
#[cfg(feature = "gui")]
mod gui;
mod horizon;
mod instance_manage;
mod java;
mod net;
mod runtime;
mod server;
mod store;
mod util;
mod worlds;

pub use app::{App, Instance};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use util::Result;
use zmcl_core::json::quote;

const ARGS: &[zclap::Arg<'static>] = &[
    zclap::Arg::flag("help").short('h'),
    zclap::Arg::flag("json"),
    zclap::Arg::flag("dry-run"),
    zclap::Arg::flag("wait"),
    zclap::Arg::value("root"),
    zclap::Arg::value("version"),
    zclap::Arg::value("loader"),
    zclap::Arg::value("loader-version"),
    zclap::Arg::value("name"),
    zclap::Arg::value("memory"),
    zclap::Arg::value("java"),
    zclap::Arg::value("offline"),
    zclap::Arg::value("account"),
    zclap::Arg::value("client-id"),
    zclap::Arg::value("jobs"),
    zclap::Arg::value("smoke-seconds"),
    zclap::Arg::positional("args").repeat(),
];
struct Cli {
    args: Vec<String>,
    values: BTreeMap<String, String>,
    flags: BTreeSet<String>,
}
impl Cli {
    fn parse(args: &[String]) -> Result<Self> {
        let command = zclap::Command::new("zmcl").args(ARGS);
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        let mut parser = command.parse(&refs);
        let mut out = Self {
            args: Vec::new(),
            values: BTreeMap::new(),
            flags: BTreeSet::new(),
        };
        while let Some(event) = parser.next().map_err(|e| e.to_string())? {
            match event {
                zclap::Event::Flag { arg } => {
                    out.flags.insert(arg.into());
                }
                zclap::Event::Value { arg: "args", value } => out.args.push(value.into()),
                zclap::Event::Value { arg, value } => {
                    out.values.insert(arg.into(), value.into());
                }
                zclap::Event::Command { .. } => {}
                _ => return Err("Unsupported parser event".into()),
            }
        }
        parser.finish().map_err(|e| e.to_string())?;
        Ok(out)
    }
    fn val(&self, key: &str) -> Option<&str> {
        self.values.get(key).map(String::as_str)
    }
    fn pos(&self, index: usize) -> Result<&str> {
        self.args
            .get(index)
            .map(String::as_str)
            .ok_or_else(|| "Missing argument. Run zmcl --help.".into())
    }
}
fn help() {
    println!(
        r#"zmcl — a small native Minecraft launcher

  zmcl gui                           Open the native interface
  zmcl versions [--json]             List official Minecraft versions
  zmcl instance list [--json]
  zmcl instance create ID --version 1.21.1 [--loader fabric|quilt|forge|neoforge]
  zmcl instance clone SOURCE_ID NEW_ID
  zmcl instance trash ID
  zmcl instance trash-list
  zmcl instance restore TRASH_ID [NEW_ID]
  zmcl instance show ID
  zmcl instance set ID [--memory 4096] [--java /path/to/java] [--name NAME]
  zmcl install ID [--jobs 4]          Install or repair verified game files
  zmcl launch ID [--account UUID] [--dry-run] [--wait]
  zmcl launch ID --offline NAME       Explicit offline single-player profile
  zmcl auth login --client-id ID      Use your registered Microsoft application
  zmcl auth refresh UUID
  zmcl auth logout UUID
  zmcl account list [--json]
  zmcl mods search QUERY [--version VERSION] [--loader fabric]
  zmcl mods add ID PROJECT
  zmcl mods list ID
  zmcl mods enable ID PROJECT
  zmcl mods disable ID PROJECT
  zmcl pack import FILE.mrpack ID
  zmcl pack export ID FILE.mrpack
  zmcl storage stats [--json]
  zmcl world list ID
  zmcl world info ID NAME
  zmcl world import ID SOURCE NAME
  zmcl world export ID NAME FILE.zip
  zmcl world clone SOURCE_ID NAME TARGET_ID NEW_NAME
  zmcl world path ID NAME
  zmcl horizon versions
  zmcl horizon install [TAG]
  zmcl horizon list
  zmcl horizon launch INSTALL_ID [--dry-run] [--wait]
  zmcl backup create ID [LABEL]
  zmcl backup list ID
  zmcl backup restore ID BACKUP_ID
  zmcl server create ID --version VERSION
  zmcl server install ID
  zmcl server start|stop|status ID
  zmcl server list
  zmcl server ping HOST [PORT]
  zmcl java list
  zmcl java install 21                Install an official verified JRE
  zmcl doctor [--json]

Global: --root DIR (or ZMCL_HOME), --help, --version.
Install data and Java/game memory are separate from launcher size.
All commands are noninteractive except explicit Microsoft sign-in.
"#
    );
}
fn main() {
    if let Err(error) = run_cli(std::env::args().skip(1).collect()) {
        eprintln!("zmcl: {error}");
        std::process::exit(1);
    }
}
pub fn run_cli(args: Vec<String>) -> Result<()> {
    if args == ["--version"] {
        println!("zmcl {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    let cli = Cli::parse(&args)?;
    if cli.flags.contains("help") {
        help();
        return Ok(());
    }
    let command = cli.args.first().map(String::as_str).unwrap_or("gui");
    if command == "gui" {
        if let Some(root) = cli.val("root") {
            // Child processes inherit the root via explicit arguments, never process-wide environment mutation.
            #[cfg(feature = "gui")]
            return gui::run_with_root(
                cli.val("smoke-seconds").and_then(|x| x.parse().ok()),
                Some(root.to_owned()),
            );
            #[cfg(not(feature = "gui"))]
            let _ = root;
        }
        #[cfg(feature = "gui")]
        return gui::run(cli.val("smoke-seconds").and_then(|x| x.parse().ok()));
        #[cfg(not(feature = "gui"))]
        return Err("This binary was built without GUI support. Run zmcl --help.".into());
    }
    let app = App::new(cli.val("root").map(str::to_owned))?;
    match command {
        "horizon" => match cli.pos(1)? {
            "versions" => println!("{}", horizon::versions()?),
            "install" => println!(
                "{}",
                horizon::install(&app, cli.args.get(2).map(String::as_str))?
            ),
            "list" => println!("{}", horizon::list(&app)?),
            "launch" => println!(
                "{}",
                horizon::launch(
                    &app,
                    cli.pos(2)?,
                    cli.flags.contains("dry-run"),
                    cli.flags.contains("wait")
                )?
            ),
            _ => return Err("Unknown Horizon command".into()),
        },
        "world" => match cli.pos(1)? {
            "list" => println!("{}", worlds::list(&app, cli.pos(2)?)?),
            "info" => println!("{}", worlds::info(&app, cli.pos(2)?, cli.pos(3)?)?),
            "path" => println!("{}", worlds::reveal(&app, cli.pos(2)?, cli.pos(3)?)?),
            "import" => println!(
                "{}",
                worlds::import(&app, cli.pos(2)?, Path::new(cli.pos(3)?), cli.pos(4)?)?
            ),
            "export" => println!(
                "{}",
                worlds::export(&app, cli.pos(2)?, cli.pos(3)?, Path::new(cli.pos(4)?))?
            ),
            "clone" => println!(
                "{}",
                worlds::clone(&app, cli.pos(2)?, cli.pos(3)?, cli.pos(4)?, cli.pos(5)?)?
            ),
            _ => return Err("Unknown world command".into()),
        },
        "backup" => match cli.pos(1)? {
            "create" => println!(
                "{}",
                backup::create(&app, cli.pos(2)?, cli.args.get(3).map(String::as_str))?
            ),
            "list" => println!("{}", backup::list(&app, cli.pos(2)?)?),
            "restore" => {
                backup::restore(&app, cli.pos(2)?, cli.pos(3)?)?;
                println!("{{\"restored\":true}}");
            }
            _ => return Err("Unknown backup command".into()),
        },
        "server" => match cli.pos(1)? {
            "create" => println!(
                "{}",
                server::create(
                    &app,
                    cli.pos(2)?,
                    cli.val("version").ok_or("--version is required")?
                )?
            ),
            "install" => println!("{}", server::install(&app, cli.pos(2)?)?),
            "start" => println!("{}", server::start(&app, cli.pos(2)?)?),
            "stop" => println!("{}", server::stop(&app, cli.pos(2)?)?),
            "status" => println!("{}", server::status(&app, cli.pos(2)?)?),
            "list" => println!("{}", server::list(&app)?),
            "ping" => println!(
                "{}",
                server::ping(
                    cli.pos(2)?,
                    cli.args
                        .get(3)
                        .map(|x| x.parse::<u16>())
                        .transpose()
                        .map_err(|_| "Invalid port")?
                        .unwrap_or(25565)
                )?
            ),
            _ => return Err("Unknown server command".into()),
        },
        "java" => match cli.pos(1)? {
            "list" => println!("{}", java::list(&app)?),
            "install" => println!(
                "{}",
                java::install(
                    &app,
                    cli.pos(2)?
                        .parse::<u32>()
                        .map_err(|_| "Invalid Java major")?
                )?
            ),
            _ => return Err("Unknown java command".into()),
        },
        "versions" => println!("{}", runtime::versions(&app)?),
        "instance" => match cli.pos(1)? {
            "clone" => println!(
                "{}",
                instance_manage::clone_instance(&app, cli.pos(2)?, cli.pos(3)?)?
            ),
            "trash" => println!("{}", instance_manage::trash(&app, cli.pos(2)?)?),
            "trash-list" => println!("{}", instance_manage::list_trash(&app)?),
            "restore" => println!(
                "{}",
                instance_manage::restore(&app, cli.pos(2)?, cli.args.get(3).map(String::as_str))?
            ),
            "list" => println!(
                "{{\"instances\":[{}]}}",
                app.instances()?
                    .iter()
                    .map(Instance::json)
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            "show" => println!("{}", app.read_instance(cli.pos(2)?)?.json()),
            "create" => {
                let instance = app.create_instance(
                    cli.pos(2)?,
                    cli.val("version").ok_or("--version is required")?,
                    cli.val("loader").unwrap_or("vanilla"),
                    cli.val("loader-version").unwrap_or(""),
                )?;
                println!("{}", instance.json());
            }
            "set" => {
                let id = cli.pos(2)?;
                let _lock = util::Lock::acquire(&app.root, &format!("instance-{id}"))?;
                let mut instance = app.read_instance(id)?;
                if let Some(v) = cli.val("memory") {
                    instance.memory_mib = v.parse().map_err(|_| "Invalid memory value")?;
                }
                if let Some(v) = cli.val("java") {
                    instance.java = v.into();
                }
                if let Some(v) = cli.val("name") {
                    if v.len() > 256 || v.chars().any(char::is_control) {
                        return Err("Invalid display name".into());
                    }
                    instance.name = v.into();
                }
                if let Some(v) = cli.val("version") {
                    instance.version = v.into();
                    instance.installed = false;
                }
                if let Some(v) = cli.val("loader") {
                    instance.loader = v.into();
                    instance.installed = false;
                }
                if let Some(v) = cli.val("loader-version") {
                    instance.loader_version = v.into();
                    instance.installed = false;
                }
                let checked = Instance::parse(instance.json().as_bytes())?;
                app.write_instance(&checked)?;
                println!("{}", checked.json());
            }
            _ => return Err("Unknown instance command".into()),
        },
        "install" => {
            let jobs = cli
                .val("jobs")
                .unwrap_or("4")
                .parse::<usize>()
                .map_err(|_| "Invalid job count")?;
            runtime::install(&app, cli.pos(1)?, jobs)?;
            println!("{{\"installed\":true}}");
        }
        "launch" => println!(
            "{}",
            runtime::launch(
                &app,
                cli.pos(1)?,
                cli.val("offline"),
                cli.val("account"),
                cli.flags.contains("dry-run"),
                cli.flags.contains("wait")
            )?
        ),
        "mods" => match cli.pos(1)? {
            "search" => println!(
                "{}",
                content::search(cli.pos(2)?, cli.val("version"), cli.val("loader"))?
            ),
            "list" => println!("{}", content::list(&app, cli.pos(2)?)?),
            "add" => {
                content::add(&app, cli.pos(2)?, cli.pos(3)?)?;
                println!("{{\"installed\":true}}");
            }
            "enable" | "disable" => {
                content::toggle(&app, cli.pos(2)?, cli.pos(3)?, cli.pos(1)? == "enable")?;
                println!("{{\"updated\":true}}");
            }
            _ => return Err("Unknown mods command".into()),
        },
        "pack" => match cli.pos(1)? {
            "import" => {
                content::import_pack(&app, Path::new(cli.pos(2)?), cli.pos(3)?)?;
                println!("{{\"imported\":true}}");
            }
            "export" => {
                content::export_pack(&app, cli.pos(2)?, Path::new(cli.pos(3)?))?;
                println!("{{\"exported\":true}}");
            }
            _ => return Err("Unknown pack command".into()),
        },
        "auth" => match cli.pos(1)? {
            "login" => println!("{}", auth::login(&app, cli.val("client-id"))?),
            "refresh" => println!("{}", auth::refresh(&app, cli.pos(2)?)?.public_json()),
            "logout" => {
                auth::logout(&app, cli.pos(2)?)?;
                println!("{{\"logged_out\":true}}");
            }
            _ => return Err("Unknown auth command".into()),
        },
        "account" => {
            if cli.pos(1)? != "list" {
                return Err("Unknown account command".into());
            }
            println!("{}", auth::list(&app)?);
        }
        "storage" => {
            if cli.pos(1)? != "stats" {
                return Err("Unknown storage command".into());
            }
            println!("{}", store::stats(&app)?);
        }
        "doctor" => println!(
            "{}",
            util::object(&[
                ("version", quote(env!("CARGO_PKG_VERSION"))),
                ("os", quote(std::env::consts::OS)),
                ("arch", quote(std::env::consts::ARCH)),
                ("curl", util::command_exists("curl").to_string()),
                ("python3", util::command_exists("python3").to_string()),
                ("java", runtime::java_report()),
                ("gui", cfg!(feature = "gui").to_string()),
                ("instances", app.instances()?.len().to_string())
            ])
        ),
        _ => return Err(format!("Unknown command '{}'. Run zmcl --help.", command)),
    }
    Ok(())
}
