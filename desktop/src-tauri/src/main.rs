#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod job;

use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tauri::{Manager, State, Url, WebviewUrl, WebviewWindow};
use tauri::menu::{MenuBuilder, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

#[derive(Default, Deserialize, Serialize)]
struct Preferences { home: PathBuf }

#[derive(Deserialize)]
struct Ready { frontend_url: String, instance: String, pid: u32 }

#[derive(Serialize)]
struct Status {
    phase: &'static str,
    home: String,
    message: String,
    frontend_url: Option<String>,
    backgrounded: bool,
    tray_available: bool,
}

struct Session {
    child: Option<Child>,
    job: Option<job::Job>,
    instance: String,
    control: PathBuf,
    runtime: PathBuf,
    log: PathBuf,
    preferences_file: PathBuf,
    home: PathBuf,
    last_error: String,
}

struct Desktop(Mutex<Session>);

#[derive(Default)]
struct Lifecycle {
    backgrounded: AtomicBool,
    quitting: AtomicBool,
    opening: AtomicBool,
}

fn focus_window(window: &WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}

fn update_tray(app: &tauri::AppHandle, phase: &str) {
    let lifecycle = app.state::<Lifecycle>();
    let text = if lifecycle.quitting.load(Ordering::SeqCst) { "正在退出" }
        else { match phase { "ready" => "正在运行", "starting" => "正在启动", "error" => "启动失败", _ => "服务已停止" } };
    let background = if lifecycle.backgrounded.load(Ordering::SeqCst) { " · 后台" } else { "" };
    if let Some(tray) = app.tray_by_id("deeptutor") {
        let _ = tray.set_tooltip(Some(format!("DeepTutor · {text}{background}")));
    }
}

fn refresh_tray(app: &tauri::AppHandle) {
    let state = app.state::<Desktop>();
    if let Ok(mut session) = state.0.try_lock() {
        let phase = status(&mut session).phase;
        drop(session);
        update_tray(app, phase);
    };
}

fn background_application(app: &tauri::AppHandle) -> Result<(), String> {
    if app.tray_by_id("deeptutor").is_none() { return Err("托盘入口不可用，应用将退出。".into()); }
    let lifecycle = app.state::<Lifecycle>();
    lifecycle.backgrounded.store(true, Ordering::SeqCst);
    for window in app.webview_windows().values() { window.hide().map_err(|e| e.to_string())?; }
    refresh_tray(app);
    Ok(())
}

fn request_quit(app: &tauri::AppHandle) {
    let lifecycle = app.state::<Lifecycle>();
    if lifecycle.quitting.swap(true, Ordering::SeqCst) { return; }
    update_tray(app, "stopped");
    let app = app.clone();
    // Never block the Windows event loop while waiting for Python/Node to stop.
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<Desktop>();
        if let Ok(mut session) = state.0.lock() { let _ = stop(&mut session); }
        app.exit(0);
    });
}

fn restore_application(app: &tauri::AppHandle) {
    if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return; }
    app.state::<Lifecycle>().backgrounded.store(false, Ordering::SeqCst);
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let running = {
            let state = app.state::<Desktop>();
            let mut session = match state.0.lock() { Ok(value) => value, Err(_) => return };
            status(&mut session).phase == "ready"
        };
        if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return; }
        if running {
            if let Some(tutor) = app.get_webview_window("tutor") {
                let _ = focus_window(&tutor);
                if let Some(settings) = app.get_webview_window("main") { let _ = settings.hide(); }
            } else if open_tutor(&app, "chat", None, false).is_err() {
                if let Some(window) = app.get_webview_window("main") { let _ = focus_window(&window); }
            }
        } else if let Some(window) = app.get_webview_window("main") {
            // Keep the current startup/error view instead of replacing it with settings.
            let _ = focus_window(&window);
        }
        refresh_tray(&app);
    });
}

fn desktop_menu_action(app: &tauri::AppHandle, id: &str) {
    if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return; }
    match id {
        "desktop-open" => restore_application(app),
        "desktop-settings" => { let _ = reveal_settings(app); }
        "desktop-background" => { if background_application(app).is_err() { request_quit(app); } }
        "desktop-quit" => request_quit(app),
        _ => {}
    }
}

// Opt-in diagnostics for the disposable CI child process. Ordinary launches
// never open a debugging port. Programmatic options also work on elevated CI.
fn test_browser_args() -> Option<String> {
    std::env::var("DEEPTUTOR_DESKTOP_TEST_CDP_PORT").ok()
        .and_then(|value| value.parse::<u16>().ok()).filter(|port| *port > 0)
        .map(|port| format!("--remote-debugging-port={port} --disable-gpu"))
}

