import os
import sys
from typing import List, Dict, Any, Optional, Union, Tuple
import importlib
import warnings

from qllama.capabilities import ModelCapability
from qllama.providers.base import Provider
from qllama.registry import ModelRegistry

class ModelManager:
    """模型管理器，负责模型的注册和调用"""
    
    def __init__(self):
        """初始化模型管理器"""
        self.providers: Dict[str, Provider] = {}
        self.model_cache: Dict[str, Tuple[str, str]] = {}  # 模型名到(提供商ID,模型ID)的映射
        
        # 自动加载内置的提供商
        self._load_built_in_providers()
    
    def _load_built_in_providers(self):
        """加载内置的提供商"""
        # 尝试加载OpenAI提供商
        try:
            import openai
            from qllama.providers.openai import OpenAIProvider
            self.register_provider("openai", OpenAIProvider())
        except ImportError:
            warnings.warn("无法加载OpenAI提供商，请检查是否已安装openai包")
        
        # 尝试加载HuggingFace提供商
        try:
            import transformers
            from qllama.providers.huggingface import HuggingFaceProvider
            self.register_provider("huggingface", HuggingFaceProvider())
        except ImportError:
            warnings.warn("无法加载HuggingFace提供商，请检查是否已安装transformers包")
        
        # 尝试加载Ollama提供商
        try:
            from qllama.providers.ollama import OllamaProvider
            self.register_provider("ollama", OllamaProvider())
        except ImportError:
            warnings.warn("无法加载Ollama提供商")
            
        # 尝试加载OpenRouter提供商
        try:
            from qllama.providers.openrouter import OpenRouterProvider
            self.register_provider("openrouter", OpenRouterProvider())
        except ImportError:
            warnings.warn("无法加载OpenRouter提供商")
            
    def register_provider(self, provider_id: str, provider: Provider) -> None:
        """
        注册模型提供商
        
        Args:
            provider_id: 提供商ID，如'openai', 'huggingface'等
            provider: 提供商实例
        """
        self.providers[provider_id] = provider
        print(f"注册提供商: {provider_id}")
    
    def get_provider(self, provider_id: str) -> Optional[Provider]:
        """
        获取指定ID的提供商
        
        Args:
            provider_id: 提供商ID
            
        Returns:
            Provider实例，如果不存在则返回None
        """
        return self.providers.get(provider_id)
    
    def list_providers(self) -> List[str]:
        """
        列出所有已注册的提供商
        
        Returns:
            提供商ID列表
        """
        return list(self.providers.keys())
    
    def list_models(self, provider_id: Optional[str] = None, capability: Optional[ModelCapability] = None) -> Dict[str, List[str]]:
        """
        列出所有可用的模型
        
        Args:
            provider_id: 可选，指定提供商ID，为None时列出所有提供商的模型
            capability: 可选，指定模型能力，为None时列出所有能力的模型
            
        Returns:
            字典，键为提供商ID，值为模型ID列表
        """
        result = {}
        
        # 确定要查询的提供商列表
        providers = [provider_id] if provider_id else self.list_providers()
        
        for pid in providers:
            if pid not in self.providers:
                continue
                
            provider = self.providers[pid]
            models = provider.list_models()
            
            # 如果指定了能力，则过滤模型
            if capability is not None:
                filtered_models = []
                for model in models:
                    model_cap = provider.get_capabilities(model)
                    if model_cap & capability:
                        filtered_models.append(model)
                models = filtered_models
            
            result[pid] = models
            
            # 更新模型缓存
            for model in models:
                self.model_cache[f"{pid}:{model}"] = (pid, model)
        
        return result
    
    def resolve_model(self, model: str) -> Tuple[Provider, str]:
        """
        解析模型标识符，获取对应的提供商和模型ID
        
        Args:
            model: 模型标识符，格式为"provider:model_id"或直接为"model_id"
            
        Returns:
            (Provider, model_id)元组
            
        Raises:
            ValueError: 如果模型标识符无效或提供商不存在
        """
        # 检查是否已缓存
        if model in self.model_cache:
            provider_id, model_id = self.model_cache[model]
            provider = self.providers.get(provider_id)
            if provider:
                return provider, model_id
        
        # 解析模型标识符
        if ":" in model:
            provider_id, model_id = model.split(":", 1)
            if provider_id not in self.providers:
                raise ValueError(f"提供商不存在: {provider_id}")
            
            self.model_cache[model] = (provider_id, model_id)
            return self.providers[provider_id], model_id
        
        # 如果没有指定提供商，则尝试在所有提供商中查找
        for provider_id, provider in self.providers.items():
            models = provider.list_models()
            if model in models:
                self.model_cache[model] = (provider_id, model)
                return provider, model
        
        raise ValueError(f"无法找到模型: {model}")
    
    def get_capabilities(self, model: str) -> ModelCapability:
        """
        获取模型的能力
        
        Args:
            model: 模型标识符
            
        Returns:
            ModelCapability: 模型能力
        """
        provider, model_id = self.resolve_model(model)
        return provider.get_capabilities(model_id)
    
    def has_capability(self, model: str, capability: ModelCapability) -> bool:
        """
        检查模型是否具有指定能力
        
        Args:
            model: 模型标识符
            capability: 要检查的能力
            
        Returns:
            bool: 是否具有指定能力
        """
        model_cap = self.get_capabilities(model)
        return bool(model_cap & capability)
    
    def generate(self, model: str, prompt: str, **kwargs) -> str:
        """
        使用指定模型生成文本
        
        Args:
            model: 模型标识符
            prompt: 提示文本
            **kwargs: 其他参数传递给提供商
            
        Returns:
            生成的文本
            
        Raises:
            ValueError: 如果模型不支持LLM能力
        """
        provider, model_id = self.resolve_model(model)
        
        # 检查模型能力
        if not self.has_capability(model, ModelCapability.LLM):
            raise ValueError(f"模型不支持LLM能力: {model}")
        
        # 调用提供商的生成方法
        return provider.generate(model_id, prompt, **kwargs)

# 创建全局模型管理器实例
_manager = ModelManager()

# 导出API函数
def get_manager() -> ModelManager:
    """获取全局模型管理器实例"""
    return _manager

def list_providers() -> List[str]:
    """列出所有提供商"""
    return _manager.list_providers()

def list_models(provider_id: Optional[str] = None, capability: Optional[ModelCapability] = None) -> Dict[str, List[str]]:
    """列出所有模型"""
    return _manager.list_models(provider_id, capability)

def generate(model: str, prompt: str, **kwargs) -> str:
    """生成文本"""
    return _manager.generate(model, prompt, **kwargs)

# 导出便捷函数
def list_llm_models(provider_id: Optional[str] = None) -> Dict[str, List[str]]:
    """列出所有LLM模型"""
    return _manager.list_models(provider_id, ModelCapability.LLM)

# 如果直接运行此模块，则打印所有可用模型
if __name__ == "__main__":
    print("可用的提供商:", list_providers())
    
    all_models = list_models()
    print("\n所有可用模型:")
    for provider, models in all_models.items():
        print(f"  {provider}: {', '.join(models)}")
    
    print("\nLLM模型:")
    for provider, models in list_llm_models().items():
        print(f"  {provider}: {', '.join(models)}")
    