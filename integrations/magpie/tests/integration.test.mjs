import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cp, mkdir, mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';

test('real Magpie connects, switches and restores DeepTutor without changing other services', async () => {
  const downloads = path.resolve(import.meta.dirname, '../../../../.downloads');
  await mkdir(downloads, { recursive: true });
  const root = await mkdtemp(path.join(downloads, 'deeptutor-magpie-test-'));
  const plugin = path.join(root, 'plugin');
  const catalogPath = path.join(root, 'data/user/settings/model_catalog.json');
  const executable = process.env.MAGPIE_EXE || 'E:\\edge\\magpie-windows-amd64.exe';
  const env = {
    ...process.env,
    XDG_CONFIG_HOME: path.join(root, 'config'),
    XDG_CACHE_HOME: path.join(root, 'cache'),
    MAGPIE_ADDR: '127.0.0.1:39425',
    MAGPIE_NO_ACCOUNTS: '1',
  };
  const run = (...args) => execFileSync(executable, args, { env, windowsHide: true, timeout: 45000, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  await mkdir(path.dirname(catalogPath), { recursive: true });
  await cp(path.resolve(import.meta.dirname, '..'), plugin, { recursive: true });
  await writeFile(path.join(plugin, 'binding.js'), `export const binding = ${JSON.stringify({ config: catalogPath, dir: root })};\n`);
  const original = {
    version: 1,
    connections: [{ id: 'mine', api_key: 'original-local-fixture' }],
    services: {
      llm: {
        active_profile_id: 'my-llm', active_model_id: 'my-model',
        profiles: [
          { id: 'my-llm', name: 'Original', binding: 'openai', api_key: 'mine', base_url: 'http://example.invalid/v1', models: [{ id: 'my-model', model: 'my-own-model' }, { id: 'other', model: 'keep-too' }] },
          { id: 'spare', models: [{ id: 'spare-model', model: 'keep-spare' }] },
        ],
      },
      task: { marker: 'keep-task' }, embedding: { marker: 'keep-embedding' },
    },
  };
  await writeFile(catalogPath, JSON.stringify(original, null, 2));
  const read = async () => JSON.parse(await readFile(catalogPath, 'utf8'));
  try {
    execFileSync('pwsh.exe', ['-NoProfile', '-File', path.join(plugin, 'install.ps1'), '-DataHome', root, '-BindOnly'], { env, windowsHide: true, timeout: 20000 });
    const prepared = await read();
    assert.deepEqual(prepared.services.llm.profiles.slice(1), original.services.llm.profiles);
    assert.equal(prepared.services.llm.active_profile_id, original.services.llm.active_profile_id);
    run('provider', 'add', 'Adapter Fixture', 'id=fixture', 'url=http://127.0.0.1:9/v1', 'key=fixture-only', 'models=first,second');
    run('plugin', 'add', plugin);
    assert.match(run('deeptutor'), /DeepTutor/);
    run('deeptutor', 'fixture/first');
    let saved = await read();
    assert.equal(saved.services.llm.profiles[0].models[0].model, 'fixture/first');
    assert.equal(saved.services.llm.profiles[0].api_key, 'magpie-deeptutor');
    assert.match(saved.services.llm.profiles[0].base_url, /127\.0\.0\.1:39425\/v1$/);
    assert.deepEqual(saved.services.llm.profiles.slice(1), original.services.llm.profiles);
    assert.deepEqual(saved.services.task, original.services.task);
    assert.deepEqual(saved.services.embedding, original.services.embedding);
    run('deeptutor', 'fixture/second');
    saved = await read();
    assert.equal(saved.services.llm.profiles[0].models[0].model, 'fixture/second');
    run('deeptutor', 'default');
    assert.deepEqual(await read(), prepared, 'Disconnect must restore the full original configuration');
    const fresh = { version: 1, connections: [], services: { llm: { active_profile_id: null, active_model_id: null, profiles: [] } } };
    await writeFile(catalogPath, JSON.stringify(fresh));
    execFileSync('pwsh.exe', ['-NoProfile', '-File', path.join(plugin, 'install.ps1'), '-DataHome', root, '-BindOnly'], { env, windowsHide: true, timeout: 20000 });
    const preparedFresh = await read();
    run('deeptutor', 'fixture/first');
    const freshSaved = await read();
    assert.ok(Array.isArray(freshSaved.services.llm.profiles));
    assert.ok(Array.isArray(freshSaved.services.llm.profiles[0].models));
    assert.equal(freshSaved.services.llm.profiles[0].models[0].model, 'fixture/first');
    run('deeptutor', 'default');
    assert.deepEqual(await read(), preparedFresh);
    console.log('PASS: actual Magpie lists DeepTutor, switches both models, and restores the original configuration.');
  } finally {
    // The resolved path was created within this task's workspace staging area.
    assert.ok(root.startsWith(downloads + path.sep));
    await rm(root, { recursive: true, force: true });
  }
});
