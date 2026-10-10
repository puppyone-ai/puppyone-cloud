/** Normal tool errors return to Pi; control-channel failures remain fatal. */
export function approvalDenied() {
  return { content: [{ type: 'text', text: 'The user declined this tool call. It was not executed. Continue with another approach; do not repeat the declined operation.' }],
    isError: true, details: { code: 'tool_approval_denied', recoverable: true } };
}

export function wrapTool(definition, { request, conversation, isStopped, onExecute }) {
  const execute = definition.execute;
  return { ...definition, execute: async (id, input, signal, update, context) => {
    if (isStopped()) throw new Error('Run stopped');
    onExecute?.(id);
    const admission = await request('tool_start', { call_id: id, name: definition.name, input, checkpoint: conversation() });
    if (admission.result) return admission.result;
    if (!admission.allow) return approvalDenied();
    let result;
    try { result = await execute(id, input, signal, update, context); }
    catch (error) { result = { content: [{ type: 'text', text: String(error?.message ?? error) }], isError: true,
      details: { code: error?.code === 'EACCES' || error?.code === 'EPERM' ? 'tool_permission_denied' : 'tool_execution_failed', recoverable: true } }; }
    // The supervisor links the receipt to a recovery point before ACK. A
    // failed write/bash may have partial effects, so it still needs recovery.
    await request('tool_end', { call_id: id, name: definition.name, input, result, mutated: !['read', 'ls', 'find', 'grep'].includes(definition.name) });
    return result;
  } };
}

/** SDK argument validation/unknown-tool errors bypass execute(). Record them
 * before the SDK appends the tool result and makes its next model request. */
export function toolErrorObserver({ request, conversation, executed }) {
  const calls = new Map();
  return async event => {
    if (event.type === 'tool_execution_start') {
      calls.set(event.toolCallId, { call_id: event.toolCallId, name: event.toolName, input: event.args ?? {} });
    } else if (event.type === 'tool_execution_end') {
      const call = calls.get(event.toolCallId);
      calls.delete(event.toolCallId);
      if (executed.delete(event.toolCallId) || !call) return;
      const admission = await request('tool_start', { ...call, checkpoint: conversation(),
        error_result: { ...event.result, isError: true } });
      if (!admission.result) throw new Error('Missing durable SDK tool-error result');
      Object.assign(event.result, admission.result);
    }
  };
}
