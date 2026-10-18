/** Native working-directory tools. Placement is chosen by the host, not the loop. */
import { createReadToolDefinition, createWriteToolDefinition, createEditToolDefinition,
  createLsToolDefinition, createFindToolDefinition, createGrepToolDefinition } from '@earendil-works/pi-coding-agent';

export function localFilesystem(root) {
  return {
    readTools: () => [createReadToolDefinition(root), createLsToolDefinition(root),
      createFindToolDefinition(root), createGrepToolDefinition(root)],
    writeTools: () => [createWriteToolDefinition(root), createEditToolDefinition(root)],
  };
}
