/** Backend-only native file channel. No Git publication and no VM lifecycle. */
import { spawn } from 'node:child_process';
import { watch, lstatSync, readdirSync } from 'node:fs';
import { readFile, writeFile, rename, chmod } from 'node:fs/promises';
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { channel } from './protocol.mjs';
import { ROOT } from './git.mjs';
import { UUID, STATE, initializeControlState, requireWorkspace } from './workbench-state.mjs';
const SOCKET = '/run/puppyone-workbench/files.sock';
const MAX_FILE_FRAME = 16 * 1024 * 1024;
const serviceId = randomUUID();
const clients = new Set();
let currentBinding, server, queued = 0, queuedBytes = 0;
const bootId = (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
if (process.getuid() !== 0) throw new Error('File service requires platform control');
const watchers = new Map();
let watching = false, directoriesChanged = false, timer, pending = Promise.resolve();
const changedPaths = new Set();
let helper, active, buffer = '', failed = false, closing = false;
function watchFailed() {
  if (closing) return;
  broadcast({reset: true});
  process.exitCode = 1; close();
}
function changed(directory, event, name) {
  if (closing || name === '.git') return;
  directoriesChanged ||= event === 'rename';
  if (name && changedPaths.size < 256) changedPaths.add(join(directory, String(name)).slice(ROOT.length + 1));
  else changedPaths.add('');
  if (!timer) timer = setTimeout(() => {
    timer = undefined;
    try {
      if (directoriesChanged) { directoriesChanged = false; watchDirectories(); }
      const paths = [...changedPaths]; changedPaths.clear();
      broadcast(paths.includes('') ? {reset: true} : {paths, reset: false});
    } catch { watchFailed(); }
  }, 100);
}
function watchDirectories() {
  // Node 22 recursive fs.watch retains stale file-inode watches after an
  // atomic save on Linux. Watch directory entries instead: replacing a file
  // cannot detach its parent's watch. Reconcile directories only on rename,
  // never poll file bytes; .git and symlink targets are outside this channel.
  const seen = new Set();
  function visit(directory) {
    try {
      const info = lstatSync(directory);
      if (!info.isDirectory() || info.isSymbolicLink()) return;
      seen.add(directory);
      const previous = watchers.get(directory);
      if (!previous || previous.ino !== info.ino || previous.dev !== info.dev) {
        previous?.watcher.close();
        const watcher = watch(directory, (event, name) => changed(directory, event, name));
        watcher.on('error', watchFailed);
        watchers.set(directory, {watcher, ino: info.ino, dev: info.dev});
      }
      for (const entry of readdirSync(directory, {withFileTypes: true})) {
        if (entry.isDirectory() && entry.name !== '.git') visit(join(directory, entry.name));
      }
    } catch (error) {
      if (directory === ROOT || !['ENOENT', 'ENOTDIR'].includes(error.code)) throw error;
    }
  }
  visit(ROOT);
  for (const [directory, entry] of watchers) {
    if (!seen.has(directory)) { entry.watcher.close(); watchers.delete(directory); }
  }
}
function helperFailed() {
  failed = true;
  active?.reject(new Error('Workspace file operation unavailable'));
  active = undefined;
  helper?.kill('SIGKILL');
  if (!closing) { process.exitCode = 1; setImmediate(close); }
}
function files(value) {
  if (failed || closing) return Promise.reject(new Error('Workspace file channel unavailable'));
  if (!helper) {
    // One unprivileged helper per service; requests still reopen the confined
    // root and use native descriptors. No file bytes or revisions are cached.
    helper = spawn('python3', ['-u', '/opt/puppyone-agent/workbench-files.py', '--stream'], {
      uid: 1000, gid: 1000, cwd: ROOT, stdio: ['pipe', 'pipe', 'ignore'],
      env: {PATH: '/usr/local/bin:/usr/bin:/bin'},
    });
    helper.on('error', helperFailed);
    helper.on('exit', () => { if (!closing) helperFailed(); });
    helper.stdin.on('error', helperFailed);
    helper.stdout.setEncoding('utf8');
    helper.stdout.on('data', chunk => {
      buffer += chunk;
      if (Buffer.byteLength(buffer) > 16 * 1024 * 1024) return helperFailed();
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      if (!active || end !== buffer.length - 1) return helperFailed();
      try {
        const result = JSON.parse(buffer.slice(0, end)); buffer = '';
        const waiter = active; active = undefined; waiter.resolve(result);
      } catch { helperFailed(); }
    });
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(helperFailed, 20000);
    active = {
      resolve: value => { clearTimeout(timeout); resolve(value); },
      reject: error => { clearTimeout(timeout); reject(error); },
    };
    helper.stdin.write(JSON.stringify(value) + '\n');
  });
}
function releaseWatchers() {
  if ([...clients].some(client => client.watching)) return;
  for (const entry of watchers.values()) entry.watcher.close();
  watchers.clear(); clearTimeout(timer); timer = undefined;
  changedPaths.clear(); watching = false; directoriesChanged = false;
}

function broadcast(value) {
  for (const client of clients) {
    if (client.watching) client.send({type: 'workspace_changed', generation: client.binding.generation, ...value});
  }
}

async function admit(value, admittedClient) {
  if (!value || value.protocol !== 1 || ![value.workbench, value.generation].every(v => typeof v === 'string' && UUID.test(v))
    || !Number.isSafeInteger(value.compute_epoch) || value.compute_epoch < 0) throw new Error('Invalid file service binding');
  await requireWorkspace(value);
  // Persist the highest accepted compute epoch across service/backend restarts.
  // Only the authenticated platform transport can reach this socket.
  let saved;
  try { saved = JSON.parse(await readFile(`${STATE}/file-service.json`, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (saved && saved.workbench === value.workbench && saved.generation === value.generation && saved.compute_epoch > value.compute_epoch)
    throw new Error('Stale file service epoch');
  const binding = {workbench: value.workbench, generation: value.generation, compute_epoch: value.compute_epoch};
  if (JSON.stringify(saved) !== JSON.stringify(binding)) {
    await writeFile(`${STATE}/file-service.tmp`, JSON.stringify(binding), {mode: 0o600});
    await rename(`${STATE}/file-service.tmp`, `${STATE}/file-service.json`);
  }
  if (currentBinding && JSON.stringify(currentBinding) !== JSON.stringify(binding)) {
    for (const client of [...clients]) if (client !== admittedClient) client.close();
  }
  currentBinding = binding;
  return binding;
}

function attach(input, output, disconnect, initialBinding) {
  const control = channel(input, output, {maxFrame: MAX_FILE_FRAME, maxBuffered: 2 * MAX_FILE_FRAME, onError: disconnect});
  const client = {
    binding: initialBinding, watching: false, closed: false,
    send(value) { if (!client.closed) { try { control.send(value); } catch { client.close(); } } },
    close() { if (client.closed) return; client.closed = true; clients.delete(client); releaseWatchers(); control.close(); disconnect(); },
  };
  clients.add(client);
  input.on('end', () => client.close()); input.on('error', () => client.close());
  output.on('error', () => client.close()); input.on('close', () => client.close());
  control.dispatch(frame => {
    const size = Buffer.byteLength(JSON.stringify(frame));
    if (queued >= 64 || queuedBytes + size > 32 * 1024 * 1024) { client.close(); return; }
    queued++; queuedBytes += size;
    pending = pending.then(async () => {
      if (client.closed) return;
      try {
        if (frame.type !== 'control' || typeof frame.id !== 'string' || frame.id.length > 128) throw new Error('Invalid file request');
        let result;
        if (frame.action === 'hello') {
          client.binding = await admit(frame.value, client);
          result = {protocol: 1, ...client.binding, boot_id: bootId, service_id: serviceId, write_encodings: ['deflate']};
        } else {
          if (!client.binding) throw new Error('File service handshake required');
          await requireWorkspace(client.binding);
          if (currentBinding && JSON.stringify(client.binding) !== JSON.stringify(currentBinding)) throw new Error('Stale file service epoch');
          if (frame.action === 'files') result = await files(frame.value);
          else if (frame.action === 'watch') {
            if (!watching) { watchDirectories(); watching = true; }
            client.watching = true; result = {};
          } else if (frame.action === 'unwatch') { client.watching = false; releaseWatchers(); result = {}; }
          else throw new Error('Unknown file service operation');
        }
        client.send({type: 'control_result', id: frame.id, result});
      } catch (error) { client.send({type: 'control_result', id: frame.id, error: error.message}); }
    }).finally(() => { queued--; queuedBytes -= size; });
  });
}

function close() {
  if (closing) return;
  closing = true;
  for (const entry of watchers.values()) entry.watcher.close();
  watchers.clear(); clearTimeout(timer); helper?.kill('SIGKILL');
  active?.reject(new Error('Workspace file service closed')); active = undefined;
  for (const client of [...clients]) client.close();
  server?.close();
  pending.finally(() => process.exit(process.exitCode || 0));
}
process.on('SIGTERM', close); process.on('SIGINT', close);

if (process.argv[2] === '--serve') {
  await initializeControlState();
  // The boot supervisor owns this unique service. A second listener must not
  // unlink a live socket; stale sockets are removed by the supervisor only.
  server = createServer(socket => attach(socket, socket, () => socket.destroy()));
  server.on('error', error => { console.error(error.message); process.exitCode = 1; close(); });
  server.listen(SOCKET, async () => { await chmod(SOCKET, 0o600); process.send?.({type: 'ready'}); });
} else {
  // Explicit stdio harness for native regression tests; providers attach to
  // the supervised service rather than invoking this legacy test entry point.
  const [workbench, generation] = process.argv.slice(2);
  if (![workbench, generation].every(value => typeof value === 'string' && UUID.test(value))) throw new Error('Invalid file channel binding');
  await requireWorkspace({workbench, generation});
  attach(process.stdin, process.stdout, close, {workbench, generation});
}
