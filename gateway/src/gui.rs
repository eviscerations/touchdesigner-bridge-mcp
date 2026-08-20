//! The GUI (egui/eframe) — the user's window into the running bridge. Themed for TouchDesigner:
//! a heavy charcoal/near-black base + a rust-orange accent, an "Armed" status pill, a left
//! section-nav rail, and a live audit log. It owns the app lifecycle: it spawns the MCP gateway +
//! executor client on a background Tokio runtime and streams every call into the audit log.
//!
//! CONTROLS:
//!   1. Working-directory field — the ONE path the tool may touch; editing it (Apply) updates the
//!      confinement root live for every future call (shared `WorkingDir` handle) and merge-writes it
//!      into ~/.touchdesigner-bridge-mcp/arm.json.
//!   2. Logging on/off toggle.
//!   3. Live verbose audit log — every call the AI makes, in real time.
//!   4. Auto-arm toggle — writes arm.json so the executor arms on the next TD session (Settings).
//!   5. Arm-bridge helper — the Textport command to arm the executor, with a Copy button (TD's
//!      arming is manual via the Textport; this replaces the Houdini "install package" button).

use crate::config::Config;
use crate::executor::Executor;
use crate::gateway::{self, AuditEvent, WorkingDir};
use anyhow::Result;
use egui::{Color32, RichText};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use std::time::{Duration, SystemTime};

// ── Palette (TouchDesigner dark) ─────────────────────────────────────────────
/// Near-black window background.
const BG: Color32 = Color32::from_rgb(20, 20, 22);
/// Slightly lighter panel / card.
const PANEL: Color32 = Color32::from_rgb(30, 31, 34);
/// Sunken field background (heavier black than the panel).
const SUNKEN: Color32 = Color32::from_rgb(14, 14, 16);
/// Rust-orange accent (replaces Houdini's SideFX orange everywhere).
const RUST: Color32 = Color32::from_rgb(232, 83, 30);
/// Light-gray body text.
const TEXT: Color32 = Color32::from_rgb(210, 210, 212);
/// Muted gray for secondary text.
const MUTED: Color32 = Color32::from_rgb(130, 130, 134);
/// Field / card border.
const BORDER: Color32 = Color32::from_rgb(48, 48, 52);

// ── Footer links ─────────────────────────────────────────────────────────────
const URL_GITHUB: &str = "https://github.com/eviscerations/touchdesigner-bridge-mcp";
const URL_HOWTO: &str = "https://github.com/eviscerations/touchdesigner-bridge-mcp#readme";
const URL_SECURITY: &str = "https://github.com/eviscerations/touchdesigner-bridge-mcp#security";
const URL_TROUBLE: &str = "https://github.com/eviscerations/touchdesigner-bridge-mcp#troubleshooting";

/// The Textport line the operator pastes into TouchDesigner to arm the executor. TD arming is manual
/// (no package-install step like Houdini), so the GUI hands the operator this line. The repo location
/// is derived AT RUNTIME from the running binary (the first ancestor dir that holds `arm.py`) — never a
/// hardcoded developer path in shipped code. The command injects `TDMCP_REPO` so arm.py can find
/// td_executor without knowing its own path (it is run via exec(), where `__file__` is undefined).
/// Computed once and cached.
fn arm_textport_cmd() -> &'static str {
    static CMD: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    CMD.get_or_init(|| {
        let repo = std::env::current_exe().ok().and_then(|exe| {
            exe.ancestors()
                .find(|d| d.join("arm.py").is_file())
                .map(|d| d.display().to_string().replace('\\', "/"))
        });
        match repo {
            Some(dir) => format!(
                "import os; os.environ['TDMCP_REPO']=r'{dir}'; \
                 exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())"
            ),
            None => "import os; os.environ['TDMCP_REPO']=r'<path-to-touchdesigner-bridge-mcp>'; \
                     exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())"
                .to_string(),
        }
    })
    .as_str()
}

/// Cap on the live audit log so a long session can't grow memory without bound.
const MAX_LOG: usize = 1000;
/// How often the background task re-checks the executor's health.
const HEALTH_INTERVAL: Duration = Duration::from_secs(2);

/// Which content pane the nav rail has selected.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Nav {
    Status,
    WorkingDir,
    AuditLog,
    Settings,
}

const NAVS: &[(Nav, &str)] = &[
    (Nav::Status, "Status"),
    (Nav::WorkingDir, "Working dir"),
    (Nav::AuditLog, "Audit log"),
    (Nav::Settings, "Settings"),
];

/// One rendered line in the live audit log (an `AuditEvent` stamped with a receipt time).
struct LogRow {
    endpoint: String,
    summary: String,
    ok: bool,
    ts: String,
}

