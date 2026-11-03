"""Text model calls go to the configured provider and never fall back."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from src.infra.llm.config import LLMConfig
from src.infra.llm.exceptions import APIKeyError
from src.infra.llm.service import LLMService

MODEL = "openai/deepseek-flash-1017"
BASE = "https://provider.example/v1"


def service(*, api_base=BASE, api_key="provider-key") -> tuple[LLMService, list[dict]]:
    calls: list[dict] = []

    async def acompletion(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="pong"), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        )

    llm = LLMService()
    llm.config = LLMConfig(_env_file=None, llm_api_base=api_base, llm_api_key=SecretStr(api_key))
    llm.default_model = llm.config.default_text_model
    llm.supported_models = llm.config.supported_text_models
    llm._litellm_loaded = True
    llm._acompletion = acompletion
    return llm, calls


@pytest.fixture(autouse=True)
def litellm_exceptions(monkeypatch):
    """The service imports these names lazily; importing litellm itself takes 20s."""

    class Unused(Exception):
        pass

    module = SimpleNamespace(
        APIError=Unused,
        AuthenticationError=Unused,
        RateLimitError=Unused,
        Timeout=Unused,
    )
    monkeypatch.setitem(sys.modules, "litellm.exceptions", module)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("LLM_API_BASE", "LLM_API_KEY", "DEFAULT_TEXT_MODEL"):
        monkeypatch.delenv(name, raising=False)


async def test_calls_the_configured_provider():
    llm, calls = service()

    response = await llm.call_text_model("ping")

    assert response.content == "pong"
    assert calls[0]["model"] == MODEL
    assert calls[0]["api_base"] == BASE
    assert calls[0]["api_key"] == "provider-key"


@pytest.mark.parametrize("missing", [{"api_base": ""}, {"api_key": ""}], ids=["base", "key"])
async def test_missing_provider_fails_without_calling_anyone(missing):
    llm, calls = service(**missing)

    with pytest.raises(APIKeyError):
        await llm.call_text_model("ping")

    assert calls == []


def test_the_key_never_appears_in_the_configuration_repr():
    config = LLMConfig(_env_file=None, llm_api_key=SecretStr("provider-key"))

    assert "provider-key" not in repr(config)
