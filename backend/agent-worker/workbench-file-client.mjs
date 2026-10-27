/** SSH/docker exec bridge to the root-only service; disconnect never stops it. */
import { createConnection } from 'node:net';
if (process.getuid() !== 0) throw new Error('File service requires platform control');
const socket = createConnection('/run/puppyone-workbench/files.sock');
socket.on('connect', () => { process.stdin.pipe(socket); socket.pipe(process.stdout); });
socket.on('error', () => { process.exitCode = 1; process.stdin.destroy(); });
socket.on('close', () => { process.stdin.destroy(); });
process.stdin.on('error', () => socket.destroy());
process.stdout.on('error', () => socket.destroy());
