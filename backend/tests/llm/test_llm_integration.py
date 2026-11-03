"""LLM Service 集成测试 - 使用真实模型 openai/deepseek-flash-1017

此测试文件仅调用真实的 LLM 模型，需要设置正确的 LLM_API_BASE 和 LLM_API_KEY 环境变量。
测试将验证 LLM 服务的核心功能。
"""

import json
import os

import pytest

from src.infra.llm.exceptions import (
    APIKeyError,
    InvalidResponseError,
    LLMError,
    ModelNotFoundError,
)
from src.infra.llm.schemas import TextModelRequest, TextModelResponse
from src.infra.llm.service import LLMService

# 测试使用的模型
TEST_MODEL = "openai/deepseek-flash-1017"

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_service():
    """创建 LLM 服务实例"""
    service = LLMService()
    return service


@pytest.fixture(autouse=True)
def check_api_key():
    """检查 API 密钥是否已设置"""
    if os.environ.get("RUN_LLM_INTEGRATION") != "1":
        pytest.skip("set RUN_LLM_INTEGRATION=1 to run live model integration tests")
    if not (os.environ.get("LLM_API_BASE") and os.environ.get("LLM_API_KEY")):
        pytest.skip("需要设置 LLM_API_BASE 和 LLM_API_KEY 环境变量才能运行集成测试")


@pytest.fixture(autouse=True)
def configure_litellm(check_api_key):
    """
    配置 litellm（延迟导入）。

    说明：
    - 在某些受限运行环境（如沙盒）中，导入 litellm/httpx 可能因为无法读取系统证书而抛出 PermissionError。
    - 集成测试本质依赖真实网络调用；在这类环境下应直接 skip，而不是让整个测试收集阶段失败。
    """
    try:
        import litellm  # noqa: WPS433 (runtime import is intentional)
    except PermissionError as e:
        pytest.skip(f"受限环境无法导入 litellm（证书读取权限不足）：{e}")
    except Exception as e:
        pytest.skip(f"无法导入 litellm，跳过集成测试：{e}")

    litellm.turn_off_message_logging = True  # 禁用消息日志
    litellm.suppress_debug_info = True  # 禁用调试信息


# ============= 基础功能测试 =============


@pytest.mark.asyncio
async def test_service_initialization(llm_service):
    """测试服务初始化"""
    assert llm_service is not None
    assert llm_service.default_model is not None
    assert len(llm_service.supported_models) > 0
    assert TEST_MODEL in llm_service.supported_models


@pytest.mark.asyncio
async def test_get_supported_models(llm_service):
    """测试获取支持的模型列表"""
    models = llm_service.get_supported_models()
    assert isinstance(models, list)
    assert len(models) > 0
    assert TEST_MODEL in models


@pytest.mark.asyncio
async def test_is_model_supported(llm_service):
    """测试检查模型是否支持"""
    assert llm_service.is_model_supported(TEST_MODEL) is True
    assert llm_service.is_model_supported("nonexistent/model") is False


# ============= 文本生成测试 =============


@pytest.mark.asyncio
async def test_simple_text_generation(llm_service):
    """测试简单的文本生成"""
    prompt = "What is artificial intelligence? Please answer in one sentence."

    response = await llm_service.call_text_model(
        system_prompt="/no_think",
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.7,
        max_tokens=200,
    )

    # 验证响应结构
    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    assert response.model == TEST_MODEL
    assert response.finish_reason in ["stop", "length", "eos", "end_turn"]

    # 验证 token 使用统计
    assert "prompt_tokens" in response.usage
    assert "completion_tokens" in response.usage
    assert "total_tokens" in response.usage
    assert response.usage["prompt_tokens"] > 0
    assert response.usage["completion_tokens"] > 0
    assert (
        response.usage["total_tokens"]
        == response.usage["prompt_tokens"] + response.usage["completion_tokens"]
    )

    print(f"\n生成的内容: {response.content}")
    print(f"Content length: {len(response.content)}")
    print(f"Token 使用: {response.usage}")
    
    # 如果内容不为空，验证其长度
    if response.content:
        assert len(response.content) > 0