fn manager_only(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" { Ok(()) } else { Err("Desktop controls are only available in the launcher.".into()) }
}

fn reveal_settings(app: &tauri::AppHandle) -> Result<(), String> {
    if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return Ok(()); }
    app.state::<Lifecycle>().backgrounded.store(false, Ordering::SeqCst);
    let window = app.get_webview_window("main").ok_or("Missing desktop settings")?;
    // This event is evaluated only in the bundled, trusted settings document.
    window.eval("window.dispatchEvent(new Event('desktop-settings'))").map_err(|e| e.to_string())?;
    focus_window(&window)?;
    refresh_tray(app);
    Ok(())
}

#[tauri::command]
fn show_settings(window: WebviewWindow, app: tauri::AppHandle, startup_error: Option<bool>) -> Result<(), String> {
    manager_only(&window)?;
    if startup_error == Some(true) {
        if app.state::<Lifecycle>().backgrounded.load(Ordering::SeqCst)
            || app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return Ok(()); }
        focus_window(&window)
    } else { reveal_settings(&app) }
}

fn hidden(command: &mut Command) {
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    #[cfg(not(windows))] { let _ = command; }
}

fn runtime_command(session: &Session) -> Result<Command, String> {
    let python = session.runtime.join("python/python.exe");
    let node_dir = session.runtime.join("node");
    let runner = session.runtime.join("runner.py");
    if !python.is_file() || !node_dir.join("node.exe").is_file() || !runner.is_file() {
        return Err("安装包中的运行环境不完整，请重新安装完整的 Windows 桌面版。".into());
    }
    let mut command = Command::new(python);
    command.args(["-X", "utf8", "-u"]).arg(runner);
    let mut paths = vec![node_dir];
    paths.extend(std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()));
    command.env("PATH", std::env::join_paths(paths).map_err(|e| e.to_string())?);
    command.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8:replace");
    command.env("DEEPTUTOR_HOME", &session.home);
    command.env("DEEPTUTOR_BACKEND_READY_TIMEOUT", "180");
    command.env("DEEPTUTOR_FRONTEND_READY_TIMEOUT", "180");
    command.current_dir(&session.home);
    hidden(&mut command);
    Ok(command)
}

fn endpoint(url: &str) -> Option<Url> {
    let parsed = Url::parse(url).ok()?;
    (parsed.scheme() == "http" && parsed.host_str() == Some("127.0.0.1")
        && parsed.port().is_some() && parsed.username().is_empty()
        && parsed.password().is_none() && parsed.path() == "/"
        && parsed.query().is_none() && parsed.fragment().is_none()).then_some(parsed)
}

fn ready(session: &Session) -> Option<Ready> {
    let child = session.child.as_ref()?;
    let path = session.control.join(format!("{}.json", session.instance));
    let info: Ready = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    (info.instance == session.instance && info.pid == child.id()
        && endpoint(&info.frontend_url).is_some()).then_some(info)
}

fn status(session: &mut Session) -> Status {
    let ended = session.child.as_mut().and_then(|child| child.try_wait().ok().flatten());
    if let Some(code) = ended {
        session.child.take();
        session.job.take();
        clear_control(session);
        session.last_error = format!("服务已退出（{code}），请查看启动日志。");
    }
    let info = ready(session);
    Status {
        phase: if info.is_some() { "ready" } else if session.child.is_some() { "starting" }
            else if session.last_error.is_empty() { "stopped" } else { "error" },
        home: session.home.display().to_string(),
        message: session.last_error.clone(),
        frontend_url: info.map(|info| info.frontend_url),
        backgrounded: false,
        tray_available: false,
    }
}

fn clear_control(session: &Session) {
    if session.instance.is_empty() { return; }
    for extension in ["json", "stop", "gate"] {
        let _ = fs::remove_file(session.control.join(format!("{}.{}", session.instance, extension)));
    }
}

fn stop(session: &mut Session) -> Result<(), String> {
    if let Some(mut child) = session.child.take() {
        let result = fs::write(session.control.join(format!("{}.stop", session.instance)), b"stop");
        let deadline = Instant::now() + Duration::from_secs(12);
        while child.try_wait().map_err(|e| e.to_string())?.is_none() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
        }
        // Closing the job also stops descendants after a crash or stalled shutdown.
        session.job.take();
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            let _ = child.kill();
        }
        let _ = child.wait();
        // Ownership cleanup still completes when writing the stop marker failed.
        let _ = result;
    }
    clear_control(session);
    Ok(())
}

