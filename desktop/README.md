# DeepTutor Desktop — Windows preview

This branch adds a Windows desktop client to the existing DeepTutor app.
The launcher uses Tauri 2/WebView2, a bundled Python interpreter, Node.js,
the backend/CLI wheel built from this commit, and its production Next.js assets.
It does not require a separate Python or Node.js installation on the user's PC.

## Download and use

Open **Actions → Desktop Windows → the latest successful run → Artifacts**.
Download `DeepTutor-Desktop-Windows-x64-*`, extract it, and run the NSIS
`*-setup.exe`. Artifacts expire after 14 days. A SHA-256 checksum and runtime
dependency manifest are included. Preview installers are not code-signed.
The installer runs for the current user and can download Microsoft's WebView2
bootstrapper if that runtime is missing; the first installation may need internet.

Start **DeepTutor Desktop**: services start automatically and the original
DeepTutor **/chat** interface opens as the primary window. Its existing sidebar,
learning features, model settings and workspace controls remain the application UI.
The brief startup screen gives way to that original interface without a Start click.

Use the native **桌面设置** menu (or **Ctrl+Shift+D**) to open the secondary desktop
settings window for application data location, service controls and a Windows
workspace folder picker. Closing settings returns to the original application.
Closing the original application exits the desktop and stops its owned services.
**停止服务** leaves settings open so you can change the data location and restart.
A launcher crash also stops the owned process tree via a Windows Job Object.
A graceful stop gives DeepTutor time to finish database writes.

The default private runtime home is under
`%LOCALAPPDATA%/io.github.wshnbb.deeptutor/home`. The launcher's small preferences,
control files, and `desktop.log` live alongside it. Selecting another home opens
another data set; it does not copy or migrate existing data. Use DeepTutor's
**数据迁移** UI for migration. Updates should use a newly built desktop installer;
the launcher disables background upstream version checks.

## Permissions and isolation

Both HTTP servers bind to IPv4 loopback. Native desktop commands are available
only to the bundled `main` launcher window; the tutoring WebView has no native
capabilities, and its navigation is restricted to its current local service origin.
Commands accept a fixed set of sections; they do not expose arbitrary shell
commands, URLs, or file reads. Workspace registration reuses the upstream service.
When multi-user authentication is enabled, native workspace operations refuse to
bypass login; manage those workspaces from the authenticated app instead.

This preview inherits upstream file/command approval behavior. It does **not**
add a Windows filesystem sandbox. Restricted-subprocess execution on Windows is
best effort; external MCP servers retain their own account/filesystem permissions.
Account settings, credentials and Memory remain shared across content workspaces.

## Branch and build

`main` remains the upstream-sync branch. Develop desktop changes on `desktop`,
merge upstream updates into it deliberately, then let **Desktop Windows** build
and verify another installer. No upstream release is published by this workflow.

On a Windows development machine with MSVC, Rust, Python 3.12, Node 22 and WebView2:

1. Install/build Web dependencies using the root project's documented process.
2. `python scripts/prepare_web_package.py --skip-build`
3. `python -m build --wheel --outdir desktop/dist .`
4. `python desktop/scripts/prepare_runtime.py --wheel <built-wheel>`
5. In `desktop`: `npm ci`, `npx tauri icon ui/icon.svg --output src-tauri/icons`,
   then `npm run dev` or `npm run build -- --bundles nsis`.

CI exercises the copied interpreter, real frontend/API readiness, Unicode workspace
registration, loopback listener addresses and graceful shutdown before bundling.
It also tests the native URL guards and the private Python control protocol.
After bundling, CI installs the NSIS package in its disposable Windows account,
verifies automatic startup into the original chat, exercises the actual native
settings menu, hides settings, stops/restarts services and closes the primary
window. It separately terminates a fresh desktop to verify Job Object cleanup. The
test uses an isolated `DEEPTUTOR_DESKTOP_STATE_DIR`; normal launches use the
standard per-user application data directory. Native screenshots are uploaded
as a separate test-evidence artifact.
CI additionally sets `DEEPTUTOR_DESKTOP_TEST_CDP_PORT` on its own child process;
the launcher applies those WebView2 arguments programmatically. No debugging port
is enabled during normal launches. Preview installers use zlib for quicker builds.
For a UI-only mock preview: `node desktop/tests/preview.mjs` (not a backend test).
