/** Compose independent capabilities without assuming their filesystem or process location. */
export function workspaceTools({ filesystem, execution, readonly }) {
  const definitions = [...filesystem.readTools()];
  if (!readonly) definitions.push(...filesystem.writeTools(), ...execution.tools());
  return definitions;
}