#[tauri::command]
fn service_status(window: WebviewWindow, state: State<Desktop>, app: tauri::AppHandle) -> Result<Status, String> {
    manager_only(&window)?;
    let mut session = state.inner().0.lock().map_err(|e| e.to_string())?;
    let mut result = status(&mut session);
    drop(session);
    result.backgrounded = app.state::<Lifecycle>().backgrounded.load(Ordering::SeqCst);
    result.tray_available = app.tray_by_id("deeptutor").is_some();
    update_tray(&app, result.phase);
    Ok(result)
}

fn start(session: &mut Session) -> Result<(), String> {
    // Reap an exited runner before deciding that a service is already running.
    let _ = status(session);
    if session.child.is_some() { return Ok(()); }
    fs::create_dir_all(&session.home).map_err(|e| e.to_string())?;
    fs::create_dir_all(&session.control).map_err(|e| e.to_string())?;
    session.instance = uuid::Uuid::new_v4().to_string();
    session.last_error.clear();
    let gate = session.control.join(format!("{}.gate", session.instance));
    let log = OpenOptions::new().create(true).truncate(true).write(true).open(&session.log).map_err(|e| e.to_string())?;
    let mut command = runtime_command(session)?;
    command.arg("serve").arg("--home").arg(&session.home)
        .arg("--control").arg(&session.control).arg("--instance").arg(&session.instance)
        .arg("--gate").arg(&gate).stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?).stderr(log);
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let job = match job::Job::attach(&child) {
        Ok(job) => job,
        Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); }
    };
    session.child = Some(child);
    session.job = Some(job);
    if let Err(error) = fs::write(gate, b"owned") {
        let _ = stop(session);
        return Err(error.to_string());
    }
    Ok(())
}

#[tauri::command]
fn start_service(window: WebviewWindow, state: State<Desktop>, app: tauri::AppHandle) -> Result<Status, String> {
    manager_only(&window)?;
    if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return Err("应用正在退出。".into()); }
    let mut session = state.inner().0.lock().map_err(|e| e.to_string())?;
    if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return Err("应用正在退出。".into()); }
    let result = start(&mut session);
    if let Err(error) = &result { session.last_error = error.clone(); }
    let mut current = status(&mut session);
    drop(session);
    current.backgrounded = app.state::<Lifecycle>().backgrounded.load(Ordering::SeqCst);
    current.tray_available = app.tray_by_id("deeptutor").is_some();
    update_tray(&app, current.phase);
    result.map(|_| current)
}

#[tauri::command]
async fn stop_service(window: WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    manager_only(&window)?;
    app.state::<Lifecycle>().backgrounded.store(false, Ordering::SeqCst);
    // Stopping services keeps the settings window available to the user.
    if let Some(window) = app.get_webview_window("tutor") { window.destroy().map_err(|e| e.to_string())?; }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<Desktop>();
        let mut session = state.inner().0.lock().map_err(|e| e.to_string())?;
        let result = stop(&mut session);
        session.last_error.clear();
        drop(session);
        update_tray(&app, "stopped");
        result
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn choose_home(window: WebviewWindow, app: tauri::AppHandle) -> Result<Option<String>, String> {
    manager_only(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<Desktop>();
        let mut session = state.inner().0.lock().map_err(|e| e.to_string())?;
        if session.child.is_some() { return Err("请先停止服务，再切换数据目录。".into()); }
        let Some(path) = rfd::FileDialog::new().set_title("选择 DeepTutor 数据目录（不会搬移旧资料）").pick_folder() else { return Ok(None); };
        if path.parent().is_none() { return Err("请选择文件夹，不要使用磁盘根目录。".into()); }
        let preferences = Preferences { home: path.clone() };
        let json = serde_json::to_vec_pretty(&preferences).map_err(|e| e.to_string())?;
        let temporary = session.preferences_file.with_extension("tmp");
        fs::write(&temporary, json).map_err(|e| e.to_string())?;
        fs::rename(temporary, &session.preferences_file).map_err(|e| e.to_string())?;
        session.home = path;
        session.last_error.clear();
        Ok(Some(session.home.display().to_string()))
    }).await.map_err(|e| e.to_string())?
}

fn workspace_output(session: &Session, folder: Option<&Path>) -> Result<serde_json::Value, String> {
    let mut command = runtime_command(session)?;
    command.arg(if folder.is_some() { "add-workspace" } else { "workspaces" })
        .arg("--home").arg(&session.home).stdin(Stdio::null());
    if let Some(path) = folder { command.arg("--folder").arg(path); }
    let output = command.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).chars().rev().take(1600).collect::<String>().chars().rev().collect());
    }
    serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())
}

