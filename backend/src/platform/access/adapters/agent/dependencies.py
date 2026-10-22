from src.platform.access.adapters.agent.service import AgentService

_agent_service = None


def get_agent_service() -> AgentService:
    global _agent_service
    if _agent_service is None:
        from src.platform.access.adapters.agent.runtime.persistence.commands import RunRepository

        from src.platform.workspace.service import WorkbenchAdmission
        _agent_service = AgentService(RunRepository(), workbenches=WorkbenchAdmission())
    return _agent_service
