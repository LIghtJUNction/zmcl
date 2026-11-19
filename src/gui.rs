//! Native, event-driven launcher UI. All game and account operations use the same CLI.
use std::{
    collections::VecDeque,
    io::Read,
    process::{Child, Command, ExitStatus, Stdio},
    sync::mpsc::{self, Receiver, SyncSender, TryRecvError},
    thread,
    time::{Duration, Instant},
};
use zmcl_core::json::{Kind, Value, get_str, with_json};
use zui::{ButtonStyle, Canvas, Event, Rect, Theme, Ui, Window};

const BG: u32 = 0x111214ff;
const SIDE: u32 = 0x151618ff;
const PANEL: u32 = 0x1b1d20ff;
const INK: u32 = 0xeeeae4ff;
const MUTED: u32 = 0x929397ff;
const DIM: u32 = 0x62666bff;
const LINE: u32 = 0x2a2c30ff;
const ACCENT: u32 = 0xd6ac82ff;
const ERROR: u32 = 0xf1a69bff;
const OUTPUT_LIMIT: usize = 2 * 1024 * 1024;
const LOG_LINES: usize = 96;

// Preserve the launcher palette when controls are disabled.
#[allow(clippy::too_many_arguments)]
trait LauncherInput {
    fn launcher_input(
        &mut self,
        canvas: &mut Canvas<'_>,
        id: u64,
        rect: Rect,
        value: &mut String,
        placeholder: &str,
        secret: bool,
        enabled: bool,
    ) -> bool;
}
#[allow(clippy::too_many_arguments)]
impl LauncherInput for Ui {
    fn launcher_input(
        &mut self,
        canvas: &mut Canvas<'_>,
        id: u64,
        rect: Rect,
        value: &mut String,
        placeholder: &str,
        secret: bool,
        enabled: bool,
    ) -> bool {
        if enabled {
            return self.input(canvas, id, rect, value, placeholder, secret, true);
        }
        canvas.rect(rect, 12., LINE);
        canvas.rect(rect.inset(1.), 11., PANEL);
        let display = if value.is_empty() {
            placeholder.to_owned()
        } else if secret {
            "•".repeat(value.chars().count())
        } else {
            value.clone()
        };
        canvas.ellipsis(
            &display,
            rect.x + 11.,
            rect.y + 11.,
            (rect.w - 22.).max(1.),
            15,
            false,
            DIM,
        );
        false
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Page {
    Library,
    Discover,
    Packs,
    Accounts,
    System,
}

#[derive(Default, Clone)]
struct Instance {
    id: String,
    name: String,
    version: String,
    loader: String,
    installed: bool,
}

#[derive(Default, Clone)]
struct Mod {
    id: String,
    title: String,
    description: String,
    downloads: u64,
}

#[derive(Default, Clone)]
struct Account {
    id: String,
    name: String,
    kind: String,
}

#[derive(Clone)]
enum Task {
    Instances,
    Versions,
    Accounts,
    Storage,
    Doctor,
    Create {
        id: String,
        version: String,
        loader: String,
        loader_version: String,
    },
    Install(String),
    Launch {
        id: String,
        offline: Option<String>,
        account: Option<String>,
    },
    Search {
        query: String,
        version: String,
        loader: String,
    },
    AddMod {
        instance: String,
        project: String,
    },
    Login(String),
    ImportPack {
        path: String,
        id: String,
    },
    ExportPack {
        path: String,
        id: String,
    },
}

impl Task {
    fn label(&self) -> String {
        match self {
            Self::Instances => "Reading your library".into(),
            Self::Versions => "Fetching Minecraft versions".into(),
            Self::Accounts => "Reading accounts".into(),
            Self::Storage => "Checking shared storage".into(),
            Self::Doctor => "Checking your setup".into(),
            Self::Create { id, .. } => format!("Creating {id}"),
            Self::Install(id) => format!("Installing {id}"),
            Self::Launch { id, .. } => format!("Starting {id}"),
            Self::Search { .. } => "Searching Modrinth".into(),
            Self::AddMod { .. } => "Adding the mod".into(),
            Self::Login(_) => "Waiting for Microsoft sign-in".into(),
            Self::ImportPack { .. } => "Importing the modpack".into(),
            Self::ExportPack { .. } => "Exporting the modpack".into(),
        }
    }

    fn silent(&self) -> bool {
        matches!(
            self,
            Self::Instances | Self::Accounts | Self::Versions | Self::Storage | Self::Search { .. }
        )
    }

    fn args(&self) -> Vec<String> {
        let mut args: Vec<String> = match self {
            Self::Instances => vec!["instance", "list", "--json"],
            Self::Versions => vec!["versions", "--json"],
            Self::Accounts => vec!["account", "list", "--json"],
            Self::Storage => vec!["storage", "stats", "--json"],
            Self::Doctor => vec!["doctor", "--json"],
            Self::Create {
                id,
                version,
                loader,
                ..
            } => vec![
                "instance",
                "create",
                id,
                "--version",
                version,
                "--loader",
                loader,
            ],
            Self::Install(id) => vec!["install", id],
            Self::Launch { id, .. } => vec!["launch", id],
            Self::Search { query, .. } => vec!["mods", "search", query],
            Self::AddMod { instance, project } => vec!["mods", "add", instance, project],
            Self::Login(_) => vec!["auth", "login"],
            Self::ImportPack { path, id } => vec!["pack", "import", path, id],
            Self::ExportPack { path, id } => vec!["pack", "export", id, path],
        }
        .into_iter()
        .map(str::to_owned)
        .collect();
        match self {
            Self::Launch {
                offline: Some(name),
                ..
            } => args.extend(["--offline".into(), name.clone()]),
            Self::Launch {
                offline: None,
                account: Some(id),
                ..
            } => args.extend(["--account".into(), id.clone()]),
            Self::Create { loader_version, .. } if !loader_version.trim().is_empty() => {
                args.extend(["--loader-version".into(), loader_version.trim().into()])
            }
            Self::Search {
                version, loader, ..
            } => {
                if !version.trim().is_empty() {
                    args.extend(["--version".into(), version.trim().into()]);
                }
                if !loader.trim().is_empty() && loader != "any" {
                    args.extend(["--loader".into(), loader.clone()]);
                }
            }
            Self::Login(id) if !id.trim().is_empty() => {
                args.extend(["--client-id".into(), id.trim().into()])
            }
            _ => {}
        }
        args
    }
}

enum PipeMessage {
    Data(bool, Vec<u8>),
    End,
}

fn reader(mut stream: impl Read + Send + 'static, stderr: bool, tx: SyncSender<PipeMessage>) {
    thread::spawn(move || {
        let mut bytes = [0u8; 4096];
        loop {
            match stream.read(&mut bytes) {
                Ok(0) => break,
                Ok(n) => {
                    if tx
                        .send(PipeMessage::Data(stderr, bytes[..n].to_vec()))
                        .is_err()
                    {
                        return;
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        let _ = tx.send(PipeMessage::End);
    });
}

struct Job {
    task: Task,
    child: Child,
    receiver: Receiver<PipeMessage>,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    pipes: usize,
    status: Option<ExitStatus>,
    truncated: bool,
    cancelled: bool,
    started: Instant,
}

impl Job {
    fn start(task: Task, root: Option<&str>) -> Result<Self, String> {
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        let mut command = Command::new(executable);
        if let Some(root) = root {
            command.args(["--root", root]);
        }
        command
            .args(task.args())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Could not start operation: {e}"))?;
        let (tx, receiver) = mpsc::sync_channel(32);
        reader(
            child.stdout.take().ok_or("Missing operation output")?,
            false,
            tx.clone(),
        );
        reader(
            child.stderr.take().ok_or("Missing operation diagnostics")?,
            true,
            tx,
        );
        Ok(Self {
            task,
            child,
            receiver,
            stdout: Vec::new(),
            stderr: Vec::new(),
            pipes: 2,
            status: None,
            truncated: false,
            cancelled: false,
            started: Instant::now(),
        })
    }

    fn poll(&mut self, log: &mut VecDeque<String>) -> bool {
        let mut changed = false;
        for _ in 0..64 {
            match self.receiver.try_recv() {
                Ok(PipeMessage::Data(is_error, bytes)) => {
                    let output = if is_error {
                        &mut self.stderr
                    } else {
                        &mut self.stdout
                    };
                    let room = OUTPUT_LIMIT.saturating_sub(output.len());
                    output.extend_from_slice(&bytes[..bytes.len().min(room)]);
                    self.truncated |= bytes.len() > room;
                    if is_error || !self.task.silent() {
                        append_log(log, &String::from_utf8_lossy(&bytes));
                    }
                    changed = true;
                }
                Ok(PipeMessage::End) => self.pipes = self.pipes.saturating_sub(1),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    self.pipes = 0;
                    break;
                }
            }
        }
        if self.status.is_none() {
            match self.child.try_wait() {
                Ok(status) => self.status = status,
                Err(error) => {
                    append_log(log, &format!("Could not read operation status: {error}"));
                    self.cancel();
                }
            }
        }
        changed || self.finished()
    }

    fn finished(&self) -> bool {
        self.status.is_some() && self.pipes == 0
    }

    fn cancel(&mut self) {
        if self.status.is_some() {
            return;
        }
        self.cancelled = true;
        #[cfg(unix)]
        {
            let _ = Command::new("kill")
                .args(["-TERM", "--", &format!("-{}", self.child.id())])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &self.child.id().to_string()])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = self.child.kill();
        self.status = self.child.wait().ok();
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        if self.status.is_none() {
            self.cancel();
        }
    }
}

fn append_log(log: &mut VecDeque<String>, text: &str) {
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        let line: String = line
            .chars()
            .filter(|c| !c.is_control() || *c == '\t')
            .take(400)
            .collect();
        log.push_back(line);
        while log.len() > LOG_LINES {
            log.pop_front();
        }
    }
}

enum Action {
    Navigate(Page),
    Start(Task),
    Select(String),
    New,
    CloseForm,
    Cancel,
    Logs,
    Quit,
}

struct App {
    root: Option<String>,
    ui: Ui,
    page: Page,
    instances: Vec<Instance>,
    accounts: Vec<Account>,
    mods: Vec<Mod>,
    versions: Vec<String>,
    selected: Option<String>,
    selected_account: Option<String>,
    pack_path: String,
    pack_id: String,
    pack_export: bool,
    latest_release: String,
    queue: VecDeque<Task>,
    job: Option<Job>,
    log: VecDeque<String>,
    notice: String,
    error: bool,
    form: bool,
    show_logs: bool,
    scroll: usize,
    log_scroll: usize,
    new_id: String,
    new_version: String,
    new_loader: usize,
    new_loader_version: String,
    offline: bool,
    username: String,
    query: String,
    search_version: String,
    search_loader: usize,
    oauth_client: String,
    storage: Vec<(String, String)>,
    diagnostics: Vec<(String, String)>,
    searched: bool,
    loaded: bool,
}

impl App {
    fn new(root: Option<String>) -> Self {
        Self {
            root,
            ui: Ui::default(),
            page: Page::Library,
            instances: Vec::new(),
            accounts: Vec::new(),
            mods: Vec::new(),
            versions: Vec::new(),
            selected: None,
            selected_account: None,
            pack_path: String::new(),
            pack_id: String::new(),
            pack_export: false,
            latest_release: String::new(),
            queue: VecDeque::from([Task::Instances, Task::Accounts, Task::Storage]),
            job: None,
            log: VecDeque::new(),
            notice: String::new(),
            error: false,
            form: false,
            show_logs: false,
            scroll: 0,
            log_scroll: 0,
            new_id: String::new(),
            new_version: String::new(),
            new_loader: 0,
            new_loader_version: String::new(),
            offline: false,
            username: String::new(),
            query: String::new(),
            search_version: String::new(),
            search_loader: 0,
            oauth_client: String::new(),
            storage: Vec::new(),
            diagnostics: Vec::new(),
            searched: false,
            loaded: false,
        }
    }

    fn busy(&self) -> bool {
        self.job.is_some() || !self.queue.is_empty()
    }

    fn poll(&mut self) -> bool {
        let mut changed = false;
        if let Some(job) = &mut self.job {
            changed |= job.poll(&mut self.log);
            if job.finished() {
                let job = self.job.take().expect("finished operation is present");
                let result = if job.cancelled {
                    Err("Operation cancelled.".into())
                } else if job.truncated {
                    Err(
                        "Operation output exceeded the 2 MiB limit. Use the CLI for this result."
                            .into(),
                    )
                } else if !job.status.is_some_and(|status| status.success()) {
                    let reason = String::from_utf8_lossy(&job.stderr);
                    Err(reason
                        .lines()
                        .rev()
                        .find(|s| !s.trim().is_empty())
                        .unwrap_or("Operation failed; open activity for details.")
                        .to_owned())
                } else {
                    self.apply_result(&job.task, &job.stdout)
                };
                if let Err(error) = result {
                    self.notice = truncate_chars(error.trim(), 220);
                    self.error = true;
                    append_log(&mut self.log, &error);
                } else if !job.task.silent() {
                    self.notice = match &job.task {
                        Task::Create { .. } => {
                            "Instance created. Install it when you are ready.".into()
                        }
                        Task::Install(_) => "Installation complete. Ready to play.".into(),
                        Task::Launch { .. } => {
                            "Minecraft is starting. You can close the launcher.".into()
                        }
                        Task::Login(_) => "Account connected.".into(),
                        Task::AddMod { .. } => "Mod added to your instance.".into(),
                        Task::ImportPack { .. } => {
                            "Modpack imported. Find it in your Library.".into()
                        }
                        Task::ExportPack { .. } => "Modpack exported.".into(),
                        _ => "Check complete.".into(),
                    };
                    self.error = false;
                }
                changed = true;
            }
        }
        if self.job.is_none()
            && let Some(task) = self.queue.pop_front()
        {
            match Job::start(task, self.root.as_deref()) {
                Ok(job) => self.job = Some(job),
                Err(error) => {
                    self.notice = error;
                    self.error = true;
                }
            }
            changed = true;
        }
        changed
    }

    fn apply_result(&mut self, task: &Task, bytes: &[u8]) -> Result<(), String> {
        match task {
            Task::Instances => {
                self.instances = with_json(bytes, |root| {
                    let array = root.get("instances").unwrap_or(root);
                    if array.kind() != Kind::Array {
                        return Err("Instance list was not an array".into());
                    }
                    Ok(array
                        .array()
                        .take(10000)
                        .filter_map(|item| {
                            let id = get_str(item, "id")?;
                            Some(Instance {
                                name: get_str(item, "name").unwrap_or_else(|| id.clone()),
                                id,
                                version: get_str(item, "version").unwrap_or_default(),
                                loader: get_str(item, "loader").unwrap_or_else(|| "vanilla".into()),
                                installed: item
                                    .get("installed")
                                    .and_then(Value::as_bool)
                                    .unwrap_or(false),
                            })
                        })
                        .collect())
                })?;
                self.loaded = true;
                if !self
                    .instances
                    .iter()
                    .any(|item| Some(&item.id) == self.selected.as_ref())
                {
                    self.selected = self.instances.first().map(|item| item.id.clone());
                }
            }
            Task::Versions => {
                let (latest, versions) = with_json(bytes, |root| {
                    let latest = root
                        .get("latest")
                        .and_then(|v| get_str(v, "release"))
                        .unwrap_or_default();
                    let array = root.get("versions").unwrap_or(root);
                    Ok((
                        latest,
                        array
                            .array()
                            .filter(|v| get_str(*v, "type").as_deref() == Some("release"))
                            .take(12)
                            .filter_map(|v| get_str(v, "id"))
                            .collect::<Vec<_>>(),
                    ))
                })?;
                self.latest_release = latest;
                self.versions = versions;
                if self.new_version.trim().is_empty() {
                    self.new_version = self.latest_release.clone();
                }
            }
            Task::Accounts => {
                self.accounts = with_json(bytes, |root| {
                    Ok(root
                        .get("accounts")
                        .unwrap_or(root)
                        .array()
                        .take(100)
                        .map(|item| Account {
                            id: get_str(item, "id")
                                .or_else(|| get_str(item, "uuid"))
                                .unwrap_or_default(),
                            name: get_str(item, "name")
                                .or_else(|| get_str(item, "username"))
                                .or_else(|| get_str(item, "profile_name"))
                                .unwrap_or_else(|| "Minecraft account".into()),
                            kind: get_str(item, "type")
                                .or_else(|| get_str(item, "kind"))
                                .unwrap_or_else(|| "Microsoft".into()),
                        })
                        .collect())
                })?;
                if !self
                    .accounts
                    .iter()
                    .any(|account| Some(&account.id) == self.selected_account.as_ref())
                {
                    self.selected_account = if self.accounts.len() == 1 {
                        Some(self.accounts[0].id.clone())
                    } else {
                        None
                    };
                }
            }
            Task::Storage => {
                self.storage = data_pairs(bytes, true)?;
            }
            Task::Doctor => {
                self.diagnostics = data_pairs(bytes, false)?;
            }
            Task::Search { .. } => {
                self.mods = with_json(bytes, |root| {
                    let array = root
                        .get("hits")
                        .or_else(|| root.get("results"))
                        .unwrap_or(root);
                    if array.kind() != Kind::Array {
                        return Err("Search response was not an array".into());
                    }
                    Ok(array
                        .array()
                        .take(100)
                        .filter_map(|item| {
                            Some(Mod {
                                id: get_str(item, "project_id")
                                    .or_else(|| get_str(item, "id"))
                                    .or_else(|| get_str(item, "slug"))?,
                                title: get_str(item, "title")
                                    .or_else(|| get_str(item, "name"))
                                    .unwrap_or_else(|| "Untitled project".into()),
                                description: get_str(item, "description").unwrap_or_default(),
                                downloads: item
                                    .get("downloads")
                                    .and_then(Value::as_u64)
                                    .unwrap_or(0),
                            })
                        })
                        .collect())
                })?;
                self.searched = true;
                self.scroll = 0;
            }
            Task::Create { id, .. } => {
                self.selected = Some(id.clone());
                self.form = false;
                self.new_id.clear();
                self.scroll = 0;
                self.queue.push_back(Task::Instances);
            }
            Task::Install(_) | Task::AddMod { .. } => {
                self.queue.extend([Task::Instances, Task::Storage]);
            }
            Task::Login(_) => {
                self.queue.push_back(Task::Accounts);
            }
            Task::ImportPack { id, .. } => {
                self.selected = Some(id.clone());
                self.queue.extend([Task::Instances, Task::Storage]);
            }
            Task::Launch { .. } | Task::ExportPack { .. } => {}
        }
        Ok(())
    }

    fn action(&mut self, action: Action) -> bool {
        match action {
            Action::Quit => return false,
            Action::Navigate(page) => {
                self.page = page;
                self.scroll = 0;
                self.form = false;
                self.show_logs = false;
            }
            Action::Start(task) if !self.busy() => {
                self.notice.clear();
                self.error = false;
                if matches!(task, Task::Login(_)) {
                    self.show_logs = true;
                    self.log.clear();
                }
                self.queue.push_back(task);
            }
            Action::Start(_) => {}
            Action::Select(id) => {
                self.selected = Some(id);
            }
            Action::New => {
                self.form = true;
                self.show_logs = false;
                self.notice.clear();
                self.error = false;
                if self.new_version.is_empty() {
                    self.new_version = self.latest_release.clone();
                }
            }
            Action::CloseForm => self.form = false,
            Action::Cancel => {
                self.queue.clear();
                if let Some(job) = &mut self.job {
                    job.cancel();
                }
            }
            Action::Logs => {
                self.show_logs = !self.show_logs;
                self.log_scroll = 0;
            }
        }
        true
    }

    fn draw(&mut self, c: &mut Canvas<'_>, event: Option<Event>) -> Option<Action> {
        if let Some(Event::Scroll(delta)) = &event {
            let value = if self.show_logs {
                &mut self.log_scroll
            } else {
                &mut self.scroll
            };
            *value = if *delta > 0 {
                value.saturating_add(1)
            } else {
                value.saturating_sub(1)
            };
        }
        if matches!(event, Some(Event::Key { code: 27, .. })) {
            self.form = false;
            self.show_logs = false;
        }
        self.ui.begin(event);
        let compact = c.width < 820.;
        let side = if compact { 92. } else { 192. };
        let margin = if c.width < 640. { 20. } else { 42. };
        let x = side + margin;
        let w = (c.width - x - margin).max(80.);
        let h = c.height;
        let mut action = None;
        c.rect(Rect::new(0., 0., side, h), 0., SIDE);
        cube(c, if compact { 28. } else { 30. }, 38., 26.);
        if !compact {
            c.text("zmcl", 70., 38., 23, true, INK);
        }
        let pages = [
            (Page::Library, "Library", "Play"),
            (Page::Discover, "Discover", "Mods"),
            (Page::Packs, "Modpacks", "Packs"),
            (Page::Accounts, "Accounts", "Login"),
            (Page::System, "Settings", "Info"),
        ];
        let content_enabled = !self.form && !self.show_logs;
        for (i, (page, label, short)) in pages.into_iter().enumerate() {
            let r = Rect::new(14., 130. + i as f32 * 54., side - 28., 42.);
            if self.page == page {
                c.rect(r, 9., PANEL);
                c.rect(Rect::new(14., r.y + 13., 2., 16.), 1., ACCENT);
            }
            if self.ui.button(
                c,
                10 + i as u64,
                r,
                if compact { short } else { label },
                ButtonStyle::Quiet,
                content_enabled,
            ) {
                action = Some(Action::Navigate(page));
            }
        }
        if h > 520. && !compact {
            c.text("PROJECT Z", 30., h - 112., 10, true, DIM);
            c.text("More room to play.", 30., h - 86., 12, false, MUTED);
        }
        if self.ui.button(
            c,
            15,
            Rect::new(14., h - 55., side - 28., 34.),
            "Quit",
            ButtonStyle::Quiet,
            content_enabled,
        ) {
            action = Some(Action::Quit);
        }
        if w < 280. || h < 500. {
            c.text("A little more room", x, 65., 18, true, INK);
            c.ellipsis(
                "Enlarge this window to continue.",
                x,
                102.,
                w,
                12,
                false,
                MUTED,
            );
        } else {
            c.clip(Rect::new(side, 0., c.width - side, h - 66.));
            let page_action = match self.page {
                Page::Library => self.draw_library(c, x, w, h, content_enabled),
                Page::Discover => self.draw_discover(c, x, w, h, content_enabled),
                Page::Packs => self.draw_packs(c, x, w, h, content_enabled),
                Page::Accounts => self.draw_accounts(c, x, w, h, content_enabled),
                Page::System => self.draw_system(c, x, w, h, content_enabled),
            };
            c.unclip();
            if page_action.is_some() {
                action = page_action;
            }
        }
        self.draw_status(c, x, w, h, &mut action, content_enabled);
        if self.form {
            action = self.draw_form(c).or(action);
        }
        if self.show_logs {
            action = self.draw_logs(c).or(action);
        }
        self.ui.end();
        action
    }

    fn heading(c: &mut Canvas<'_>, x: f32, w: f32, title: &str, subtitle: &str) {
        c.ellipsis(title, x, 50., w, if w < 380. { 30 } else { 38 }, true, INK);
        c.ellipsis(subtitle, x, 107., w, 14, false, MUTED);
    }

    fn draw_library(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        h: f32,
        enabled: bool,
    ) -> Option<Action> {
        let mut action = None;
        Self::heading(
            c,
            x,
            w,
            "Your worlds.",
            "A home for every way you play Minecraft.",
        );
        let busy = self.busy();
        if self.ui.button(
            c,
            100,
            Rect::new(x, 156., 158., 40.),
            "+  New instance",
            ButtonStyle::Secondary,
            enabled && !busy,
        ) {
            action = Some(Action::New);
        }
        if self.ui.button(
            c,
            101,
            Rect::new(x + 168., 156., 90., 40.),
            "Refresh",
            ButtonStyle::Quiet,
            enabled && !busy,
        ) {
            action = Some(Action::Start(Task::Instances));
        }
        if self.instances.is_empty() {
            let center = x + w / 2.;
            let cy = 250. + ((h - 500.).max(0.) * 0.25);
            cube(c, center - 26., cy, 52.);
            centered(
                c,
                if self.loaded {
                    "Your next adventure starts here."
                } else {
                    "Opening your library…"
                },
                x,
                cy + 90.,
                w,
                22,
                true,
                INK,
            );
            centered(
                c,
                "Create an instance. Choose a version. Make it yours.",
                x,
                cy + 129.,
                w,
                14,
                false,
                MUTED,
            );
            if self.ui.button(
                c,
                102,
                Rect::new(center - 94., cy + 175., 188., 43.),
                "Create your first instance",
                ButtonStyle::Secondary,
                enabled && !busy,
            ) {
                action = Some(Action::New);
            }
            return action;
        }
        let available = (h - 428.).max(78.);
        let count = ((available / 80.).floor() as usize).max(1);
        self.scroll = self.scroll.min(self.instances.len().saturating_sub(count));
        for (i, item) in self
            .instances
            .iter()
            .enumerate()
            .skip(self.scroll)
            .take(count)
        {
            let y = 220. + (i - self.scroll) as f32 * 80.;
            let selected = self.selected.as_deref() == Some(&item.id);
            if selected {
                c.rect(Rect::new(x - 12., y - 7., w + 24., 70.), 10., PANEL);
            }
            cube(c, x + 3., y + 9., 25.);
            c.ellipsis(
                &item.name,
                x + 48.,
                y + 3.,
                (w - 150.).max(50.),
                18,
                true,
                INK,
            );
            c.ellipsis(
                &format!("{}  ·  {}", item.version, label_loader(&item.loader)),
                x + 48.,
                y + 32.,
                (w - 150.).max(50.),
                12,
                false,
                MUTED,
            );
            if self.ui.button(
                c,
                2000 + i as u64,
                Rect::new(x + w - 92., y + 8., 92., 36.),
                if selected { "Selected" } else { "Select" },
                ButtonStyle::Quiet,
                enabled,
            ) {
                action = Some(Action::Select(item.id.clone()));
            }
        }
        let y = (h - 185.).max(312.);
        c.line((x, y - 18.), (x + w, y - 18.), 1., LINE);
        if let Some(item) = self
            .instances
            .iter()
            .find(|item| Some(&item.id) == self.selected.as_ref())
        {
            let offline_width = if w < 420. { 144. } else { 188. };
            let label = if self.offline {
                "✓ Single-player mode"
            } else {
                "Microsoft account"
            };
            if self.ui.button(
                c,
                110,
                Rect::new(x, y, offline_width, 34.),
                label,
                ButtonStyle::Quiet,
                enabled && !busy,
            ) {
                self.offline = !self.offline;
            }
            if self.offline {
                self.ui.launcher_input(
                    c,
                    111,
                    Rect::new(
                        x + offline_width + 8.,
                        y - 2.,
                        (w - offline_width - 8.).min(230.),
                        40.,
                    ),
                    &mut self.username,
                    "Player name",
                    false,
                    enabled && !busy,
                );
            } else {
                let account_label = self
                    .accounts
                    .iter()
                    .find(|account| Some(&account.id) == self.selected_account.as_ref())
                    .map(|account| format!("{} ↻", truncate_chars(&account.name, 18)))
                    .unwrap_or_else(|| "Choose account".into());
                if self.ui.button(
                    c,
                    114,
                    Rect::new(
                        x + offline_width + 8.,
                        y - 2.,
                        (w - offline_width - 8.).min(230.),
                        40.,
                    ),
                    &account_label,
                    ButtonStyle::Quiet,
                    enabled && !busy,
                ) {
                    if self.accounts.is_empty() {
                        action = Some(Action::Navigate(Page::Accounts));
                    } else {
                        let next = self
                            .accounts
                            .iter()
                            .position(|account| Some(&account.id) == self.selected_account.as_ref())
                            .map_or(0, |index| (index + 1) % self.accounts.len());
                        self.selected_account = Some(self.accounts[next].id.clone());
                    }
                }
            }
            let play_y = y + 54.;
            if self.ui.button(
                c,
                112,
                Rect::new(x, play_y, 150., 44.),
                if item.installed {
                    "Play Minecraft"
                } else {
                    "Install instance"
                },
                ButtonStyle::Secondary,
                enabled
                    && !busy
                    && (!item.installed
                        || (self.offline && valid_username(&self.username))
                        || (!self.offline && self.selected_account.is_some())),
            ) {
                action = Some(Action::Start(if item.installed {
                    Task::Launch {
                        id: item.id.clone(),
                        offline: self.offline.then(|| self.username.trim().to_owned()),
                        account: self.selected_account.clone(),
                    }
                } else {
                    Task::Install(item.id.clone())
                }));
            }
            if item.installed
                && self.ui.button(
                    c,
                    113,
                    Rect::new(x + 160., play_y, 110., 44.),
                    "Repair files",
                    ButtonStyle::Quiet,
                    enabled && !busy,
                )
            {
                action = Some(Action::Start(Task::Install(item.id.clone())));
            }
            if self.offline && !valid_username(&self.username) {
                c.ellipsis(
                    "Use 3–16 letters, numbers or underscores.",
                    x + offline_width + 8.,
                    y + 42.,
                    w - offline_width - 8.,
                    11,
                    false,
                    MUTED,
                );
            }
        }
        action
    }

    fn draw_discover(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        h: f32,
        enabled: bool,
    ) -> Option<Action> {
        let mut action = None;
        Self::heading(
            c,
            x,
            w,
            "Make it yours.",
            "Find mods on Modrinth and add them to your selected instance.",
        );
        let busy = self.busy();
        self.ui.launcher_input(
            c,
            300,
            Rect::new(x, 156., w - 104., 42.),
            &mut self.query,
            "Search mods",
            false,
            enabled && !busy,
        );
        if self.ui.button(
            c,
            301,
            Rect::new(x + w - 94., 156., 94., 42.),
            "Search",
            ButtonStyle::Secondary,
            enabled && !busy && !self.query.trim().is_empty(),
        ) {
            action = Some(Action::Start(Task::Search {
                query: self.query.trim().into(),
                version: self.search_version.clone(),
                loader: ["any", "fabric", "quilt", "forge", "neoforge"][self.search_loader].into(),
            }));
        }
        let filter_w = (w * 0.44).min(220.);
        self.ui.launcher_input(
            c,
            302,
            Rect::new(x, 211., filter_w, 40.),
            &mut self.search_version,
            "Game version (optional)",
            false,
            enabled && !busy,
        );
        if self.ui.button(
            c,
            303,
            Rect::new(
                x + filter_w + 12.,
                211.,
                (w - filter_w - 12.).min(160.),
                40.,
            ),
            ["Any loader", "Fabric", "Quilt", "Forge", "NeoForge"][self.search_loader],
            ButtonStyle::Quiet,
            enabled && !busy,
        ) {
            self.search_loader = (self.search_loader + 1) % 5;
        }
        let target = self
            .selected
            .as_deref()
            .unwrap_or("select an instance in Library first");
        c.ellipsis(
            &format!("Adding to: {target}"),
            x,
            273.,
            w,
            12,
            false,
            MUTED,
        );
        if self.mods.is_empty() {
            c.text(
                if self.searched {
                    "No matching mods."
                } else {
                    "A new idea for your next world?"
                },
                x,
                342.,
                23,
                true,
                INK,
            );
            c.ellipsis(
                if self.searched {
                    "Try another name, version or loader."
                } else {
                    "Start with a mod name or something you want to change."
                },
                x,
                383.,
                w,
                14,
                false,
                MUTED,
            );
        }
        let count = (((h - 400.).max(106.) / 106.).floor() as usize).max(1);
        self.scroll = self.scroll.min(self.mods.len().saturating_sub(count));
        for (i, item) in self.mods.iter().enumerate().skip(self.scroll).take(count) {
            let y = 320. + (i - self.scroll) as f32 * 106.;
            c.ellipsis(&item.title, x, y, w - 96., 20, true, INK);
            c.ellipsis(&item.description, x, y + 32., w - 96., 13, false, MUTED);
            c.text(
                &format!("{} downloads", compact_count(item.downloads)),
                x,
                y + 57.,
                11,
                false,
                DIM,
            );
            if self.ui.button(
                c,
                3000 + i as u64,
                Rect::new(x + w - 80., y + 14., 80., 36.),
                "Add",
                ButtonStyle::Secondary,
                enabled && !busy && self.selected.is_some(),
            ) {
                action = Some(Action::Start(Task::AddMod {
                    instance: self.selected.clone().unwrap_or_default(),
                    project: item.id.clone(),
                }));
            }
            c.line((x, y + 87.), (x + w, y + 87.), 1., LINE);
        }
        action
    }

    fn draw_packs(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        _h: f32,
        enabled: bool,
    ) -> Option<Action> {
        let mut action = None;
        let busy = self.busy();
        Self::heading(
            c,
            x,
            w,
            "A whole new world.",
            "Bring a Modrinth modpack with you, or share your own.",
        );
        if self.ui.button(
            c,
            350,
            Rect::new(x, 156., 130., 40.),
            if self.pack_export {
                "Import"
            } else {
                "✓ Import"
            },
            ButtonStyle::Secondary,
            enabled && !busy,
        ) {
            self.pack_export = false;
            self.pack_path.clear();
        }
        if self.ui.button(
            c,
            351,
            Rect::new(x + 142., 156., 130., 40.),
            if self.pack_export {
                "✓ Export"
            } else {
                "Export"
            },
            ButtonStyle::Secondary,
            enabled && !busy,
        ) {
            self.pack_export = true;
            self.pack_path.clear();
        }
        c.text(
            if self.pack_export {
                "Save modpack to"
            } else {
                "Modpack file"
            },
            x,
            231.,
            13,
            true,
            INK,
        );
        self.ui.launcher_input(
            c,
            352,
            Rect::new(x, 257., w, 42.),
            &mut self.pack_path,
            "Full path to a .mrpack file",
            false,
            enabled && !busy,
        );
        if self.pack_export {
            c.ellipsis(
                &format!(
                    "Selected instance: {}",
                    self.selected
                        .as_deref()
                        .unwrap_or("choose one in your Library")
                ),
                x,
                326.,
                w,
                14,
                false,
                INK,
            );
            c.ellipsis(
                "Existing destination files are preserved.",
                x,
                359.,
                w,
                12,
                false,
                MUTED,
            );
        } else {
            c.text(
                "New instance name",
                x,
                if _h < 560. { 313. } else { 323. },
                13,
                true,
                INK,
            );
            self.ui.launcher_input(
                c,
                353,
                Rect::new(
                    x,
                    if _h < 560. { 338. } else { 348. },
                    w.min(430.),
                    if _h < 560. { 36. } else { 42. },
                ),
                &mut self.pack_id,
                "my-modpack",
                false,
                enabled && !busy,
            );
        }
        let valid_path = std::path::Path::new(self.pack_path.trim()).is_absolute();
        let ready = valid_path
            && if self.pack_export {
                self.selected.is_some()
            } else {
                valid_id(self.pack_id.trim())
            };
        if self.ui.button(
            c,
            354,
            Rect::new(
                x,
                if _h < 560. { 389. } else { 411. },
                170.,
                if _h < 560. { 40. } else { 44. },
            ),
            if self.pack_export {
                "Export modpack"
            } else {
                "Import modpack"
            },
            ButtonStyle::Secondary,
            enabled && !busy && ready,
        ) {
            action = Some(Action::Start(if self.pack_export {
                Task::ExportPack {
                    path: self.pack_path.trim().into(),
                    id: self.selected.clone().unwrap_or_default(),
                }
            } else {
                Task::ImportPack {
                    path: self.pack_path.trim().into(),
                    id: self.pack_id.trim().into(),
                }
            }));
        }
        action
    }

    fn draw_accounts(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        h: f32,
        enabled: bool,
    ) -> Option<Action> {
        let mut action = None;
        let busy = self.busy();
        Self::heading(
            c,
            x,
            w,
            "Your identity.",
            "Connect Microsoft to use your Minecraft account.",
        );
        let visible = self.accounts.len().min(if h > 900. {
            4
        } else if h > 640. {
            2
        } else if h > 570. {
            1
        } else {
            0
        });
        self.scroll = self
            .scroll
            .min(self.accounts.len().saturating_sub(visible.max(1)));
        for (i, account) in self
            .accounts
            .iter()
            .enumerate()
            .skip(self.scroll)
            .take(visible)
        {
            let y = 170. + (i - self.scroll) as f32 * 70.;
            c.rect(Rect::new(x, y, 42., 42.), 13., PANEL);
            let letter = account
                .name
                .chars()
                .next()
                .unwrap_or('M')
                .to_uppercase()
                .to_string();
            centered(c, &letter, x, y + 10., 42., 18, true, ACCENT);
            c.ellipsis(&account.name, x + 62., y, w - 165., 19, true, INK);
            if self.ui.button(
                c,
                4000 + i as u64,
                Rect::new(x + w - 90., y + 2., 90., 38.),
                if Some(&account.id) == self.selected_account.as_ref() {
                    "Selected"
                } else {
                    "Select"
                },
                ButtonStyle::Quiet,
                enabled && !busy,
            ) {
                self.selected_account = Some(account.id.clone());
            }
            c.ellipsis(&account.kind, x + 62., y + 29., w - 62., 12, false, MUTED);
        }
        let y = 180. + visible as f32 * 70.;
        if self.accounts.is_empty() {
            c.text("No account connected", x, y, 23, true, INK);
        }
        c.text("Microsoft application ID", x, y + 65., 13, true, INK);
        self.ui.launcher_input(
            c,
            400,
            Rect::new(x, y + 92., w.min(470.), 42.),
            &mut self.oauth_client,
            "Use the configured application, or enter its client ID",
            false,
            enabled && !busy,
        );
        if self.ui.button(
            c,
            401,
            Rect::new(x, y + 151., 185., 44.),
            "Sign in with Microsoft",
            ButtonStyle::Secondary,
            enabled && !busy,
        ) {
            action = Some(Action::Start(Task::Login(self.oauth_client.clone())));
        }
        if self.ui.button(
            c,
            402,
            Rect::new(x + 198., y + 151., 90., 44.),
            "Refresh",
            ButtonStyle::Quiet,
            enabled && !busy,
        ) {
            action = Some(Action::Start(Task::Accounts));
        }
        c.ellipsis(
            "Sign in in your browser. Your password stays with Microsoft.",
            x,
            y + 221.,
            w,
            13,
            false,
            MUTED,
        );
        if y + 274. < h - 66. {
            c.ellipsis(
                "For local play, choose Single-player mode in your Library.",
                x,
                y + 251.,
                w,
                13,
                false,
                MUTED,
            );
        }
        action
    }

    fn draw_system(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        h: f32,
        enabled: bool,
    ) -> Option<Action> {
        let mut action = None;
        let busy = self.busy();
        Self::heading(
            c,
            x,
            w,
            "Keep it simple.",
            "Check your setup and see what is stored on this device.",
        );
        if self.ui.button(
            c,
            500,
            Rect::new(x, 156., 140., 42.),
            "Check setup",
            ButtonStyle::Secondary,
            enabled && !busy,
        ) {
            action = Some(Action::Start(Task::Doctor));
        }
        if self.ui.button(
            c,
            501,
            Rect::new(x + 154., 156., 145., 42.),
            "Refresh storage",
            ButtonStyle::Quiet,
            enabled && !busy,
        ) {
            action = Some(Action::Start(Task::Storage));
        }
        let all: Vec<(&str, &str)> = self
            .storage
            .iter()
            .chain(self.diagnostics.iter())
            .map(|(a, b)| (a.as_str(), b.as_str()))
            .collect();
        let count = (((h - 310.).max(50.) / 52.).floor() as usize).max(1);
        self.scroll = self.scroll.min(all.len().saturating_sub(count));
        for (i, (key, value)) in all.iter().enumerate().skip(self.scroll).take(count) {
            let y = 235. + (i - self.scroll) as f32 * 52.;
            let half = (w * 0.38).min(200.);
            c.ellipsis(key, x, y, half - 12., 13, false, MUTED);
            c.ellipsis(value, x + half, y, w - half, 14, false, INK);
            c.line((x, y + 32.), (x + w, y + 32.), 1., LINE);
        }
        if all.is_empty() {
            c.ellipsis(
                "Run a check to see Java, network tools and storage details.",
                x,
                250.,
                w,
                14,
                false,
                MUTED,
            );
        }
        action
    }

    fn draw_status(
        &mut self,
        c: &mut Canvas<'_>,
        x: f32,
        w: f32,
        h: f32,
        action: &mut Option<Action>,
        enabled: bool,
    ) {
        c.rect(Rect::new(x - 8., h - 65., w + 16., 65.), 0., BG);
        let message = if let Some(job) = &self.job {
            job.task.label()
        } else if !self.notice.is_empty() {
            self.notice.clone()
        } else if self.loaded {
            format!(
                "{} instance{}  ·  Shared game files",
                self.instances.len(),
                if self.instances.len() == 1 { "" } else { "s" }
            )
        } else {
            "Opening your library".into()
        };
        c.ellipsis(
            &message,
            x,
            h - 42.,
            (w - 160.).max(100.),
            12,
            false,
            if self.error { ERROR } else { MUTED },
        );
        if self.job.is_some()
            && self.ui.button(
                c,
                600,
                Rect::new(x + w - 158., h - 53., 76., 34.),
                "Cancel",
                ButtonStyle::Quiet,
                enabled,
            )
        {
            *action = Some(Action::Cancel);
        }
        if self.ui.button(
            c,
            601,
            Rect::new(x + w - 80., h - 53., 80., 34.),
            "Activity",
            ButtonStyle::Quiet,
            enabled,
        ) {
            *action = Some(Action::Logs);
        }
    }

    fn draw_form(&mut self, c: &mut Canvas<'_>) -> Option<Action> {
        c.rect(Rect::new(0., 0., c.width, c.height), 0., 0x000000b8);
        let w = (c.width - 48.).min(530.);
        let h = (c.height - 32.).min(555.);
        let x = (c.width - w) / 2.;
        let y = (c.height - h) / 2.;
        c.rect(Rect::new(x, y, w, h), 18., PANEL);
        if w < 320. || h < 380. {
            c.ellipsis(
                "Enlarge the window to create an instance.",
                x + 20.,
                y + 30.,
                w - 40.,
                14,
                false,
                INK,
            );
            return self
                .ui
                .button(
                    c,
                    700,
                    Rect::new(x + 20., y + 85., (w - 40.).max(40.), 36.),
                    "Close",
                    ButtonStyle::Quiet,
                    true,
                )
                .then_some(Action::CloseForm);
        }
        let compact = h < 520.;
        c.ellipsis(
            "A new beginning.",
            x + 30.,
            y + 28.,
            w - 105.,
            if w < 430. { 23 } else { 29 },
            true,
            INK,
        );
        let busy = self.busy();
        let mut action = None;
        if self.ui.button(
            c,
            700,
            Rect::new(x + w - 68., y + 24., 40., 36.),
            "×",
            ButtonStyle::Quiet,
            true,
        ) {
            action = Some(Action::CloseForm);
        }
        let first = if compact { 76. } else { 90. };
        let version = if compact { 158. } else { 203. };
        let loader = if compact { 239. } else { 306. };
        c.text("Instance name", x + 30., y + first, 13, true, INK);
        self.ui.launcher_input(
            c,
            701,
            Rect::new(x + 30., y + first + 24., w - 60., 42.),
            &mut self.new_id,
            "my-world",
            false,
            !busy,
        );
        if !compact {
            c.ellipsis(
                "Letters, numbers, dashes and underscores. Your saves stay separate.",
                x + 30.,
                y + 166.,
                w - 60.,
                11,
                false,
                MUTED,
            );
        }
        c.text("Minecraft version", x + 30., y + version, 13, true, INK);
        self.ui.launcher_input(
            c,
            702,
            Rect::new(x + 30., y + version + 24., w - 190., 42.),
            &mut self.new_version,
            "Version number",
            false,
            !busy,
        );
        if self.ui.button(
            c,
            703,
            Rect::new(x + w - 148., y + version + 24., 118., 42.),
            "Latest release",
            ButtonStyle::Quiet,
            !busy,
        ) {
            if self.latest_release.is_empty() {
                action = Some(Action::Start(Task::Versions));
            } else {
                self.new_version = self.latest_release.clone();
            }
        }
        c.text("Mod loader", x + 30., y + loader, 13, true, INK);
        let loader_width = (w * 0.31).clamp(106., 150.);
        let loader_label = format!(
            "{} ↻",
            ["Vanilla", "Fabric", "Quilt", "Forge", "NeoForge"][self.new_loader]
        );
        if self.ui.button(
            c,
            704,
            Rect::new(x + 30., y + loader + 27., loader_width, 42.),
            &loader_label,
            ButtonStyle::Secondary,
            !busy,
        ) {
            self.new_loader = (self.new_loader + 1) % 5;
            self.new_loader_version.clear();
        }
        if self.new_loader > 0 {
            self.ui.launcher_input(
                c,
                705,
                Rect::new(
                    x + 42. + loader_width,
                    y + loader + 27.,
                    w - loader_width - 72.,
                    42.,
                ),
                &mut self.new_loader_version,
                if self.new_loader >= 3 {
                    "Loader version (required)"
                } else {
                    "Latest compatible version"
                },
                false,
                !busy,
            );
        }
        if self.error && !self.notice.is_empty() {
            c.ellipsis(
                &self.notice,
                x + 30.,
                y + h - 105.,
                w - 60.,
                12,
                false,
                ERROR,
            );
        } else if !compact {
            c.ellipsis(
                "Game assets and libraries are shared between instances.",
                x + 30.,
                y + 404.,
                w - 60.,
                12,
                false,
                MUTED,
            );
        }
        let can_create = valid_id(self.new_id.trim())
            && !self.new_version.trim().is_empty()
            && (self.new_loader < 3 || !self.new_loader_version.trim().is_empty());
        if self.ui.button(
            c,
            710,
            Rect::new(
                x + 30.,
                y + h - if compact { 62. } else { 74. },
                w - 60.,
                44.,
            ),
            if busy {
                "Please wait…"
            } else {
                "Create instance"
            },
            ButtonStyle::Secondary,
            !busy && can_create,
        ) {
            action = Some(Action::Start(Task::Create {
                id: self.new_id.trim().into(),
                version: self.new_version.trim().into(),
                loader: ["vanilla", "fabric", "quilt", "forge", "neoforge"][self.new_loader].into(),
                loader_version: self.new_loader_version.trim().into(),
            }));
        }
        action
    }

    fn draw_logs(&mut self, c: &mut Canvas<'_>) -> Option<Action> {
        c.rect(Rect::new(0., 0., c.width, c.height), 0., 0x000000b8);
        let w = (c.width - 48.).min(850.);
        let h = (c.height - 48.).min(610.);
        let x = (c.width - w) / 2.;
        let y = (c.height - h) / 2.;
        c.rect(Rect::new(x, y, w, h), 16., PANEL);
        c.text("Activity", x + 28., y + 25., 27, true, INK);
        let mut action = None;
        if self.ui.button(
            c,
            800,
            Rect::new(x + w - 70., y + 20., 42., 38.),
            "×",
            ButtonStyle::Quiet,
            true,
        ) {
            action = Some(Action::Logs);
        }
        let status = self
            .job
            .as_ref()
            .map(|job| {
                format!(
                    "{} · {:.0}s",
                    job.task.label(),
                    job.started.elapsed().as_secs_f64()
                )
            })
            .unwrap_or_else(|| "Recent operations".into());
        c.ellipsis(&status, x + 28., y + 70., w - 56., 12, false, MUTED);
        let count = (((h - 164.).max(24.) / 24.).floor() as usize).max(1);
        self.log_scroll = self.log_scroll.min(self.log.len().saturating_sub(count));
        let from = self.log.len().saturating_sub(count + self.log_scroll);
        c.clip(Rect::new(x + 25., y + 111., w - 50., h - 170.));
        for (i, line) in self.log.iter().skip(from).take(count).enumerate() {
            c.ellipsis(
                line,
                x + 28.,
                y + 115. + i as f32 * 24.,
                w - 56.,
                12,
                false,
                INK,
            );
        }
        if self.log.is_empty() {
            c.text("No recent activity.", x + 28., y + 123., 14, false, MUTED);
        }
        c.unclip();
        if self.job.is_some()
            && self.ui.button(
                c,
                801,
                Rect::new(x + 28., y + h - 58., 125., 36.),
                "Cancel operation",
                ButtonStyle::Quiet,
                true,
            )
        {
            action = Some(Action::Cancel);
        }
        c.ellipsis(
            "Activity is kept in memory for this session.",
            x + 168.,
            y + h - 46.,
            w - 196.,
            11,
            false,
            MUTED,
        );
        action
    }
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .as_bytes()
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(b))
}

fn valid_username(name: &str) -> bool {
    let name = name.trim();
    (3..=16).contains(&name.len())
        && name
            .as_bytes()
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || *b == b'_')
}

fn truncate_chars(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

fn label_loader(loader: &str) -> &str {
    match loader {
        "vanilla" => "Vanilla",
        "fabric" => "Fabric",
        "quilt" => "Quilt",
        "forge" => "Forge",
        "neoforge" => "NeoForge",
        _ => loader,
    }
}

fn compact_count(n: u64) -> String {
    if n >= 1_000_000 {
        format!("{:.1}M", n as f64 / 1_000_000.)
    } else if n >= 1000 {
        format!("{:.1}K", n as f64 / 1000.)
    } else {
        n.to_string()
    }
}

fn human_bytes(n: u64) -> String {
    let (unit, divisor) = if n >= 1 << 30 {
        ("GiB", 1u64 << 30)
    } else if n >= 1 << 20 {
        ("MiB", 1 << 20)
    } else if n >= 1 << 10 {
        ("KiB", 1 << 10)
    } else {
        return format!("{n} B");
    };
    format!("{:.1} {unit}", n as f64 / divisor as f64)
}

fn data_pairs(bytes: &[u8], storage: bool) -> Result<Vec<(String, String)>, String> {
    with_json(bytes, |root| {
        let mut values = Vec::new();
        flatten(root, "", storage, 0, &mut values);
        Ok(values)
    })
}

fn flatten(
    value: Value<'_, '_>,
    prefix: &str,
    storage: bool,
    depth: usize,
    output: &mut Vec<(String, String)>,
) {
    if depth > 3 || output.len() >= 80 {
        return;
    }
    if value.kind() == Kind::Object {
        for (name, item) in value.object() {
            let name = String::from_utf8_lossy(&name.bytes().collect::<Vec<_>>()).into_owned();
            if ["token", "secret", "password", "email", "key", "uuid"]
                .iter()
                .any(|term| name.to_ascii_lowercase().contains(term))
            {
                continue;
            }
            let key = if prefix.is_empty() {
                name.replace('_', " ")
            } else {
                format!("{prefix} · {}", name.replace('_', " "))
            };
            flatten(item, &key, storage, depth + 1, output);
        }
    } else if value.kind() == Kind::Array {
        for (index, item) in value.array().take(10).enumerate() {
            flatten(
                item,
                &format!("{prefix} {}", index + 1),
                storage,
                depth + 1,
                output,
            );
        }
    } else {
        let text = if let Some(number) = value.as_u64() {
            if storage && (prefix.contains("bytes") || prefix.contains("size")) {
                human_bytes(number)
            } else {
                number.to_string()
            }
        } else if let Some(string) = value.as_string() {
            String::from_utf8_lossy(&string.bytes().collect::<Vec<_>>()).into_owned()
        } else if let Some(boolean) = value.as_bool() {
            if boolean {
                "Available"
            } else {
                "Not available"
            }
            .into()
        } else {
            "—".into()
        };
        output.push((prefix.into(), text));
    }
}

#[allow(clippy::too_many_arguments)]
fn centered(
    c: &mut Canvas<'_>,
    text: &str,
    x: f32,
    y: f32,
    w: f32,
    size: i32,
    bold: bool,
    color: u32,
) {
    let width = c.measure(text, size, bold).min(w);
    c.ellipsis(text, x + (w - width) / 2., y, w, size, bold, color);
}

fn cube(c: &mut Canvas<'_>, x: f32, y: f32, s: f32) {
    let half = s * 0.5;
    let quarter = s * 0.25;
    c.polygon(
        &[
            (x + half, y),
            (x + s, y + quarter),
            (x + half, y + half),
            (x, y + quarter),
        ],
        ACCENT,
    );
    c.polygon(
        &[
            (x, y + quarter),
            (x + half, y + half),
            (x + half, y + s),
            (x, y + s - quarter),
        ],
        0x9b785aff,
    );
    c.polygon(
        &[
            (x + half, y + half),
            (x + s, y + quarter),
            (x + s, y + s - quarter),
            (x + half, y + s),
        ],
        0x6e5644ff,
    );
}

/// Open the native launcher. `smoke_seconds` exits automatically for visual/resource checks.
pub fn run(smoke_seconds: Option<u64>) -> Result<(), String> {
    run_with_root(smoke_seconds, None)
}

/// Forward an explicit storage root to CLI children without changing process environment.
pub fn run_with_root(smoke_seconds: Option<u64>, root: Option<String>) -> Result<(), String> {
    let started = Instant::now();
    let metrics = std::env::var("ZMCL_GUI_METRICS").as_deref() == Ok("1");
    let mut first_frame = true;
    let mut window = Window::new("zmcl — Minecraft Launcher", 1120, 760)?;
    window.set_theme(Theme::Dark);
    let mut app = App::new(root);
    let mut event = None;
    let mut redraw = true;
    loop {
        if smoke_seconds.is_some_and(|seconds| started.elapsed() >= Duration::from_secs(seconds)) {
            break;
        }
        if matches!(event, Some(Event::Close)) {
            break;
        }
        redraw |= app.poll();
        if let Some(Event::Move(x, y)) = event.as_ref()
            && !app.ui.pointer_changed(*x, *y)
        {
            event = None;
        }
        redraw |= event.is_some();
        if redraw {
            let had_event = event.is_some();
            let mut canvas = window.begin(BG);
            let action = app.draw(&mut canvas, event.take());
            drop(canvas);
            window.present();
            if first_frame {
                if metrics {
                    eprintln!(
                        "{{\"event\":\"gui_first_frame\",\"startup_ms\":{},\"renderer\":{}}}",
                        started.elapsed().as_millis(),
                        zmcl_core::json::quote(&window.gpu())
                    );
                }
                first_frame = false;
            }
            if let Some(text) = app.ui.clipboard.take() {
                window.copy(&text);
            }
            let had_action = action.is_some();
            if let Some(action) = action
                && !app.action(action)
            {
                break;
            }
            redraw = had_event || had_action;
            if redraw {
                continue;
            }
        }
        event = window.event(if app.busy() { 100 } else { 1000 });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn game_arguments_do_not_enter_a_shell() {
        let task = Task::Launch {
            id: "world one".into(),
            offline: Some("Player_1".into()),
            account: Some("unused-in-single-player".into()),
        };
        assert_eq!(
            task.args(),
            ["launch", "world one", "--offline", "Player_1"]
        );
    }

    #[test]
    fn multiple_accounts_require_a_visible_choice() {
        let mut app = App::new(None);
        let bytes = br#"{"accounts":[{"id":"first","name":"One"},{"id":"second","name":"Two"}]}"#;
        app.apply_result(&Task::Accounts, bytes).unwrap();
        assert_eq!(app.selected_account, None);
        app.selected_account = Some("second".into());
        app.apply_result(&Task::Accounts, bytes).unwrap();
        assert_eq!(app.selected_account.as_deref(), Some("second"));
        let task = Task::Launch {
            id: "world".into(),
            offline: None,
            account: app.selected_account,
        };
        assert_eq!(task.args(), ["launch", "world", "--account", "second"]);
    }

    #[test]
    fn forge_version_is_forwarded_without_shell_interpretation() {
        let task = Task::Create {
            id: "world".into(),
            version: "1.21.1".into(),
            loader: "forge".into(),
            loader_version: "52.0.1".into(),
        };
        assert_eq!(
            task.args(),
            [
                "instance",
                "create",
                "world",
                "--version",
                "1.21.1",
                "--loader",
                "forge",
                "--loader-version",
                "52.0.1"
            ]
        );
    }

    #[test]
    fn bounded_logs_and_valid_names() {
        let mut log = VecDeque::new();
        for _ in 0..200 {
            append_log(&mut log, &"a".repeat(2000));
        }
        assert_eq!(log.len(), LOG_LINES);
        assert!(log.iter().all(|line| line.len() <= 400));
        assert!(valid_id("my-world_2"));
        assert!(!valid_id("../world"));
        assert!(!valid_id(""));
        assert!(valid_username("Player_1"));
        assert!(!valid_username("bad name"));
    }

    #[test]
    fn data_views_do_not_display_credentials() {
        let rows = data_pairs(
            br#"{"bytes":1048576,"access_token":"secret","java":{"version":"21"}}"#,
            true,
        )
        .unwrap();
        assert!(rows.iter().any(|(_, value)| value == "1.0 MiB"));
        assert!(!format!("{rows:?}").contains("secret"));
    }
}