#[tauri::command]
async fn workspaces(window: WebviewWindow, app: tauri::AppHandle, add: bool) -> Result<serde_json::Value, String> {
    manager_only(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let folder = if add { rfd::FileDialog::new().set_title("添加学习资料工作区").pick_folder() } else { None };
        if add && folder.is_none() { return Ok(serde_json::json!({"cancelled": true})); }
        let state = app.state::<Desktop>();
        let session = state.inner().0.lock().map_err(|e| e.to_string())?;
        if ready(&session).is_none() { return Err("请等待服务启动完成。".into()); }
        workspace_output(&session, folder.as_deref())
    }).await.map_err(|e| e.to_string())?
}

fn section_path(section: &str, workspace_id: Option<&str>) -> Result<String, String> {
    let path = match section {
        "chat" => "/chat", "providers" => "/settings/connections", "models" => "/settings/llm",
        "workspaces" => "/settings/workspace", "tools" => "/settings/tools", "status" => "/settings/status",
        _ => return Err("Unknown application section.".into()),
    };
    let mut result = path.to_string();
    if let Some(id) = workspace_id {
        if !id.starts_with("ws_") || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
            return Err("Invalid workspace id.".into());
        }
        result.push_str(&format!("?dt_workspace={id}"));
    }
    Ok(result)
}

#[tauri::command]
async fn open_app(window: WebviewWindow, app: tauri::AppHandle, section: String, workspace_id: Option<String>, automatic: Option<bool>) -> Result<(), String> {
    manager_only(&window)?;
    open_tutor(&app, &section, workspace_id.as_deref(), automatic.unwrap_or(false))
}

struct Opening<'a>(&'a AtomicBool);
impl Drop for Opening<'_> {
    fn drop(&mut self) { self.0.store(false, Ordering::SeqCst); }
}

fn open_tutor(app: &tauri::AppHandle, section: &str, workspace_id: Option<&str>, automatic: bool) -> Result<(), String> {
    let lifecycle = app.state::<Lifecycle>();
    if lifecycle.quitting.load(Ordering::SeqCst) { return Err("应用正在退出。".into()); }
    if !automatic { lifecycle.backgrounded.store(false, Ordering::SeqCst); }
    if lifecycle.opening.swap(true, Ordering::SeqCst) { return Ok(()); }
    let _opening = Opening(&lifecycle.opening);
    // Never retain the supervisor lock across window creation/COM callbacks.
    let (info, browser_data) = {
        let state = app.state::<Desktop>();
        let mut session = state.inner().0.lock().map_err(|e| e.to_string())?;
        if status(&mut session).phase != "ready" { return Err("服务还没有准备好。".into()); }
        (ready(&session).ok_or("Missing service endpoints")?, session.control.parent().ok_or("Missing state directory")?.join("webview"))
    };
    let base = endpoint(&info.frontend_url).ok_or("Invalid service origin")?;
    let url = base.join(&section_path(section, workspace_id)?).map_err(|e| e.to_string())?;
    if let Some(tutor) = app.get_webview_window("tutor") {
        // A tray restore can beat the hidden startup page's readiness poll.
        // Its later auto-open must not reload the page or erase an unsent draft.
        let same_origin = tutor.url().map(|current| current.origin() == url.origin()).unwrap_or(false);
        if !automatic || !same_origin { tutor.navigate(url).map_err(|e| e.to_string())?; }
    } else {
        let port = base.port();
        let mut builder = tauri::WebviewWindowBuilder::new(app, "tutor", WebviewUrl::External(url))
            .title("DeepTutor").visible(false).inner_size(1320.0, 900.0).min_inner_size(900.0, 640.0)
            .menu(app.menu().ok_or("Missing application menu")?)
            .data_directory(browser_data)
            .on_navigation(move |url| url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == port);
        if let Some(args) = test_browser_args() { builder = builder.additional_browser_args(&args); }
        builder.build().map_err(|e| e.to_string())?;
    }
    if lifecycle.quitting.load(Ordering::SeqCst) { return Ok(()); }
    // A close while the services were starting takes precedence over auto-open.
    if !lifecycle.backgrounded.load(Ordering::SeqCst) {
        if let Some(tutor) = app.get_webview_window("tutor") { focus_window(&tutor)?; }
        if let Some(settings) = app.get_webview_window("main") { settings.hide().map_err(|e| e.to_string())?; }
    }
    refresh_tray(app);
    Ok(())
}

