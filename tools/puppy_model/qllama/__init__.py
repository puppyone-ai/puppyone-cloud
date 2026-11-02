"""
qllama包 - 统一LLM接口

支持多种模型提供商，包括：
- OpenAI (GPT系列)
- HuggingFace (transformers模型)
- Ollama (本地模型)
- OpenRouter (多模型路由)

使用示例：
    from qllama import LLM
    
    llm = LLM("gpt-3.5-turbo")
    response = llm.generate("Hello, world!")
"""

from qllama.capabilities import ModelCapability
from qllama.registry import ModelRegistry
from qllama.llm import LLM
from qllama.main import (
    get_manager,
    list_providers,
    list_models,
    list_llm_models,
    generate
)

__all__ = [
    # 能力枚举
    'ModelCapability',
    
    # 模型类
    'LLM',
    'ModelRegistry',
    
    # 主要API
    'get_manager',
    'list_providers',
    'list_models',
    'list_llm_models',
    'generate',
]