struct App {
    config: Config,
    working_dir: WorkingDir, // shared with the running gateway
    working_dir_buf: String, // the editable text-field buffer
    dir_status: Option<String>, // feedback after an Apply
    nav: Nav,
    auto_arm: bool,
    min_interval_buf: String, // editable buffer for the min-action-interval throttle (ms)
    audit_rx: tokio::sync::mpsc::UnboundedReceiver<AuditEvent>,
    ui_tx: tokio::sync::mpsc::UnboundedSender<AuditEvent>, // GUI-originated lifecycle lines
    ui_rx: tokio::sync::mpsc::UnboundedReceiver<AuditEvent>,
    log_rx: tokio::sync::mpsc::UnboundedReceiver<AuditEvent>, // tailed from the headless gateway's log file
    audit_log: VecDeque<LogRow>,
    connected: Arc<AtomicBool>,
    td_build: Arc<RwLock<Option<String>>>,
    rt: tokio::runtime::Runtime, // kept alive so the spawned gateway/health tasks keep running
}

pub fn run(cfg: Config) -> Result<()> {
    // Publish any persisted throttle into the process env BEFORE the gateway starts, so the
    // in-process serve() (which reads TDMCP_MIN_ACTION_INTERVAL_MS at startup) picks it up.
    if let Some(ms) = read_min_interval() {
        std::env::set_var("TDMCP_MIN_ACTION_INTERVAL_MS", ms.to_string());
    }

    // Shared confinement root: the GUI edits it, the gateway reads it per call.
    let working_dir: WorkingDir = Arc::new(RwLock::new(cfg.working_dir.clone()));
    let connected = Arc::new(AtomicBool::new(false));
    let td_build: Arc<RwLock<Option<String>>> = Arc::new(RwLock::new(None));
    let (audit_tx, audit_rx) = tokio::sync::mpsc::unbounded_channel::<AuditEvent>();
    let (ui_tx, ui_rx) = tokio::sync::mpsc::unbounded_channel::<AuditEvent>();
    let (log_tx, log_rx) = tokio::sync::mpsc::unbounded_channel::<AuditEvent>();

    // STACK-OVERFLOW FIX: mvp_catalog() (500+ tools / 14k+ params) overflows the default worker stack
    // in debug when a client calls tools/list. The gateway is spawned as a task on THIS runtime, so
    // the worker threads must carry a 16 MB stack — matching the headless serve path in main.rs.
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .thread_stack_size(16 * 1024 * 1024)
        .build()?;

    // 1) The MCP stdio gateway — the AI's sole entry point. Owns stdin/stdout.
    {
        let exec = Executor::connect(&cfg);
        let wd = working_dir.clone();
        rt.spawn(async move {
            if let Err(e) = gateway::serve(wd, exec, audit_tx).await {
                tracing::error!("MCP gateway stopped: {e}");
            }
        });
    }

    // 2) A background health poll so the GUI can show connect state + the live TouchDesigner build
    //    without blocking the UI thread. Re-resolves the arm.json port/token EACH tick so the poll
    //    hits whatever port the executor armed on even if arm.json appears/changes after startup.
    {
        let base_cfg = cfg.clone();
        let connected = connected.clone();
        let tb = td_build.clone();
        rt.spawn(async move {
            loop {
                let exec = Executor::connect(&armed_config(&base_cfg));
                let (ok, ver) = exec.health().await;
                connected.store(ok, Ordering::Relaxed);
                if let Ok(mut g) = tb.write() {
                    *g = ver;
                }
                tokio::time::sleep(HEALTH_INTERVAL).await;
            }
        });
    }

    // 3) Tail the newest `touchdesigner-bridge-mcp_*.log` in the working dir so the GUI shows the AI's
    //    REAL activity. The AI drives a SEPARATE headless gateway process; its audit lines land in
    //    that process's log file, never this GUI's in-memory channel — so without this tail the panel
    //    stays empty while TD is being driven. Follows the live working-dir + newest file.
    spawn_log_tailer(working_dir.clone(), log_tx, &rt);

    let app = App {
        working_dir_buf: cfg.working_dir.display().to_string(),
        auto_arm: read_arm_enabled(),
        min_interval_buf: read_min_interval().map(|n| n.to_string()).unwrap_or_default(),
        config: cfg,
        working_dir,
        dir_status: None,
        nav: Nav::Status,
        audit_rx,
        ui_tx,
        ui_rx,
        log_rx,
        audit_log: VecDeque::new(),
        connected,
        td_build,
        rt,
    };

    // Window / taskbar icon: the bridge logo, baked into the binary. Fail-soft — a decode failure
    // just leaves the default icon.
    let mut viewport = egui::ViewportBuilder::default()
        .with_inner_size([880.0, 600.0])
        .with_min_inner_size([640.0, 460.0])
        .with_title("touchdesigner-bridge-mcp");
    if let Ok(icon) = eframe::icon_data::from_png_bytes(include_bytes!("../assets/logo.png")) {
        viewport = viewport.with_icon(std::sync::Arc::new(icon));
    }
    let native_options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };

    eframe::run_native(
        "touchdesigner-bridge-mcp",
        native_options,
        Box::new(|cc| {
            cc.egui_ctx.set_visuals(td_visuals());
            Ok(Box::new(app))
        }),
    )
    .map_err(|e| anyhow::anyhow!("GUI failed: {e}"))
}