#[tauri::command]
fn read_logs(window: WebviewWindow, state: State<Desktop>) -> Result<String, String> {
    manager_only(&window)?;
    let session = state.inner().0.lock().map_err(|e| e.to_string())?;
    if !session.log.exists() { return Ok("尚无启动日志。".into()); }
    let mut file = File::open(&session.log).map_err(|e| e.to_string())?;
    let length = file.metadata().map_err(|e| e.to_string())?.len();
    file.seek(SeekFrom::Start(length.saturating_sub(32768))).map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if app.try_state::<Lifecycle>().is_some() { restore_application(app); }
        }))
        .setup(|app| {
            let root = match std::env::var_os("DEEPTUTOR_DESKTOP_STATE_DIR") {
                Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
                Some(_) => return Err("DEEPTUTOR_DESKTOP_STATE_DIR must be an absolute path".into()),
                None => app.path().app_local_data_dir()?,
            };
            fs::create_dir_all(&root)?;
            let preferences_file = root.join("desktop.json");
            let home = if preferences_file.exists() {
                serde_json::from_slice::<Preferences>(&fs::read(&preferences_file)?)?.home
            } else { root.join("home") };
            app.manage(Desktop(Mutex::new(Session {
                child: None, job: None, instance: String::new(),
                control: root.join("control"), runtime: app.path().resource_dir()?.join("runtime"),
                log: root.join("desktop.log"), preferences_file, home, last_error: String::new(),
            })));
            app.manage(Lifecycle::default());
            let settings = MenuItem::with_id(app, "desktop-settings", "桌面设置", true, Some("Ctrl+Shift+D"))?;
            let menu = MenuBuilder::new(app).text("desktop-open", "显示学习界面").item(&settings)
                .text("desktop-background", "后台运行").text("desktop-quit", "退出 DeepTutor").build()?;
            app.set_menu(menu)?;
            let tray_menu = MenuBuilder::new(app).text("desktop-open", "打开 DeepTutor")
                .item(&settings).separator().text("desktop-quit", "退出 DeepTutor").build()?;
            TrayIconBuilder::with_id("deeptutor")
                .icon(app.default_window_icon().ok_or("Missing application icon")?.clone())
                .tooltip("DeepTutor · 正在启动").menu(&tray_menu).show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if matches!(event, TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. }) {
                        restore_application(tray.app_handle());
                    }
                }).build(app)?;
            let mut window = tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                .data_directory(root.join("webview"));
            if let Some(args) = test_browser_args() { window = window.additional_browser_args(&args); }
            window.build()?;
            Ok(())
        })
        .on_menu_event(|app, event| desktop_menu_action(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle();
                if app.state::<Lifecycle>().quitting.load(Ordering::SeqCst) { return; }
                let tutor_visible = app.get_webview_window("tutor").map(|tutor| tutor.is_visible().unwrap_or(false)).unwrap_or(false);
                if window.label() == "main" && tutor_visible {
                    let _ = window.hide();
                } else if background_application(app).is_err() { request_quit(app); }
            }
        })
        .invoke_handler(tauri::generate_handler![service_status, start_service, stop_service, choose_home, workspaces, open_app, read_logs, show_settings])
        .build(tauri::generate_context!()).expect("Could not initialize DeepTutor Desktop")
        .run(|app, event| {
            match event {
                tauri::RunEvent::ExitRequested { api, .. } => {
                    if let Some(lifecycle) = app.try_state::<Lifecycle>() {
                        if !lifecycle.quitting.load(Ordering::SeqCst) {
                            api.prevent_exit();
                            request_quit(app);
                        }
                    }
                }
                tauri::RunEvent::Exit => {
                    if let Some(state) = app.try_state::<Desktop>() {
                        if let Ok(mut session) = state.inner().0.lock() { let _ = stop(&mut session); };
                    }
                }
                _ => {}
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_owned_ipv4_loopback_origins_are_accepted() {
        assert!(endpoint("http://127.0.0.1:3782").is_some());
        for url in ["https://example.org", "http://evil.test:3782", "http://localhost:3782", "http://127.0.0.1:3782/?x=1"] {
            assert!(endpoint(url).is_none(), "accepted {url}");
        }
    }
    #[test]
    fn navigation_rejects_arbitrary_paths_and_query_injection() {
        assert_eq!(section_path("providers", None).unwrap(), "/settings/connections");
        assert!(section_path("https://example.org", None).is_err());
        assert!(section_path("chat", Some("ws_ok&evil=1")).is_err());
    }
}
