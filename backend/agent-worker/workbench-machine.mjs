/** Entry of a workspace container without a provider init (local Docker):
 * the file service, plus the desktop when the sandbox Profile ships one. */
import { superviseFiles } from './workbench-files-supervisor.mjs';
import { desktopEnabled, superviseDesktop } from './desktop-supervisor.mjs';

await superviseFiles();
if (desktopEnabled()) await superviseDesktop();