/// Dark charcoal base with the rust-orange accent — the TouchDesigner feel. Applied EVERY frame in
/// `update` (see the white-box fix note there), not just at startup.
fn td_visuals() -> egui::Visuals {
    let mut v = egui::Visuals::dark();
    v.panel_fill = BG;
    v.window_fill = BG;
    v.extreme_bg_color = SUNKEN;
    v.faint_bg_color = PANEL;
    v.override_text_color = Some(TEXT);
    v.hyperlink_color = RUST;
    v.selection.bg_fill = RUST.linear_multiply(0.4);
    v.selection.stroke = egui::Stroke::new(1.0, RUST);
    v.widgets.inactive.bg_fill = PANEL;
    v.widgets.inactive.weak_bg_fill = PANEL;
    v.widgets.hovered.bg_stroke = egui::Stroke::new(1.0, RUST);
    v.widgets.active.bg_fill = Color32::from_rgb(40, 41, 45);
    v
}

impl App {
    /// Apply the working-dir text field to the shared confinement root + persist it. Also
    /// merge-writes `working_dir` into ~/.touchdesigner-bridge-mcp/arm.json — the single source of
    /// truth the gateways read — so the change reaches BOTH this GUI's gateway and the headless
    /// (AI-facing) gateway live, without clobbering the auto-arm `enabled` flag.
    fn apply_working_dir(&mut self) {
        let new = PathBuf::from(self.working_dir_buf.trim());
        if !new.is_dir() {
            self.dir_status = Some(format!("⚠ not a directory: {}", new.display()));
            return;
        }
        if let Ok(mut guard) = self.working_dir.write() {
            *guard = new.clone();
        }
        self.config.working_dir = new.clone();
        match self.config.save() {
            Ok(()) => self.dir_status = Some("✓ working directory updated".into()),
            Err(e) => self.dir_status = Some(format!("saved in memory, but config write failed: {e}")),
        }
        // Publish to arm.json so every running gateway picks up the new root on its next call.
        match merge_write_arm(&self.config, serde_json::json!({ "working_dir": fwd(&new) })) {
            Ok(path) => self.note(true, "working-dir", format!("arm.json · {}", fwd(&path))),
            Err(e) => self.note(false, "working-dir", format!("arm.json write failed · {e}")),
        }
    }

    /// Push an audit line into the live log (stamped with the receipt time).
    fn push_event(&mut self, ev: AuditEvent) {
        self.audit_log.push_back(LogRow {
            endpoint: ev.endpoint,
            summary: ev.summary,
            ok: ev.ok,
            ts: now_hms(),
        });
        while self.audit_log.len() > MAX_LOG {
            self.audit_log.pop_front();
        }
    }

    /// Record a GUI-originated status line directly (we're on the UI thread).
    fn note(&mut self, ok: bool, endpoint: &str, summary: String) {
        self.push_event(AuditEvent { endpoint: endpoint.to_string(), ok, summary });
    }

    /// Re-check connection now (one-shot health probe) and surface the result.
    fn spawn_recheck(&self) {
        let cfg = self.config.clone();
        let connected = self.connected.clone();
        let tb = self.td_build.clone();
        let tx = self.ui_tx.clone();
        self.rt.spawn(async move {
            let exec = Executor::connect(&armed_config(&cfg));
            let (ok, ver) = exec.health().await;
            connected.store(ok, Ordering::Relaxed);
            if let Ok(mut g) = tb.write() {
                *g = ver.clone();
            }
            let summary = if ok {
                format!("reachable · TD {}", ver.unwrap_or_else(|| "?".into()))
            } else {
                "not reachable".into()
            };
            let _ = tx.send(AuditEvent { endpoint: "health".into(), ok, summary });
        });
    }

    // ── Auto-arm — merge-write `enabled` into ~/.touchdesigner-bridge-mcp/arm.json ────────────────
    // MERGE, so toggling auto-arm never clobbers the working_dir/token/port the other controls wrote.
    fn write_arm(&mut self, enabled: bool) {
        match merge_write_arm(&self.config, serde_json::json!({ "enabled": enabled })) {
            Ok(path) => self.note(
                true,
                "auto-arm",
                format!("{} · {}", if enabled { "enabled" } else { "disabled" }, fwd(&path)),
            ),
            Err(e) => self.note(false, "auto-arm", format!("write failed · {e}")),
        }
    }

    /// Regenerate the session token: mint a new CSPRNG token, persist it to config + arm.json.
    fn regenerate_token(&mut self) {
        match crate::config::generate_token() {
            Ok(tok) => {
                self.config.token = tok.clone();
                let _ = self.config.save();
                match merge_write_arm(&self.config, serde_json::json!({ "token": tok })) {
                    Ok(path) => self.note(true, "token", format!("regenerated · {}", fwd(&path))),
                    Err(e) => self.note(false, "token", format!("arm.json write failed · {e}")),
                }
            }
            Err(e) => self.note(false, "token", format!("generate failed · {e}")),
        }
    }

