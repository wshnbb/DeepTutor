// Run only on the disposable Windows CI machine, against the NSIS installer.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
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
const installers = (await readdir('src-tauri/target/release/bundle/nsis')).filter((name) => name.endsWith('.exe'));
assert.equal(installers.length, 1);
const installer = spawn(path.resolve('src-tauri/target/release/bundle/nsis', installers[0]), ['/S', `/D=${install}`], { windowsHide: true });
const [installCode] = await once(installer, 'exit');
assert.equal(installCode, 0, 'Installer failed');
const executables = (await readdir(install)).filter((name) => name.endsWith('.exe') && !name.toLowerCase().includes('uninstall'));
assert.equal(executables.length, 1, 'Expected one desktop executable');

const launch = () => spawn(path.join(install, executables[0]), [], {
  env: { ...process.env, DEEPTUTOR_DESKTOP_STATE_DIR: state, DEEPTUTOR_DESKTOP_TEST_CDP_PORT: '9223' },
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
  return action === 'Snapshot' ? JSON.parse(output) : undefined;
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
let connectionError;
try {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try { browser = await chromium.connectOverCDP('http://127.0.0.1:9223', { timeout: 3000 }); break; }
    catch (error) { connectionError = error.message; if (native.exitCode !== null) throw new Error(`Desktop exited: ${native.exitCode}`); await wait(500); }
  }
  assert.ok(browser, `Desktop WebView2 debugging endpoint did not start: ${connectionError}`);
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
  await tutor.getByRole('heading', { name: 'What would you like to learn?', exact: true }).waitFor({ timeout: 60000 });
  await tutor.getByRole('link', { name: 'Partners', exact: true }).waitFor();
  await tutor.getByRole('link', { name: 'Task Board', exact: true }).waitFor();
  await tutor.screenshot({ path: path.join(evidence, 'native-original-chat.png') });
  await writeFile(path.join(evidence, 'native-windows.json'), JSON.stringify({ executable: path.join(install, executables[0]), pid: native.pid, windows: control('Snapshot'), tauri: await windowSnapshot() }, null, 2));
  control('CaptureTutor', path.join(evidence, 'native-application-window.png'));
  await waitForWindow('main', false);
  await waitForWindow('tutor', true);
  console.log('PASS: startup automatically opens the original chat, sidebar and learning tools.');

  control('Settings');
  await waitForWindow('main', true);
  await page.getByRole('button', { name: '工作区 02', exact: true }).click();
  await page.locator('.workspace-row').first().waitFor({ timeout: 60000 });
  await page.screenshot({ path: path.join(evidence, 'native-workspaces.png') });
  control('CloseSettings');
  await waitForWindow('main', false);
  assert.equal(native.exitCode, null, 'Closing secondary settings quit the application');
  control('Settings');
  await waitForWindow('main', true);
  await page.getByRole('button', { name: '概览 01', exact: true }).click();
  await page.getByRole('button', { name: '停止服务', exact: true }).click();
  await page.locator('#status').filter({ hasText: '尚未启动' }).waitFor({ timeout: 40000 });
  console.log('PASS: native menu opens secondary settings; settings can close and stop services.');

  await page.getByRole('button', { name: '启动并打开应用 ↗', exact: true }).click();
  await page.locator('#status').filter({ hasText: '正在运行' }).waitFor({ timeout: 240000 });
  await waitForWindow('main', false);
  let endpoints = await readEndpoints();
  assert.deepEqual(errors, [], 'Desktop page has JavaScript errors');
  const exited = once(native, 'exit');
  control('CloseTutor');
  const exitResult = await Promise.race([exited, wait(30000).then(() => { throw new Error('Closing original UI did not exit the desktop'); })]);
  assert.equal(exitResult[0], 0);
  await assertStopped(endpoints);
  console.log('PASS: closing the original application exits cleanly and stops both services.');

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
}
