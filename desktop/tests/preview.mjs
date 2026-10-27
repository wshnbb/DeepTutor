// Local UI verification only; never bundled into the desktop application.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../ui');
const mock = `
let state = { phase: 'stopped', home: 'D:\\\\DeepTutor Data', message: '', frontend_url: null };
window.__TAURI__ = { core: { invoke: async (name, args = {}) => {
  if (name === 'service_status') return {...state};
  if (name === 'start_service') { state.phase = 'starting'; setTimeout(() => { state.phase = 'ready'; state.frontend_url = 'http://127.0.0.1:3782'; }, 500); return {...state}; }
  if (name === 'stop_service') { state.phase = 'stopped'; return; }
  if (name === 'workspaces') return { workspaces: [{ workspace_id: 'ws_preview', display_name: '电路与电子技术', path: 'D:\\\\学习资料\\\\电路', kind: 'workspace', archived: false, status: 'ready' }] };
  if (name === 'read_logs') return 'Preview: startup complete. Backend and frontend ready.';
  if (name === 'open_app') { window.__lastOpened = args; return; }
  if (name === 'choose_home') return null;
  throw new Error('Unexpected preview command: ' + name);
} } };
`;
http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (!['/', '/index.html', '/styles.css', '/app.js'].includes(pathname)) {
    response.writeHead(404).end(); return;
  }
  const filename = pathname === '/' ? 'index.html' : pathname.slice(1);
  let body = await readFile(path.join(root, filename), 'utf8');
  if (filename === 'app.js') body = mock + '\n' + body;
  response.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8');
  response.end(body);
}).listen(4178, '127.0.0.1', () => console.log('Desktop UI preview: http://127.0.0.1:4178'));
