const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const invoke = window.__TAURI__?.core?.invoke;
const titles = { overview: '概览', workspace: '工作区', settings: '配置与权限', logs: '启动日志' };
let phase = 'stopped';
let busy = false;
let openWhenReady = false;
let workspacesLoaded = false;
let polling = false;
let errorRevealed = false;
let startupFailed = false;

function showSettings() {
  document.body.classList.remove('booting');
  openWhenReady = false;
}
function showStartupError(error) {
  startupFailed = true;
  openWhenReady = false;
  document.body.classList.add('booting');
  $('#startup-title').textContent = '启动遇到问题';
  $('#startup-detail').textContent = String(error || '服务未能启动，请查看启动日志。');
  $('#startup-retry').hidden = false;
  $('#startup-logs').hidden = false;
}
window.addEventListener('desktop-settings', showSettings);
$('#startup-settings').addEventListener('click', showSettings);
$('#startup-retry').addEventListener('click', () => action(startApplication));
$('#startup-logs').addEventListener('click', () => { showSettings(); showView('logs'); });

function message(text) {
  $('#message').textContent = String(text);
  $('#message').hidden = !text;
}

async function command(name, args = {}) {
  if (!invoke) throw new Error('此页面需要从 DeepTutor Desktop 客户端打开。');
  return invoke(name, args);
}

async function action(callback) {
  if (busy) return;
  busy = true;
  message('');
  try { await callback(); } catch (error) {
    if (document.body.classList.contains('booting')) showStartupError(error);
    else message(error);
  }
  finally { busy = false; await refresh(); }
}

function showView(view) {
  $$('.view').forEach((element) => { element.hidden = element.id !== view; });
  $$('.nav').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  $('#breadcrumb').textContent = `个人空间 / ${titles[view]}`;
  if (view === 'logs') loadLogs().catch(message);
  if (view === 'workspace' && phase === 'ready') loadWorkspaces().catch(message);
}

function renderStatus(status) {
  phase = status.phase;
  const running = phase === 'ready';
  const starting = phase === 'starting';
  const labels = { ready: '正在运行', starting: '正在启动', stopped: '尚未启动', error: '启动遇到问题' };
  $('#home').textContent = status.home;
  $('#status').textContent = labels[phase] || phase;
  $('#rail-state').textContent = labels[phase] || phase;
  $('#rail-dot').classList.toggle('ready', running);
  $('#service-title').textContent = running ? '学习空间已就绪' : starting ? '正在准备你的学习空间' : '准备好，继续学习';
  $('#service-detail').textContent = status.message || (running ? '模型设置、工作区和学习工具已可以打开。' : starting ? '首次启动可能需要几分钟。你可以在启动日志查看进度。' : '启动后即可配置模型、整理资料，进入你的学习工作区。');
  if (!startupFailed && phase !== 'error') $('#startup-detail').textContent = starting ? '正在启动，随后进入学习界面…' : '正在打开学习界面…';
  $('#start').disabled = running || starting || busy;
  $('#stop').disabled = (!running && !starting) || busy;
  $('#choose-home').disabled = running || starting || busy;
  $('#add-workspace').disabled = !running || busy;
  $$('[data-section]').forEach((button) => { button.disabled = !running || busy; });
  if (!running) workspacesLoaded = false;
}

async function refresh() {
  if (polling || busy) return;
  polling = true;
  try {
    const status = await command('service_status');
    renderStatus(status);
    if (phase === 'ready') {
      if (openWhenReady) {
        openWhenReady = false;
        await command('open_app', { section: 'chat', workspaceId: null });
        showSettings();
      }
      if (!workspacesLoaded) { workspacesLoaded = true; await loadWorkspaces(); }
    } else if (phase === 'error' || phase === 'stopped') {
      openWhenReady = false;
      if (phase === 'error' && !errorRevealed) {
        errorRevealed = true;
        showStartupError(status.message);
        await command('show_settings', { startupError: true });
      }
    }
  } catch (error) {
    if (document.body.classList.contains('booting')) showStartupError(error);
    else message(error);
  }
  finally { polling = false; }
}

async function loadWorkspaces(add = false) {
  const response = await command('workspaces', { add });
  if (response.cancelled) return;
  const container = $('#workspaces');
  container.replaceChildren();
  const rows = response.workspaces.filter((row) => row.kind !== 'system' && !row.archived);
  for (const row of rows) {
    const item = document.createElement('div');
    item.className = 'workspace-row';
    const description = document.createElement('div');
    const name = document.createElement('strong');
    const path = document.createElement('small');
    name.textContent = row.display_name;
    path.textContent = row.path;
    description.append(name, path);
    const open = document.createElement('button');
    open.textContent = row.status === 'invalid' ? '目录不可用' : '进入工作区 ↗';
    open.disabled = row.status === 'invalid';
    open.addEventListener('click', () => action(() => command('open_app', { section: 'chat', workspaceId: row.workspace_id })));
    item.append(description, open);
    container.append(item);
  }
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '还没有工作区，可以选择一个资料文件夹。';
    container.append(empty);
  }
}

async function loadLogs() { $('#log-output').textContent = await command('read_logs'); }

$$('[data-view]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
$$('[data-section]').forEach((button) => button.addEventListener('click', () => action(() => command('open_app', { section: button.dataset.section, workspaceId: null }))));
async function startApplication() {
  startupFailed = false;
  $('#startup-title').textContent = 'DeepTutor';
  $('#startup-retry').hidden = true;
  $('#startup-logs').hidden = true;
  errorRevealed = false;
  openWhenReady = true;
  try { renderStatus(await command('start_service')); }
  catch (error) { openWhenReady = false; throw error; }
}
$('#start').addEventListener('click', () => action(startApplication));
$('#stop').addEventListener('click', () => action(async () => {
  openWhenReady = false;
  await command('stop_service');
}));
$('#choose-home').addEventListener('click', () => action(() => command('choose_home')));
$('#add-workspace').addEventListener('click', () => action(() => loadWorkspaces(true)));
$('#refresh-logs').addEventListener('click', () => action(loadLogs));
await action(startApplication);
setInterval(refresh, 1800);
