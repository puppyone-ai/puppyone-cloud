/** Native process execution. It accesses the directory selected by the host. */
import { createBashToolDefinition } from '@earendil-works/pi-coding-agent';

export function localExecution(root) {
  return { tools: () => [createBashToolDefinition(root, { exposeSessionEnvironment: false })] };
}