    /// Apply the min-action-interval throttle: persist to arm.json + set the process env var so a
    /// future gateway launch (and the headless AI-facing gateway, once it re-reads) picks it up.
    fn apply_min_interval(&mut self) {
        let raw = self.min_interval_buf.trim();
        let ms: u64 = if raw.is_empty() { 0 } else {
            match raw.parse() {
                Ok(n) => n,
                Err(_) => {
                    self.note(false, "throttle", "not a whole number of milliseconds".into());
                    return;
                }
            }
        };
        std::env::set_var("TDMCP_MIN_ACTION_INTERVAL_MS", ms.to_string());
        match merge_write_arm(&self.config, serde_json::json!({ "min_action_interval_ms": ms })) {
            Ok(path) => self.note(
                true,
                "throttle",
                format!("{ms} ms · {} (applies to the gateway on next launch)", fwd(&path)),
            ),
            Err(e) => self.note(false, "throttle", format!("write failed · {e}")),
        }
    }

    // ── Reach arm.json — open the trust-root file / its folder in the OS ──────────────────────────
    fn open_arm_json(&mut self) {
        // Guarantee the file exists first (merge-write with no changes creates a valid default on a
        // first run), then hand it to the OS default editor so any key can be hand-edited.
        match merge_write_arm(&self.config, serde_json::json!({})) {
            Ok(path) => match open_in_os(&path, false) {
                Ok(_) => self.note(true, "arm.json", format!("opened · {}", fwd(&path))),
                Err(e) => self.note(false, "arm.json", format!("open failed · {e}")),
            },
            Err(e) => self.note(false, "arm.json", format!("prepare failed · {e}")),
        }
    }

    fn open_config_folder(&mut self) {
        match crate::config::arm_json_path().and_then(|p| p.parent().map(Path::to_path_buf)) {
            Some(dir) => {
                let _ = std::fs::create_dir_all(&dir);
                match open_in_os(&dir, true) {
                    Ok(_) => self.note(true, "config-dir", format!("opened · {}", fwd(&dir))),
                    Err(e) => self.note(false, "config-dir", format!("open failed · {e}")),
                }
            }
            None => self.note(false, "config-dir", "no home directory".into()),
        }
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // WHITE-BOX FIX: re-apply the dark visuals EVERY frame. eframe follows the OS theme and can
        // stomp the visuals set once in the creation closure with a light theme on the first frame
        // (which rendered the window as a bland white box). Setting them here, in `update`, runs
        // after eframe's theme application each frame, so the dark TD theme always wins.
        ctx.set_visuals(td_visuals());

        // Drain new audit events into the ring buffer (MCP calls respect the logging toggle;
        // GUI-originated lifecycle lines always show).
        while let Ok(ev) = self.audit_rx.try_recv() {
            if self.config.logging_enabled {
                self.push_event(ev);
            }
        }
        while let Ok(ev) = self.ui_rx.try_recv() {
            self.push_event(ev);
        }
        // Real executor activity tailed from the headless gateway's log file — always shown.
        while let Ok(ev) = self.log_rx.try_recv() {
            self.push_event(ev);
        }

        let armed = self.connected.load(Ordering::Relaxed);
        let build = self.td_build.read().ok().and_then(|g| g.clone());

        // Intents collected from the (self-free) header, acted on after the panels close.
        let mut do_recheck = false;
        let mut want_settings = false;

        egui::TopBottomPanel::top("header")
            .frame(egui::Frame::none().fill(PANEL).inner_margin(egui::Margin::symmetric(12.0, 8.0)))
            .show(ctx, |ui| {
                header_ui(ui, armed, &build, &mut do_recheck, &mut want_settings);
            });

        egui::TopBottomPanel::bottom("footer")
            .frame(egui::Frame::none().fill(PANEL).inner_margin(egui::Margin::symmetric(12.0, 6.0)))
            .show(ctx, |ui| footer_ui(ui));

        egui::SidePanel::left("nav")
            .exact_width(150.0)
            .resizable(false)
            .frame(egui::Frame::none().fill(BG).inner_margin(egui::Margin::same(8.0)))
            .show(ctx, |ui| {
                ui.add_space(4.0);
                for (n, label) in NAVS {
                    if nav_item(ui, self.nav == *n, label) {
                        self.nav = *n;
                    }
                }
            });

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(BG).inner_margin(egui::Margin::same(16.0)))
            .show(ctx, |ui| match self.nav {
                Nav::Status => self.ui_status(ui, armed, &build),
                Nav::WorkingDir => self.ui_working_dir(ui),
                Nav::AuditLog => self.ui_audit(ui),
                Nav::Settings => self.ui_settings(ui),
            });

        if want_settings {
            self.nav = Nav::Settings;
        }
        if do_recheck {
            self.spawn_recheck();
        }

        // Keep repainting so the live log and connection state stay current without user input.
        ctx.request_repaint_after(Duration::from_millis(400));
    }
}

