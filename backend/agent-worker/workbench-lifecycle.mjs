/** Workspace creation channel: Git initialization only, no task/model execution. */
import { channel } from './protocol.mjs';
import { initializeWorkbench } from './workbench-initialize.mjs';
const [workbench, generation] = process.argv.slice(2);
if (process.argv.length !== 4 || process.getuid() !== 0) throw new Error('Invalid workspace lifecycle controller');
process.umask(0o002);
const control = channel();
let pending = Promise.resolve();
control.dispatch(frame => {
  if (frame.type !== 'control') throw new Error('Workspace lifecycle accepts control only');
  pending = pending.then(async () => {
    try {
      let result;
      if (frame.action === 'capabilities') result = { workbench_lifecycle: 1 };
      else if (frame.action === 'initialize')
        result = await initializeWorkbench({ workbench, generation }, frame.value, control.request);
      else throw new Error('Unsupported workspace lifecycle operation');
      control.send({ type: 'control_result', id: frame.id, result });
    } catch (error) { control.send({ type: 'control_result', id: frame.id, error: error.message }); }
  });
});
process.stdin.on('end', () => { control.close(); pending.finally(() => process.exit(0)); });
