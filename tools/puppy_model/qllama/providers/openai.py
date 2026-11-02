import os
import time
from typing import List, Dict, Any, Optional
import warnings

from qllama.capabilities import ModelCapability, cached
from qllama.providers.base import Provider

# 尝试导入OpenAI库，没有安装则忽略
try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

class OpenAIProvider(Provider):
    """OpenAI提供商实现"""
    
    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None):
        """
        初始化OpenAI提供商
        
        Args:
            api_key: OpenAI API密钥，为None时使用环境变量OPENAI_API_KEY
            api_base: OpenAI API基础URL，为None时使用默认值
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.api_base = api_base
        
        if not HAS_OPENAI:
            warnings.warn("openai模块未安装，OpenAI功能将不可用")
            return
            
        self.client = self._setup_client()
    
    def _setup_client(self):
        """设置OpenAI客户端"""
        if not HAS_OPENAI:
            return None
            
        client_kwargs = {}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.api_base:
            client_kwargs["base_url"] = self.api_base
            
        return openai.OpenAI(**client_kwargs)
    
    @classmethod
    def list_models(cls, api_key: Optional[str] = None, api_base: Optional[str] = None) -> List[str]:
        """列出可用的模型"""
        if not HAS_OPENAI:
            return []
            
        try:
            # 创建临时客户端
            client_kwargs = {}
            if api_key or os.environ.get("OPENAI_API_KEY"):
                client_kwargs["api_key"] = api_key or os.environ.get("OPENAI_API_KEY")
            if api_base:
                client_kwargs["base_url"] = api_base
                
            client = openai.OpenAI(**client_kwargs)
            
            # 获取模型列表
            models = client.models.list()
            return [model.id for model in models.data]
        except Exception as e:
            print(f"Error listing OpenAI models: {e}")
            return []
    
    @cached
    def get_capabilities(self, model_name: str) -> ModelCapability:
        """获取模型能力"""
        if not HAS_OPENAI or not self.client:
            return ModelCapability.NONE
            
        capabilities = ModelCapability.NONE
        
        # GPT模型支持LLM能力
        if model_name.startswith(("gpt-", "ft:gpt-")):
            capabilities |= ModelCapability.LLM
            print(f"Model {model_name} supports LLM")

        return capabilities
    
    def generate(self, model_name: str, prompt: str, **kwargs) -> str:
        """生成文本"""
        if not HAS_OPENAI or not self.client:
            raise RuntimeError("openai library is not installed or client setup failed")
            
        # 生成模型调用
        try:
            # 构建消息列表
            messages = [{"role": "user", "content": prompt}]
            
            # 设置参数
            completion_kwargs = {
                "model": model_name,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.7),
                "top_p": kwargs.get("top_p", 1.0),
                "n": 1,
                "stream": False,
                "max_tokens": kwargs.get("max_tokens", None),
            }
            
            # 如果提供了stop参数
            if "stop" in kwargs:
                completion_kwargs["stop"] = kwargs["stop"]
                
            # 调用API
            response = self.client.chat.completions.create(**completion_kwargs)
            
            # 提取生成的文本
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"OpenAI generation failed: {e}") 