// ── Content panes ────────────────────────────────────────────────────────────
impl App {
    fn ui_status(&mut self, ui: &mut egui::Ui, armed: bool, build: &Option<String>) {
        section_label(ui, "SESSION");
        ui.add_space(8.0);

        // Connection + TouchDesigner build + the reachable executor endpoint.
        ui.horizontal(|ui| {
            let dot = if armed { Color32::from_rgb(70, 200, 90) } else { Color32::from_rgb(120, 120, 120) };
            ui.colored_label(dot, "●");
            ui.label(RichText::new(if armed { "Executor reachable" } else { "Executor not reachable" }).strong().color(TEXT));
        });
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            ui.label(RichText::new("TouchDesigner build").small().color(MUTED));
            boxed_field(ui, &build.clone().unwrap_or_else(|| "—".into()), 160.0);
        });
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            ui.label(RichText::new("Executor endpoint").small().color(MUTED));
            let port = crate::config::resolve_executor_port(self.config.executor_port);
            boxed_field(ui, &format!("http://{}:{}  (loopback)", self.config.executor_host, port), 260.0);
        });

        ui.add_space(14.0);
        ui.label(RichText::new("Working directory · the only path this tool may touch").strong().color(TEXT));
        ui.add_space(4.0);
        boxed_field(ui, &self.config.working_dir.display().to_string(), 440.0);

        ui.add_space(14.0);
        ui.horizontal(|ui| {
            let mut logging = self.config.logging_enabled;
            if toggle_switch(ui, &mut logging).changed() {
                self.config.logging_enabled = logging;
                let _ = self.config.save();
            }
            ui.add_space(6.0);
            ui.label(RichText::new("Logging").color(TEXT));
            ui.label(RichText::new("· record every call to the audit log").small().color(MUTED));
        });

        ui.add_space(14.0);
        ui.label(RichText::new("Live audit log").strong().color(TEXT));
        ui.add_space(4.0);
        render_audit(ui, &self.audit_log, Some(200.0));
    }

    fn ui_working_dir(&mut self, ui: &mut egui::Ui) {
        section_label(ui, "WORKING DIRECTORY");
        ui.add_space(8.0);
        ui.label(
            RichText::new(
                "Every file the tool reads or writes is confined under this one folder. Changing it \
                 takes effect immediately for all future calls.",
            )
            .color(MUTED),
        );
        ui.add_space(10.0);
        ui.add(
            egui::TextEdit::singleline(&mut self.working_dir_buf)
                .desired_width(520.0)
                .hint_text("C:\\path\\to\\your\\project"),
        );
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            if ui.button("Apply").clicked() {
                self.apply_working_dir();
            }
            if let Some(s) = &self.dir_status {
                ui.label(RichText::new(s).small().color(RUST));
            }
        });
    }

    fn ui_audit(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            section_label(ui, "AUDIT LOG");
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("Clear").clicked() {
                    self.audit_log.clear();
                }
                ui.label(RichText::new(format!("{} calls", self.audit_log.len())).small().color(MUTED));
            });
        });
        ui.add_space(6.0);
        if !self.config.logging_enabled {
            ui.label(RichText::new("logging paused").italics().color(MUTED));
        }
        render_audit(ui, &self.audit_log, None);
    }

    fn ui_settings(&mut self, ui: &mut egui::Ui) {
        egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
            section_label(ui, "SETTINGS");
            ui.add_space(10.0);

            // Show the arm.json-resolved values (the port/token the executor armed on), falling back
            // to Config when arm.json is absent/invalid.
            let port = crate::config::resolve_executor_port(self.config.executor_port);
            let token = crate::config::resolve_token(&self.config.token);

            ui.label(RichText::new("Executor port").strong().color(TEXT));
            boxed_field(ui, &port.to_string(), 120.0);
            ui.label(RichText::new("loopback port the in-TouchDesigner executor listens on").small().color(MUTED));

            ui.add_space(12.0);
            ui.label(RichText::new("Session token").strong().color(TEXT));
            ui.horizontal(|ui| {
                boxed_field(ui, &mask_token(&token), 260.0);
                if ui.button("Regenerate").clicked() {
                    self.regenerate_token();
                }
            });
            ui.label(RichText::new("shared secret for loopback calls — never leaves this machine").small().color(MUTED));

            ui.add_space(16.0);
            ui.horizontal(|ui| {
                let mut a = self.auto_arm;
                if toggle_switch(ui, &mut a).changed() {
                    self.auto_arm = a;
                    self.write_arm(a);
                }
                ui.add_space(6.0);
                ui.label(RichText::new("Auto-arm TouchDesigner").color(TEXT));
            });
            ui.label(
                RichText::new("writes ~/.touchdesigner-bridge-mcp/arm.json so the executor arms on the next TD session")
                    .small()
                    .color(MUTED),
            );

            ui.add_space(16.0);
            ui.label(RichText::new("Min action interval").strong().color(TEXT));
            ui.horizontal(|ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.min_interval_buf)
                        .desired_width(120.0)
                        .hint_text("0"),
                );
                ui.label(RichText::new("ms").color(MUTED));
                if ui.button("Apply").clicked() {
                    self.apply_min_interval();
                }
            });
            ui.label(
                RichText::new(
                    "throttle floor between destructive tool calls (0 = off). Persisted to arm.json; \
                     the gateway reads it at launch.",
                )
                .small()
                .color(MUTED),
            );

            ui.add_space(16.0);
            ui.label(RichText::new("Arm bridge (Textport)").strong().color(TEXT));
            ui.label(
                RichText::new(
                    "TouchDesigner arms the executor manually: paste this line into the TD Textport to \
                     start the loopback executor for this session.",
                )
                .small()
                .color(MUTED),
            );
            ui.add_space(4.0);
            boxed_field(ui, arm_textport_cmd(), 520.0);
            ui.add_space(4.0);
            if ui.button("Copy arm command").clicked() {
                ui.ctx().copy_text(arm_textport_cmd().to_string());
                self.note(true, "arm-cmd", "copied to clipboard".into());
            }

            ui.add_space(16.0);
            ui.horizontal(|ui| {
                if ui.button("Open arm.json").clicked() {
                    self.open_arm_json();
                }
                if ui.button("Open config folder").clicked() {
                    self.open_config_folder();
                }
            });
            ui.label(
                RichText::new(
                    "arm.json is the trust root (working dir, token, port, flags) at \
                     ~/.touchdesigner-bridge-mcp/arm.json — edit it by hand for anything not exposed here.",
                )
                .small()
                .color(MUTED),
            );
        });
    }
}

