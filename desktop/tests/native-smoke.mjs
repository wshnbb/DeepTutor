// Run only on the disposable Windows CI machine, against the NSIS installer.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { access, mkdtemp, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright-core';

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const root = await mkdtemp(path.join(os.tmpdir(), 'dt-desktop-native-'));
const install = path.join(root, 'app');
const state = path.join(root, 'state');
const evidence = path.resolve(process.env.DESKTOP_EVIDENCE || path.join(root, 'evidence'));
await mkdir(evidence, { recursive: true });
const blockers = [];
for (const port of [8001, 3782]) {
  const server = net.createServer((socket) => socket.end());
  server.listen(port, '0.0.0.0');
  await once(server, 'listening');
  blockers.push(server);
}
const installers = (await readdir('src-tauri/target/release/bundle/nsis')).filter((name) => name.endsWith('.exe'));
assert.equal(installers.length, 1);
const installer = spawn(path.resolve('src-tauri/target/release/bundle/nsis', installers[0]), ['/S', `/D=${install}`], { windowsHide: true });
const [installCode] = await once(installer, 'exit');
assert.equal(installCode, 0, 'Installer failed');
const executables = (await readdir(install)).filter((name) => name.endsWith('.exe') && !name.toLowerCase().includes('uninstall'));
assert.equal(executables.length, 1, 'Expected one desktop executable');

