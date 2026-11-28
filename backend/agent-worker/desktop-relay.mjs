/** Bridge stdin/stdout to the desktop's private VNC socket, byte for byte.
 *
 * Run by the provider over its authenticated transport (root, per viewer).
 * It waits briefly for a desktop that is still starting, then exits with the
 * connection; it never interprets or buffers the RFB protocol.
 */
import { access } from 'node:fs/promises';
import { connect } from 'node:net';
import { VNC_SOCKET } from './desktop-supervisor.mjs';

const deadline = Date.now() + 20000;
for (;;) {
  try { await access(VNC_SOCKET); break; } catch {
    if (Date.now() > deadline) {
      console.error('Desktop is not available');
      process.exit(3);
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}
const socket = connect(VNC_SOCKET);
socket.once('error', error => { console.error('Desktop connection failed:', error.message); process.exit(4); });
socket.once('connect', () => {
  process.stdin.pipe(socket);
  socket.pipe(process.stdout);
});
socket.once('close', () => process.exit(0));
process.stdin.once('end', () => socket.end());