// ── Header / footer / nav (self-free helpers) ────────────────────────────────
fn header_ui(
    ui: &mut egui::Ui,
    armed: bool,
    build: &Option<String>,
    do_recheck: &mut bool,
    want_settings: &mut bool,
) {
    ui.horizontal(|ui| {
        // The "Armed" pill: rust-orange when armed, gray when not.
        let pill_fill = if armed { RUST } else { Color32::from_rgb(64, 64, 64) };
        let on_pill = if armed { Color32::from_rgb(20, 20, 22) } else { TEXT };
        egui::Frame::none()
            .fill(pill_fill)
            .rounding(egui::Rounding::same(9.0))
            .inner_margin(egui::Margin::symmetric(10.0, 4.0))
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.spacing_mut().item_spacing.y = 0.0;
                        let title = if armed { "Armed" } else { "Disarmed" };
                        ui.label(RichText::new(title).strong().color(on_pill));
                        let v = build
                            .clone()
                            .map(|s| format!("TD {s}"))
                            .unwrap_or_else(|| "— ".to_string());
                        let sub = if armed { Color32::from_rgb(70, 35, 15) } else { MUTED };
                        ui.label(RichText::new(v).small().color(sub));
                    });
                    ui.menu_button(RichText::new("▾").strong().color(on_pill), |ui| {
                        if ui.button("Re-check connection").clicked() {
                            *do_recheck = true;
                            ui.close_menu();
                        }
                        ui.separator();
                        let _ = ui.add_enabled(
                            false,
                            egui::Button::new(RichText::new("Executor · loopback only").small()),
                        );
                    });
                });
            });

        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if ui.button(RichText::new("⚙").size(16.0)).on_hover_text("Settings").clicked() {
                *want_settings = true;
            }
            ui.add_space(2.0);
            ui.label(RichText::new("executor · loopback").color(MUTED));
            let dot = if armed {
                Color32::from_rgb(70, 200, 90)
            } else {
                Color32::from_rgb(120, 120, 120)
            };
            ui.colored_label(dot, "●");
        });
    });
}

fn footer_ui(ui: &mut egui::Ui) {
    ui.columns(4, |c| {
        footer_link(&mut c[0], "HOW-TO", URL_HOWTO);
        footer_link(&mut c[1], "SECURITY", URL_SECURITY);
        footer_link(&mut c[2], "TROUBLESHOOTING", URL_TROUBLE);
        footer_link(&mut c[3], "GITHUB", URL_GITHUB);
    });
}

fn footer_link(ui: &mut egui::Ui, text: &str, url: &str) {
    ui.vertical_centered(|ui| {
        let resp = ui.add(
            egui::Label::new(RichText::new(text).color(RUST).strong().small())
                .sense(egui::Sense::click()),
        );
        if resp.hovered() {
            ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
        }
        if resp.clicked() {
            ui.ctx().open_url(egui::OpenUrl::new_tab(url));
        }
    });
}

/// A vertical nav row with a rust-orange left-border highlight when active. Returns `true` on click.
fn nav_item(ui: &mut egui::Ui, active: bool, label: &str) -> bool {
    let resp = ui.add_sized(
        [ui.available_width(), 30.0],
        egui::SelectableLabel::new(active, RichText::new(label).color(if active { RUST } else { TEXT })),
    );
    if active {
        let r = resp.rect;
        let bar = egui::Rect::from_min_size(r.left_top(), egui::vec2(3.0, r.height()));
        ui.painter().rect_filled(bar, egui::Rounding::ZERO, RUST);
    }
    resp.clicked()
}

// ── Small reusable widgets ───────────────────────────────────────────────────
/// A small caps-ish section label in muted gray.
fn section_label(ui: &mut egui::Ui, text: &str) {
    ui.label(RichText::new(text).small().strong().color(MUTED));
}

/// A read-only, boxed (sunken) monospace value field.
fn boxed_field(ui: &mut egui::Ui, text: &str, min_width: f32) {
    egui::Frame::none()
        .fill(SUNKEN)
        .rounding(egui::Rounding::same(4.0))
        .stroke(egui::Stroke::new(1.0, BORDER))
        .inner_margin(egui::Margin::symmetric(8.0, 5.0))
        .show(ui, |ui| {
            ui.set_min_width(min_width);
            ui.label(RichText::new(text).monospace().color(TEXT));
        });
}

