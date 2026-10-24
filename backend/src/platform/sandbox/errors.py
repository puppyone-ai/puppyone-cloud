"""Provider-neutral failures raised by sandbox providers and their callers."""


class WorkerLost(RuntimeError):
    """Provider no longer has an unconfirmed filesystem to preserve."""


class WorkerDisconnected(ConnectionError):
    """Transport loss does not prove the provider's working copy is gone."""


class WorkspaceUnavailable(WorkerDisconnected):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable
