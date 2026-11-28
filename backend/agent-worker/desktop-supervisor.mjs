/** The Cloud Computer's desktop: one X display served over a private VNC socket.
 *
 * Xvnc listens only on a Unix socket (no TCP); the root-run desktop-relay
 * bridges it to an authenticated provider stream. The session runs as the
 * workspace user. A failing desktop never stops the machine or its file
 * service; each process restarts with backoff.
 */
import { spawn } from 'node:child_process';
import { access, chown, mkdir, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export const DESKTOP_RUN = '/run/puppyone-desktop';
export const VNC_SOCKET = `${DESKTOP_RUN}/vnc.sock`;
const USER = {uid: 1000, gid: 1000};
const DISPLAY = ':1';

export function desktopEnabled(features = process.env.PUPPYONE_SANDBOX_FEATURES) {
  return (features || '').split(',').map(item => item.trim()).includes('desktop');
}

function environment() {
  return {
    PATH: '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    HOME: '/home/node', USER: 'node', LOGNAME: 'node', SHELL: '/bin/bash',
    LANG: 'C.UTF-8', DISPLAY, XDG_RUNTIME_DIR: `${DESKTOP_RUN}/xdg`,
    XDG_SESSION_TYPE: 'x11', XDG_CURRENT_DESKTOP: 'XFCE',
  };
}

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

function keep(name, command, args, {after} = {}) {
  let child, timer, failures = 0, stopping = false;
  const start = async () => {
    if (stopping) return;
    if (after && !(await after())) { timer = setTimeout(start, 250); return; }
    const started = Date.now();
    child = spawn(command, args, {...USER, env: environment(), cwd: '/home/node',
      stdio: ['ignore', 'ignore', 'inherit'], detached: true});
    child.once('error', error => console.error(`Desktop ${name} failed to start:`, error.message));
    child.once('exit', () => {
      if (stopping) return;
      failures = Date.now() - started > 30000 ? 0 : failures + 1;
      timer = setTimeout(start, Math.min(30000, 250 * 2 ** Math.min(failures, 7)));
    });
  };
  void start();
  return () => {
    stopping = true;
    clearTimeout(timer);
    // The session leads a process group: stop its applications with it.
    if (child?.pid) try { process.kill(-child.pid, 'SIGTERM'); } catch {}
  };
}

/** Start the desktop and return without waiting: boot readiness never depends on it. */
export async function superviseDesktop() {
  await rm(DESKTOP_RUN, {recursive: true, force: true});
  await mkdir(`${DESKTOP_RUN}/xdg`, {recursive: true, mode: 0o700});
  await chown(DESKTOP_RUN, USER.uid, USER.gid);
  await chown(`${DESKTOP_RUN}/xdg`, USER.uid, USER.gid);
  const stops = [
    keep('display', 'Xvnc', [DISPLAY, '-geometry', '1440x900', '-depth', '24',
      '-rfbport', '-1', '-rfbunixpath', VNC_SOCKET, '-rfbunixmode', '0600',
      '-SecurityTypes', 'None', '-AlwaysShared', '-AcceptSetDesktopSize',
      '-nolisten', 'tcp', '-desktop', 'PuppyOne']),
    keep('session', 'dbus-launch', ['--exit-with-session', 'startxfce4'],
      {after: () => exists(VNC_SOCKET)}),
  ];
  const stop = () => stops.forEach(item => item());
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
  return stop;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await superviseDesktop();
}
