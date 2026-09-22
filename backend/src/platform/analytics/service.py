"""
Analytics Logging Service

Records all Agent activities in a unified log:
- bash: Bash command execution in sandbox
- tool: MCP tool calls
- llm: LLM API calls (Claude/GPT)

All logs are grouped by session_id for complete audit trail.
Designed to be lightweight and non-blocking.
"""

from typing import Any

from src.infra.supabase import get_supabase_client
from src.utils.logger import logger


def log_agent_call(
    call_type: str,  # 'bash', 'tool', 'llm'
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    success: bool = True,
    latency_ms: int | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Record an agent execution event.

    This is the unified logging function for all agent activities.
    Fire-and-forget - errors are logged but don't block the main flow.

    Args:
        call_type: 'bash', 'tool', or 'llm'
        details: Type-specific data:
            - bash: {"command": "...", "output_preview": "..."}
            - tool: {"tool_name": "...", "input": {...}, "output_preview": "..."}
            - llm:  {"model": "...", "input_tokens": N, "output_tokens": N}
    """
    try:
        supabase = get_supabase_client()

        supabase.table("agent_logs").insert({
            "call_type": call_type,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "success": success,
            "latency_ms": latency_ms,
            "error_message": error_message,
            "details": details or {},
        }).execute()

        # Brief log for debugging
        preview = ""
        if details:
            if call_type == "bash":
                cmd = details.get("command", "")[:30]
                preview = f"cmd={cmd}..."
            elif call_type == "tool":
                preview = f"tool={details.get('tool_name', '?')}"
            elif call_type == "llm":
                preview = f"tokens={details.get('input_tokens', 0)}+{details.get('output_tokens', 0)}"

        logger.debug(f"[agent_log] {call_type}: {preview}, session={session_id}")

    except Exception as e:
        # Don't fail the main request if logging fails
        logger.warning(f"[agent_log] Failed to log {call_type}: {e}")


# Convenience wrappers for specific call types

async def log_bash_execution(
    command: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    sandbox_session_id: str | None = None,
    success: bool = True,
    output: str | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
    source: str = "unknown",
    decision: str = "allowed",
) -> None:
    """Log a bash command execution."""
    # Truncate output
    output_preview = None
    if output:
        output_preview = output[:500] + "..." if len(output) > 500 else output

    log_agent_call(
        call_type="bash",
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        success=success,
        latency_ms=latency_ms,
        error_message=error_message,
        details={
            "command": command,
            "output_preview": output_preview,
            "sandbox_session_id": sandbox_session_id,
            "source": source,
            "decision": decision,
        },
    )


async def log_tool_call(
    tool_name: str,
    tool_input: Any,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    success: bool = True,
    output: str | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> None:
    """Log a tool (MCP) call."""
    output_preview = None
    if output:
        output_preview = output[:500] + "..." if len(output) > 500 else output

    log_agent_call(
        call_type="tool",
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        success=success,
        latency_ms=latency_ms,
        error_message=error_message,
        details={
            "tool_name": tool_name,
            "input": tool_input,
            "output_preview": output_preview,
        },
    )


async def log_llm_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    success: bool = True,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> None:
    """Log an LLM API call."""
    log_agent_call(
        call_type="llm",
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        success=success,
        latency_ms=latency_ms,
        error_message=error_message,
        details={
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )


# Legacy function for context access (kept for backward compatibility)
def log_context_access(
    path: str,
    node_type: str | None = None,
    node_name: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """
    Record a context access event (data injection to sandbox).
    This is separate from agent_logs as it tracks data egress, not execution.
    """
    try:
        supabase = get_supabase_client()

        # NOTE: access_logs has no "path" column (schema uses node_name); inserting
        # "path" previously made every write fail silently in the except below.
        # The accessed-node identifier lands in node_name (falling back to path).
        supabase.table("access_logs").insert({
            "node_type": node_type,
            "node_name": node_name or path,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "project_id": project_id,
        }).execute()

        logger.debug(f"[access_log] Logged access: path={path}, agent={agent_id}")

    except Exception as e:
        logger.warning(f"[access_log] Failed to log access: {e}")