const launch = (stateDirectory = state) => spawn(path.join(install, executables[0]), [], {
  env: { ...process.env, DEEPTUTOR_DESKTOP_STATE_DIR: stateDirectory, DEEPTUTOR_DESKTOP_TEST_CDP_PORT: '9223' },
  windowsHide: false, stdio: 'ignore',
});
let native = launch();
let settingsPage;
const windowSnapshot = () => settingsPage.evaluate(async () => Promise.all(
  (await window.__TAURI__.window.getAllWindows()).map(async (item) => ({
    label: item.label, title: await item.title(), visible: await item.isVisible(),
  })),
));
const control = (action, outputPath) => {
  const args = ['-NoProfile', '-File', 'tests/window-control.ps1', '-DesktopProcessId', String(native.pid), '-Action', action];
  if (outputPath) args.push('-OutputPath', outputPath);
  const output = execFileSync('pwsh.exe', args, { encoding: 'utf8', windowsHide: true });
  return ['Snapshot', 'Processes'].includes(action) ? JSON.parse(output) : undefined;
};
const desktopStatus = () => settingsPage.evaluate(() => window.__TAURI__.core.invoke('service_status'));
const assertChildrenStopped = async (processes) => {
  const deadline = Date.now() + 20000;
  let remaining = [];
  while (Date.now() < deadline) {
    remaining = JSON.parse(execFileSync('python', ['-c', 'import json,sys,psutil; print(json.dumps([p for p in json.loads(sys.argv[1]) if psutil.pid_exists(p["pid"])]))', JSON.stringify(processes)], { encoding: 'utf8', windowsHide: true }));
    if (!remaining.length) return;
    await wait(500);
  }
  assert.fail(`Owned desktop processes survived exit: ${JSON.stringify(remaining)}`);
};
const waitForWindow = async (label, visible) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if ((await windowSnapshot()).some((item) => item.label === label && item.visible === visible)) return;
    await wait(250);
  }
  assert.fail(`Window visibility mismatch: ${label}, ${visible}; process ${native.pid}; windows ${JSON.stringify(await windowSnapshot())}; native ${JSON.stringify(control('Snapshot'))}`);
};
const readEndpoints = async () => {
  const files = (await readdir(path.join(state, 'control'))).filter((name) => name.endsWith('.json'));
  assert.equal(files.length, 1);
  return JSON.parse(await readFile(path.join(state, 'control', files[0]), 'utf8'));
};
const assertStopped = async (endpoints) => {
  for (const key of ['frontend_url', 'backend_url']) {
    const port = Number(new URL(endpoints[key]).port);
    let closed = false;
    for (let attempt = 0; attempt < 30; attempt++) {
      const listening = await new Promise((resolve) => {
        const socket = net.connect({ host: '127.0.0.1', port });
        socket.once('connect', () => { socket.destroy(); resolve(true); });
        socket.once('error', () => resolve(false));
        socket.setTimeout(1000, () => { socket.destroy(); resolve(false); });
      });
      if (!listening) { closed = true; break; }
      await wait(500);
    }
    assert.ok(closed, `Owned server survived desktop termination: ${key}`);
  }
};
let browser;
const attach = async () => {
  let connectionError;
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try { return await chromium.connectOverCDP('http://127.0.0.1:9223', { timeout: 3000 }); }
    catch (error) { connectionError = error.message; if (native.exitCode !== null) throw new Error(`Desktop exited: ${native.exitCode}`); await wait(500); }
  }
  throw new Error(`Desktop WebView2 debugging endpoint did not start: ${connectionError}`);
};
try {
  browser = await attach();
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.waitForEvent('page', { timeout: 30000 });
  settingsPage = page;
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  // No Start click: opening the installed app must open the original UI itself.
  await page.locator('#status').filter({ hasText: '正在运行' }).waitFor({ timeout: 240000 });
  let tutor;
  for (let attempt = 0; attempt < 120; attempt++) {
    tutor = context.pages().find((item) => item !== page && /^http:\/\/127\.0\.0\.1:\d+\/chat/.test(item.url()));
    if (tutor) break;
    await wait(500);
  }
  assert.ok(tutor, 'Original application did not open automatically');
  assert.notEqual(new URL(tutor.url()).port, '3782', 'Desktop reused the occupied Web frontend');
  // The original chat chooses its greeting from the local time on mount.
  await tutor.getByRole('heading', { name: /What would you like to learn|Good morning|Good afternoon|Good evening|It's late|Morning —|Afternoon —|Evening —|Burning the midnight/ }).waitFor({ timeout: 60000 });
  await tutor.getByRole('link', { name: 'Partners', exact: true }).waitFor();
  await tutor.getByRole('link', { name: 'Task Board', exact: true }).waitFor();
  await tutor.screenshot({ path: path.join(evidence, 'native-original-chat.png') });
  await writeFile(path.join(evidence, 'native-windows.json'), JSON.stringify({ executable: path.join(install, executables[0]), pid: native.pid, windows: control('Snapshot'), tauri: await windowSnapshot() }, null, 2));
  control('CaptureTutor', path.join(evidence, 'native-application-window.png'));
  await waitForWindow('main', false);
  await waitForWindow('tutor', true);
  assert.equal((await desktopStatus()).tray_available, true, 'Native tray icon was not registered');
  console.log('PASS: startup automatically opens the original chat, sidebar and learning tools.');

  control('Settings');
  await waitForWindow('main', true);
  await page.getByRole('button', { name: '工作区 02', exact: true }).click();
  await page.locator('.workspace-row').first().waitFor({ timeout: 60000 });
  await page.screenshot({ path: path.join(evidence, 'native-workspaces.png') });
  control('CloseSettings');
  await waitForWindow('main', false);
  await waitForWindow('tutor', true);
  assert.equal(native.exitCode, null, 'Closing secondary settings quit the application');

  // Closing the learning window retains the exact WebView and its page, so
  // drafts and navigation survive restoration rather than being loaded anew.
  await tutor.evaluate(() => { window.__retainedDraft = 'unsent learning draft'; });
  const retainedURL = tutor.url();
  const originalEndpoints = await readEndpoints();
  control('CloseTutor');
  await waitForWindow('tutor', false);
  await waitForWindow('main', false);
  assert.equal(native.exitCode, null, 'Close should keep the desktop in its tray');
  assert.equal((await desktopStatus()).backgrounded, true);
  assert.equal((await fetch(originalEndpoints.backend_url)).ok, true, 'Backgrounding stopped the backend');
  control('Restore');
  await waitForWindow('tutor', true);
  assert.equal(tutor.url(), retainedURL);
  assert.equal(await tutor.evaluate(() => window.__retainedDraft), 'unsent learning draft');
  await page.evaluate(() => window.__TAURI__.core.invoke('open_app', { section: 'chat', workspaceId: null, automatic: true }));
  assert.equal(await tutor.evaluate(() => window.__retainedDraft), 'unsent learning draft', 'A late startup poll reloaded the restored page');

  control('Background');
  await waitForWindow('tutor', false);
  const duplicate = launch();
  const [duplicateCode] = await Promise.race([once(duplicate, 'exit'), wait(15000).then(() => { throw new Error('Second launch did not hand over to the existing instance'); })]);
  assert.equal(duplicateCode, 0);
  await waitForWindow('tutor', true);
  assert.deepEqual(await readEndpoints(), originalEndpoints, 'Second launch created another service instance');
  assert.equal(await tutor.evaluate(() => window.__retainedDraft), 'unsent learning draft');
  console.log('PASS: close-to-tray, restore without reload, and second-launch handover preserve the existing processes and page.');

  control('Settings');
  await waitForWindow('main', true);
  await page.getByRole('button', { name: '概览 01', exact: true }).click();
  await page.getByRole('button', { name: '停止服务', exact: true }).click();
  await page.locator('#status').filter({ hasText: '尚未启动' }).waitFor({ timeout: 40000 });
  console.log('PASS: native menu opens secondary settings; settings can close and stop services.');

  await page.getByRole('button', { name: '启动并打开应用 ↗', exact: true }).click();
  await page.locator('#status').filter({ hasText: '正在启动' }).waitFor({ timeout: 10000 });
  control('CloseSettings');
  await waitForWindow('main', false);
  await page.locator('#status').filter({ hasText: '正在运行' }).waitFor({ timeout: 240000 });
  const hiddenReadyDeadline = Date.now() + 15000;
  while (!(await windowSnapshot()).some((item) => item.label === 'tutor') && Date.now() < hiddenReadyDeadline) await wait(250);
  await waitForWindow('main', false);
  await waitForWindow('tutor', false);
  assert.equal((await desktopStatus()).backgrounded, true);
  control('Restore');
  await waitForWindow('tutor', true);
  console.log('PASS: closing while services start keeps the completed application in the background until restored.');
  let endpoints = await readEndpoints();
  assert.deepEqual(errors, [], 'Desktop page has JavaScript errors');
  const ownedProcesses = control('Processes');
  assert.ok(ownedProcesses.some((item) => /python/i.test(item.Name)));
  assert.ok(ownedProcesses.some((item) => /node/i.test(item.Name)));
  assert.ok(ownedProcesses.some((item) => /msedgewebview2/i.test(item.Name)));
  const exited = once(native, 'exit');
  control('Quit');
  const exitResult = await Promise.race([exited, wait(30000).then(() => { throw new Error('Explicit exit did not stop the desktop'); })]);
  assert.equal(exitResult[0], 0);
  await assertStopped(endpoints);
  await assertChildrenStopped(ownedProcesses);
  await writeFile(path.join(evidence, 'native-lifecycle.json'), JSON.stringify({ tray_registered: true, close_to_tray: true, restore_without_reload: true, single_instance: true, background_startup: true, explicit_exit: true, owned_processes_removed: ownedProcesses }, null, 2));
  console.log('PASS: explicit exit removes the desktop, Python, Node and WebView2 child processes and closes both ports.');

  await browser.close(); browser = undefined;
  native = launch();
  const restartedDeadline = Date.now() + 240000;
  while (Date.now() < restartedDeadline) {
    try { endpoints = await readEndpoints(); if ((await fetch(endpoints.frontend_url)).ok) break; }
    catch { /* Wait for this fresh process's readiness file. */ }
    await wait(500);
  }
  assert.equal((await fetch(endpoints.frontend_url)).ok, true, 'Fresh launch did not become ready');
  native.kill();
  await once(native, 'exit');
  await assertStopped(endpoints);
  console.log('PASS: Windows Job Object cleans up both servers when the desktop is terminated.');
  assert.ok(blockers.every((server) => server.listening), 'Desktop stopped an existing listener');
  console.log('PASS: installed desktop coexists with Web services occupying the default ports.');

  // An invalid data directory fails before a runtime child can be spawned.
  // Its error must remain visible across polling, with an explicit retry path.
  const invalidState = path.join(root, 'invalid-state');
  const invalidHome = path.join(root, 'not-a-directory');
  await mkdir(invalidState);
  await writeFile(invalidHome, 'This is a file, not a data directory.');
  await writeFile(path.join(invalidState, 'desktop.json'), JSON.stringify({ home: invalidHome }));
  native = launch(invalidState);
  browser = await attach();
  const errorContext = browser.contexts()[0];
  const errorPage = errorContext.pages()[0] || await errorContext.waitForEvent('page', { timeout: 30000 });
  settingsPage = errorPage;
  await errorPage.getByRole('heading', { name: '启动遇到问题', exact: true }).waitFor({ timeout: 30000 });
  await errorPage.getByRole('button', { name: '重新启动', exact: true }).waitFor();
  await errorPage.getByRole('button', { name: '查看启动日志', exact: true }).waitFor();
  const failureDetail = await errorPage.locator('#startup-detail').innerText();
  assert.ok(failureDetail.trim(), 'Startup failure has no explanation');
  await wait(3800);
  assert.equal(await errorPage.locator('#startup-detail').innerText(), failureDetail, 'Polling replaced the startup error');
  assert.equal(await errorPage.locator('#overview').isVisible(), false, 'Startup failure fell back to the old dashboard');
  await errorPage.screenshot({ path: path.join(evidence, 'native-startup-error.png') });
  control('CloseSettings');
  await waitForWindow('main', false);
  assert.equal(native.exitCode, null);
  assert.equal((await desktopStatus()).tray_available, true);
  control('Restore');
  await waitForWindow('main', true);
  assert.equal(await errorPage.locator('#startup-detail').innerText(), failureDetail);
  const invalidExited = once(native, 'exit');
  control('Quit');
  await Promise.race([invalidExited, wait(15000).then(() => { throw new Error('Startup error application did not exit'); })]);
  console.log('PASS: startup failures retain a stable error view, can be hidden/restored from the tray, and exit explicitly.');

  await browser.close().catch(() => {}); browser = undefined;
  const uninstall = spawn(path.join(install, 'uninstall.exe'), ['/S'], { windowsHide: true });
  const [uninstallCode] = await once(uninstall, 'exit');
  assert.equal(uninstallCode, 0, 'Uninstaller failed');
  const uninstallDeadline = Date.now() + 60000;
  let removed = false;
  while (Date.now() < uninstallDeadline) {
    try { await access(install); }
    catch (error) { if (error.code === 'ENOENT') { removed = true; break; } throw error; }
    await wait(500);
  }
  assert.ok(removed, 'Uninstaller left application files or runtime caches in the installation directory');
  await access(path.join(state, 'home', 'data', 'user', 'settings', 'system.json'));
  console.log('PASS: the actual uninstaller removes the application and keeps the separate runtime home.');
} catch (error) {
  try {
    const diagnostic = execFileSync('powershell.exe', ['-NoProfile', '-Command', "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'deeptutor|msedgewebview2' } | Select-Object Name,ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Depth 3"], { encoding: 'utf8', windowsHide: true });
    await writeFile(path.join(evidence, 'native-processes.json'), diagnostic);
  } catch { /* Preserve the original failure. */ }
  if (browser) {
    for (const [index, candidate] of browser.contexts()[0].pages().entries()) {
      await candidate.screenshot({ path: path.join(evidence, `failure-${index}.png`) }).catch(() => {});
      await writeFile(path.join(evidence, `failure-${index}.txt`), await candidate.locator('body').innerText().catch(() => 'No document'));
    }
  }
  try { console.error(await readFile(path.join(state, 'desktop.log'), 'utf8')); } catch { /* No launcher log yet. */ }
  throw error;
} finally {
  await browser?.close().catch(() => {});
  if (native.exitCode === null) native.kill();
  for (const server of blockers) server.close();
}