/// A compact rust-orange on/off switch (self-contained — no extra deps). Returns the `Response`
/// (`.changed()` fires on toggle).
fn toggle_switch(ui: &mut egui::Ui, on: &mut bool) -> egui::Response {
    let desired = egui::vec2(34.0, 18.0);
    let (rect, mut resp) = ui.allocate_exact_size(desired, egui::Sense::click());
    if resp.clicked() {
        *on = !*on;
        resp.mark_changed();
    }
    let t = ui.ctx().animate_bool(resp.id, *on);
    let radius = 0.5 * rect.height();
    let bg = if *on { RUST } else { Color32::from_rgb(80, 80, 80) };
    ui.painter().rect_filled(rect, egui::Rounding::same(radius), bg);
    let cx = egui::lerp((rect.left() + radius)..=(rect.right() - radius), t);
    ui.painter()
        .circle_filled(egui::pos2(cx, rect.center().y), radius - 2.5, Color32::WHITE);
    resp
}

/// Render the audit log: a colored dot, the tool name, `→`, a short detail, and a right-aligned
/// timestamp per row. `max_height` bounds the mini-log on the Status pane.
fn render_audit(ui: &mut egui::Ui, rows: &VecDeque<LogRow>, max_height: Option<f32>) {
    let mut area = egui::ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true);
    if let Some(h) = max_height {
        area = area.max_height(h);
    }
    area.show(ui, |ui| {
        if rows.is_empty() {
            ui.add_space(8.0);
            ui.label(RichText::new("No calls yet. Connect your AI client and drive TouchDesigner.").color(MUTED));
            return;
        }
        for r in rows {
            ui.horizontal(|ui| {
                let dot = if r.ok {
                    Color32::from_rgb(70, 200, 90)
                } else {
                    Color32::from_rgb(220, 90, 80)
                };
                ui.colored_label(dot, "●");
                ui.label(RichText::new(&r.endpoint).monospace().strong().color(RUST));
                ui.label(RichText::new("→").color(MUTED));
                ui.label(RichText::new(&r.summary).monospace().color(TEXT));
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(RichText::new(&r.ts).small().color(MUTED));
                });
            });
        }
    });
}

// ── Free helpers ─────────────────────────────────────────────────────────────
/// A `Config` clone whose executor port + token are single-sourced from
/// `~/.touchdesigner-bridge-mcp/arm.json` (falling back to the stored Config when arm.json is
/// absent/invalid). Every GUI→executor client is built through this so the GUI always talks to the
/// port/token the executor armed on.
fn armed_config(cfg: &Config) -> Config {
    let mut c = cfg.clone();
    c.executor_port = crate::config::resolve_executor_port(cfg.executor_port);
    c.token = crate::config::resolve_token(&cfg.token);
    c
}

/// Path → forward-slash string (arm.json / audit lines want portable slashes).
fn fwd(p: &Path) -> String {
    p.display().to_string().replace('\\', "/")
}

/// Merge-write `~/.touchdesigner-bridge-mcp/arm.json`: load the existing object (if any), apply
/// `updates` over it, filling any still-missing keys from `cfg`, then write it back pretty-printed.
/// This is the ONE writer every caller shares — the auto-arm toggle passes `{enabled}`, Apply passes
/// `{working_dir}`, Regenerate passes `{token}` — and none clobbers the others' field. Returns the path.
fn merge_write_arm(cfg: &Config, updates: serde_json::Value) -> std::io::Result<PathBuf> {
    let path = crate::config::arm_json_path()
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "no home directory"))?;

    // Start from the existing file when present + valid, else an empty object.
    let mut obj = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();

    // Fill any key not already present (first-time creation) from the current config.
    obj.entry("enabled").or_insert(serde_json::json!(false));
    obj.entry("working_dir").or_insert(serde_json::json!(fwd(&cfg.working_dir)));
    obj.entry("token").or_insert(serde_json::json!(cfg.token));
    obj.entry("port").or_insert(serde_json::json!(cfg.executor_port));

    // Apply the explicit updates last, so they win over the fill-ins above.
    if let Some(u) = updates.as_object() {
        for (k, v) in u {
            obj.insert(k.clone(), v.clone());
        }
    }

    if let Some(p) = path.parent() {
        std::fs::create_dir_all(p)?;
    }
    let text = serde_json::to_string_pretty(&serde_json::Value::Object(obj)).unwrap_or_default();
    std::fs::write(&path, text)?;
    Ok(path)
}

/// Mask a token for display: first 4 + bullets + last 4.
fn mask_token(t: &str) -> String {
    if t.is_empty() {
        return "— (open / dev)".into();
    }
    if t.len() <= 8 {
        "•".repeat(t.len().max(4))
    } else {
        format!("{}{}{}", &t[..4], "•".repeat(t.len() - 8), &t[t.len() - 4..])
    }
}

/// Read the current auto-arm state from `~/.touchdesigner-bridge-mcp/arm.json` (default `false`).
fn read_arm_enabled() -> bool {
    crate::config::arm_json_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("enabled").and_then(|b| b.as_bool()))
        .unwrap_or(false)
}