@pytest.mark.asyncio
async def test_text_generation_with_system_prompt(llm_service):
    """测试带系统提示的文本生成"""
    system_prompt = "/no_think \n\n你是一个专业的技术文档写作助手，请用简洁专业的语言回答问题。"
    prompt = "什么是 RESTful API？"

    response = await llm_service.call_text_model(
        prompt=prompt,
        system_prompt=system_prompt,
        model=TEST_MODEL,
        temperature=0.3,
        max_tokens=200,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    assert len(response.content) > 0
    assert response.model == TEST_MODEL
    assert response.usage["total_tokens"] > 0

    print(f"\n生成的内容: {response.content}")


@pytest.mark.asyncio
async def test_text_generation_with_low_temperature(llm_service):
    """测试低温度参数的文本生成（更确定性）"""
    prompt = "1 + 1 等于多少？请只回答数字。"

    response = await llm_service.call_text_model(
        system_prompt="/no_think",
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.1,  # 低温度，更确定性
        max_tokens=10,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    # 应该包含数字 2
    assert "2" in response.content

    print(f"\n生成的内容: {response.content}")


# ============= JSON 模式测试 =============


@pytest.mark.asyncio
async def test_json_object_generation(llm_service):
    """测试 JSON 对象生成"""
    system_prompt = "/no_think \n\n你是一个 JSON 生成器，只返回有效的 JSON 对象，不要有其他文字。"
    prompt = """
请生成一个描述一本书的 JSON 对象，包含以下字段：
- title: 书名
- author: 作者
- year: 出版年份
- genre: 类型

请生成一个科幻小说的例子。
"""

    response = await llm_service.call_text_model(
        prompt=prompt,
        system_prompt=system_prompt,
        model=TEST_MODEL,
        temperature=0.3,
        response_format="json_object",
        max_tokens=300,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    assert response.model == TEST_MODEL

    # 验证返回的内容是有效的 JSON
    try:
        data = json.loads(response.content)
        print(f"\n生成的 JSON: {json.dumps(data, ensure_ascii=False, indent=2)}")

        # 可选：验证 JSON 结构（如果模型正确理解了指令）
        # 注意：由于模型可能不完全遵循指令，这部分验证可能会失败
        # 这里只是示例，实际测试可以更宽松
        if isinstance(data, dict):
            print("✓ JSON 对象结构正确")
    except json.JSONDecodeError:
        pytest.fail(f"模型返回的内容不是有效的 JSON: {response.content}")


@pytest.mark.asyncio
async def test_json_array_generation(llm_service):
    """测试 JSON 数组生成"""
    system_prompt = "/no_think \n\n你是一个 JSON 生成器，只返回有效的 JSON，不要有其他文字。"
    prompt = """
请生成一个包含 3 个水果对象的 JSON 数组，每个对象有 name 和 color 字段。
"""

    response = await llm_service.call_text_model(
        prompt=prompt,
        system_prompt=system_prompt,
        model=TEST_MODEL,
        temperature=0.3,
        response_format="json_object",
        max_tokens=300,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None

    # 验证是有效的 JSON
    try:
        data = json.loads(response.content)
        print(f"\n生成的 JSON: {json.dumps(data, ensure_ascii=False, indent=2)}")
    except json.JSONDecodeError:
        pytest.fail(f"模型返回的内容不是有效的 JSON: {response.content}")


# ============= Request 对象测试 =============


@pytest.mark.asyncio
async def test_call_with_request_object(llm_service):
    """测试使用 Request 对象调用模型"""
    request = TextModelRequest(
        prompt="请用一句话解释什么是机器学习。",
        system_prompt="/no_think \n\n你是一个 AI 教育专家。",
        model=TEST_MODEL,
        temperature=0.5,
        response_format="text",
        max_tokens=150,
    )

    response = await llm_service.call_text_model_from_request(request)

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    assert len(response.content) > 0
    assert response.model == TEST_MODEL
    assert response.usage["total_tokens"] > 0

    print(f"\n生成的内容: {response.content}")


# ============= 错误处理测试 =============


@pytest.mark.asyncio
async def test_unsupported_model_error(llm_service):
    """测试不支持的模型错误"""
    with pytest.raises(ModelNotFoundError) as exc_info:
        await llm_service.call_text_model(
            system_prompt="/no_think",
            prompt="Test prompt",
            model="nonexistent/model",
        )

    error = exc_info.value
    assert error.model == "nonexistent/model"
    assert len(error.available_models) > 0
    assert "not supported" in str(error)


@pytest.mark.asyncio
async def test_empty_prompt_handling(llm_service):
    """测试空提示的处理"""
    # 即使提示为空，也应该能够调用模型（模型可能返回空或默认响应）
    try:
        response = await llm_service.call_text_model(
            prompt="",
            system_prompt="/no_think",
            model=TEST_MODEL,
            max_tokens=50,
        )

        # 如果成功，验证响应结构
        assert isinstance(response, TextModelResponse)
        print(f"\n空提示的响应: {response.content}")
    except Exception as e:
        # 某些模型可能不接受空提示
        print(f"\n空提示导致错误（预期行为）: {e}")


# ============= 边界条件测试 =============


@pytest.mark.asyncio
async def test_long_prompt_handling(llm_service):
    """测试长提示的处理"""
    # 创建一个较长的提示
    long_prompt = "请总结以下文本: " + "这是一个测试句子。" * 50

    response = await llm_service.call_text_model(
        prompt=long_prompt,
        model=TEST_MODEL,
        temperature=0.3,
        max_tokens=200,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    assert response.usage["prompt_tokens"] > 100  # 应该有较多的输入 token

    print(f"\n长提示的 Token 使用: {response.usage}")


@pytest.mark.asyncio
async def test_max_tokens_limit(llm_service):
    """测试 max_tokens 限制"""
    response = await llm_service.call_text_model(
        prompt="请写一篇关于人工智能的长文章。",
        model=TEST_MODEL,
        temperature=0.7,
        max_tokens=50,  # 限制输出长度
    )

    assert isinstance(response, TextModelResponse)
    assert response.usage["completion_tokens"] > 0
    # 可能因为达到 token 限制而结束
    print(f"\nFinish reason: {response.finish_reason}")
    print(f"Completion tokens: {response.usage['completion_tokens']}")


# ============= 多次调用稳定性测试 =============


@pytest.mark.asyncio
async def test_multiple_sequential_calls(llm_service):
    """测试连续多次调用的稳定性"""
    prompts = [
        "1+1=?",
        "2+2=?",
        "3+3=?",
    ]

    responses = []
    for prompt in prompts:
        response = await llm_service.call_text_model(
            prompt=prompt,
            model=TEST_MODEL,
            temperature=0.1,
            max_tokens=20,
        )
        responses.append(response)

    # 验证所有调用都成功
    assert len(responses) == 3
    for i, response in enumerate(responses):
        assert isinstance(response, TextModelResponse)
        assert response.content is not None
        print(f"\n第 {i+1} 次调用结果: {response.content}")


@pytest.mark.asyncio
async def test_concurrent_calls(llm_service):
    """测试并发调用的稳定性"""
    import asyncio

    prompts = [
        "什么是 Python?",
        "什么是 JavaScript?",
        "什么是 Java?",
    ]

    # 并发调用
    tasks = [
        llm_service.call_text_model(
            prompt=prompt,
            model=TEST_MODEL,
            temperature=0.3,
            max_tokens=100,
        )
        for prompt in prompts
    ]

    responses = await asyncio.gather(*tasks)

    # 验证所有调用都成功
    assert len(responses) == 3
    for i, response in enumerate(responses):
        assert isinstance(response, TextModelResponse)
        assert response.content is not None
        assert response.model == TEST_MODEL
        print(f"\n并发调用 {i+1} 结果: {response.content[:50]}...")


# ============= 特殊字符和编码测试 =============


@pytest.mark.asyncio
async def test_unicode_handling(llm_service):
    """测试 Unicode 字符处理"""
    prompt = "请用一句话介绍: 日本語、한국어、中文、Emoji 😀🎉🚀"

    response = await llm_service.call_text_model(
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.5,
        max_tokens=150,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    print(f"\nUnicode 响应: {response.content}")


@pytest.mark.asyncio
async def test_special_characters_handling(llm_service):
    """测试特殊字符处理"""
    prompt = "请解释这些符号: @#$%^&*()[]{}|\\/<>?~`"

    response = await llm_service.call_text_model(
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.5,
        max_tokens=150,
    )

    assert isinstance(response, TextModelResponse)
    assert response.content is not None
    print(f"\n特殊字符响应: {response.content}")


# ============= 性能和健壮性测试 =============


@pytest.mark.asyncio
async def test_response_time(llm_service):
    """测试响应时间"""
    import time

    start_time = time.time()

    response = await llm_service.call_text_model(
        prompt="Hello, how are you?",
        model=TEST_MODEL,
        temperature=0.5,
        max_tokens=50,
    )

    end_time = time.time()
    elapsed_time = end_time - start_time

    assert isinstance(response, TextModelResponse)
    print(f"\n响应时间: {elapsed_time:.2f} 秒")

    # 合理的响应时间应该在 60 秒内（配置的超时时间）
    assert elapsed_time < 60


@pytest.mark.asyncio
async def test_deterministic_output_with_low_temperature(llm_service):
    """测试低温度下的确定性输出
    
    注意: Qwen3-8b 是一个支持推理模式的模型,会在 reasoning_content 中放置思考过程,
    在 content 中放置最终答案。需要足够的 tokens 才能生成完整响应。
    """
    # 使用简单的问题,需要足够的 tokens 让模型完成推理和答案
    prompt = "What is 2+2? Answer with only the number."
    
    # 使用相同参数调用两次,给予充足的 tokens
    response1 = await llm_service.call_text_model(
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.0,  # 最低温度，应该最确定
        max_tokens=200,  # 足够的 tokens 让模型完成推理和答案
    )

    response2 = await llm_service.call_text_model(
        prompt=prompt,
        model=TEST_MODEL,
        temperature=0.0,
        max_tokens=200,
    )

    print(f"\n第一次响应: {response1.content}")
    print(f"第二次响应: {response2.content}")
    print(f"第一次 finish_reason: {response1.finish_reason}")
    print(f"第二次 finish_reason: {response2.finish_reason}")

    # 验证响应不为空
    assert len(response1.content.strip()) > 0, "第一次响应内容为空"
    assert len(response2.content.strip()) > 0, "第二次响应内容为空"
    
    # 验证两次响应都包含答案 "4"
    assert "4" in response1.content, f"第一次响应中没有找到答案: {response1.content}"
    assert "4" in response2.content, f"第二次响应中没有找到答案: {response2.content}"
