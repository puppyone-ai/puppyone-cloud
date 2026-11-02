"""
OpenRouter 提供商实现

支持通过OpenRouter访问多种模型
"""
import os
import requests
from typing import List, Dict, Any, Optional
import warnings

from qllama.providers.base import Provider
from qllama.capabilities import ModelCapability

class OpenRouterProvider(Provider):
    """OpenRouter 模型提供商"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 OpenRouter 提供商
        
        Args:
            api_key: OpenRouter API 密钥，如果不提供则从环境变量获取
        """
        # 获取 API 密钥
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            warnings.warn("未设置 OPENROUTER_API_KEY 环境变量或提供 API 密钥")
        
        # 获取 API URL
        self.base_url = os.environ.get("OPENROUTER_CHAT_URL", "https://openrouter.ai/api/v1/chat/completions")
        
        # 常见模型列表（仅保留 GPT-5）
        self.models = [
            "openai/gpt-5",
        ]
    
    @classmethod
    def list_models(cls) -> List[str]:
        """
        列出所有可用模型
        
        Returns:
            模型 ID 列表
        """
        try:
            # 这里我们使用一个静态列表，实际应用中可以调用 OpenRouter API 获取最新列表
            return [
                "openai/gpt-5",
            ]
        except Exception as e:
            warnings.warn(f"获取 OpenRouter 模型列表失败: {e}")
            return ["openai/gpt-5"]
    
    def get_capabilities(self, model_id: str) -> ModelCapability:
        """
        获取模型的能力
        
        Args:
            model_id: 模型 ID
            
        Returns:
            ModelCapability: 模型能力
        """
        # 所有 OpenRouter 模型都支持 LLM 能力
        return ModelCapability.LLM
    
    def generate(self, model_id: str, prompt: str, **kwargs) -> str:
        """
        使用指定模型生成文本
        
        Args:
            model_id: 模型 ID
            prompt: 提示文本
            **kwargs: 其他参数
            
        Returns:
            生成的文本
            
        Raises:
            Exception: 如果生成失败
        """
        if not self.api_key:
            raise ValueError("OpenRouter API 密钥未设置")
        
        # 解析参数
        max_tokens = kwargs.get("max_tokens", 4096)
        temperature = kwargs.get("temperature", 0.7)
        max_thinking_tokens = kwargs.get("max_thinking_tokens", 0)
        system_message = kwargs.get("system_message", "你是一个有用的AI助手。")
        
        # 构建消息
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        
        # 构建请求头
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # 构建请求体
        payload = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        if max_thinking_tokens > 0:
            payload["reasoning"] = {
                "max_tokens": max_thinking_tokens
            }
        
        # 发送请求
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            
            # 解析响应
            result = response.json()
            if "choices" not in result or not result["choices"]:
                raise ValueError("无效的响应格式")
            
            choice = result["choices"][0]
            if "message" in choice:
                content = choice["message"].get("content", "")
                return content
            else:
                raise ValueError("无效的响应格式")
        except requests.RequestException as e:
            raise Exception(f"OpenRouter 请求失败: {e}")
    