/// Read the persisted min-action-interval (ms) from arm.json, if any.
fn read_min_interval() -> Option<u64> {
    crate::config::arm_json_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("min_action_interval_ms").and_then(|n| n.as_u64()))
}

/// Open a file (default handler) or a folder (file manager) with the OS. Fire-and-forget — we
/// `spawn` and never wait, so a launcher's own exit code (explorer.exe returns non-zero even on
/// success) is irrelevant. Windows-first; a POSIX fallback keeps non-Windows dev builds honest.
fn open_in_os(path: &Path, is_dir: bool) -> std::io::Result<()> {
    use std::process::Command;
    #[cfg(windows)]
    {
        if is_dir {
            Command::new("explorer").arg(path).spawn().map(|_| ())
        } else {
            Command::new("cmd").args(["/C", "start", ""]).arg(path).spawn().map(|_| ())
        }
    }
    #[cfg(not(windows))]
    {
        let _ = is_dir;
        Command::new("xdg-open").arg(path).spawn().map(|_| ())
    }
}

/// Tail the newest `touchdesigner-bridge-mcp_*.log` in the (live) working dir and forward its audit
/// + error lines as `AuditEvent`s. This is the fix for the empty-panel bug: the AI drives a SEPARATE
/// headless gateway process whose calls are written to that process's log file, not to this GUI's
/// in-memory audit channel. Re-resolves the working dir + newest file each tick, and reads only up to
/// the last complete line (a trailing partial line waits for the next tick).
fn spawn_log_tailer(
    working_dir: WorkingDir,
    tx: tokio::sync::mpsc::UnboundedSender<AuditEvent>,
    rt: &tokio::runtime::Runtime,
) {
    use std::io::{Read, Seek, SeekFrom};
    rt.spawn(async move {
        let mut cur: Option<PathBuf> = None;
        let mut offset: u64 = 0;
        loop {
            let dir = working_dir.read().ok().map(|g| g.clone()).unwrap_or_default();
            let newest = newest_log(&dir);
            if newest != cur {
                cur = newest;
                offset = 0; // a new file (headless relaunch / dir change) → read from its start
            }
            if let Some(path) = &cur {
                if let Ok(meta) = std::fs::metadata(path) {
                    let len = meta.len();
                    if len < offset {
                        offset = 0; // rotated / truncated
                    }
                    if len > offset {
                        if let Ok(mut f) = std::fs::File::open(path) {
                            if f.seek(SeekFrom::Start(offset)).is_ok() {
                                let mut bytes = Vec::new();
                                if f.take(len - offset).read_to_end(&mut bytes).is_ok() {
                                    if let Some(last_nl) = bytes.iter().rposition(|&b| b == b'\n') {
                                        let text = String::from_utf8_lossy(&bytes[..=last_nl]);
                                        for line in text.lines() {
                                            if let Some(ev) = parse_log_line(line) {
                                                let _ = tx.send(ev);
                                            }
                                        }
                                        offset += (last_nl + 1) as u64;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(600)).await;
        }
    });
}

/// The `touchdesigner-bridge-mcp_*.log` in `dir` with the most recent mtime (the actively-written
/// one), if any.
fn newest_log(dir: &Path) -> Option<PathBuf> {
    let mut best: Option<(SystemTime, PathBuf)> = None;
    for entry in std::fs::read_dir(dir).ok()?.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with("touchdesigner-bridge-mcp_") && name.ends_with(".log") {
            if let Ok(mt) = entry.metadata().and_then(|m| m.modified()) {
                if best.as_ref().map_or(true, |(bt, _)| mt > *bt) {
                    best = Some((mt, entry.path()));
                }
            }
        }
    }
    best.map(|(_, p)| p)
}

/// Parse one log line into an `AuditEvent`, or `None` to ignore it. Recognizes the headless gateway's
/// audit lines (`… audit: OK <endpoint> — <summary>` / `ERR …`) and surfaces gateway ERROR lines so
/// a crash isn't invisible in the window.
fn parse_log_line(line: &str) -> Option<AuditEvent> {
    if let Some(idx) = line.find("audit: ") {
        let rest = &line[idx + "audit: ".len()..];
        let (ok, tail) = if let Some(t) = rest.strip_prefix("OK ") {
            (true, t)
        } else if let Some(t) = rest.strip_prefix("ERR ") {
            (false, t)
        } else {
            (true, rest)
        };
        let (endpoint, summary) = match tail.split_once(" — ") {
            Some((e, s)) => (e.trim().to_string(), s.trim().to_string()),
            None => (tail.trim().to_string(), String::new()),
        };
        return Some(AuditEvent { endpoint, ok, summary });
    }
    if line.contains(" ERROR ") {
        let msg = line.rsplit_once(": ").map(|(_, m)| m).unwrap_or(line).trim();
        return Some(AuditEvent { endpoint: "error".into(), ok: false, summary: msg.to_string() });
    }
    None
}

/// A wall-clock HH:MM:SS stamp for audit rows (UTC; no chrono dependency).
fn now_hms() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let s = secs % 86_400;
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}
