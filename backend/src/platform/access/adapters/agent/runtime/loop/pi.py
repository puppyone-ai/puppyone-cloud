"""Pi conversation and wire adapter; no provider SDK or project filesystem access."""

from src.platform.access.adapters.agent.runtime.loop.port import (
    LoopChannel,
    LoopProtocolError,
    Row,
)


class PiLoop:
    """The current composition supplies the sandbox channel.

    A separate host may inject a different channel using the same Pi protocol;
    model policy, resource ownership and publication remain in the control plane.
    """

    def __init__(self, channel: LoopChannel | None = None):
        self.channel = channel

    def initial_state(self) -> Row:
        return {"version": 2, "pi_version": "0.85.1", "entries": None, "leaf_id": None}

    def checkpoint(self, value: Row) -> Row:
        return {key: value[key] for key in ("version", "pi_version", "entries", "leaf_id")}

    def continue_session(self, value: Row) -> Row:
        return {"entries": value["entries"], "leaf_id": value["leaf_id"]}

    def begin_run(self, value: Row) -> Row:
        return {"run_entry_start": len(value.get("entries") or [])}

    def completed_tools(self, value: Row) -> set[str]:
        return {
            call
            for entry in value.get("entries") or []
            if (call := entry.get("message", {}).get("toolCallId"))
        }

    def confirmed_text(self, value: Row) -> str:
        return "".join(
            part.get("text", "")
            for entry in (value.get("entries") or [])[value.get("run_entry_start", 0) :]
            if entry.get("message", {}).get("role") == "assistant"
            for part in entry["message"].get("content", [])
            if part.get("type") == "text"
        )[-200000:]

    def receipt_result(self, receipt: Row) -> Row:
        return receipt["result"]["pi_result"]

    def receipt_payload(self, result: Row, checkpoint: Row) -> Row:
        # Preserve the deployed receipt shape; a source refactor is not a data migration.
        return {"pi_result": result, "checkpoint": checkpoint}

    async def start(self, channel: LoopChannel, config: Row) -> None:
        await (self.channel or channel).send({"type": "start", "config": config})

    async def send(self, channel: LoopChannel, value: Row) -> None:
        await (self.channel or channel).send(value)

    async def receive(self, channel: LoopChannel) -> Row:
        frame = await (self.channel or channel).receive()
        if frame.get("type") == "ready":
            if frame.get("pi_version") != "0.85.1" or frame.get("node_version") != "v22.22.3":
                raise ValueError("Unexpected worker artifact")
            if frame.get("workspace_version") != 2:
                raise ValueError("Sandbox artifact does not support provider workspaces")
            if frame.get("operation_version") != 1:
                raise LoopProtocolError("worker_protocol_mismatch")
        return frame
