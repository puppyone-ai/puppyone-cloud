"""
Ollama 提供商实现

支持本地运行的Ollama模型
"""
import requests
import json
from typing import List, Dict, Any, Optional
import sys
import os

# 添加父级目录到路径以解决导入问题
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qllama.capabilities import ModelCapability, cached
from qllama.providers.base import Provider

class OllamaProvider(Provider):
    """Ollama提供商实现"""
    
    def __init__(self, endpoint: str = "http://localhost:11434"):
        self.endpoint = endpoint.rstrip("/")
    
    @classmethod
    def list_models(cls, endpoint: str = "http://localhost:11434") -> List[str]:
        """获取模型列表"""
        try:
            response = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=5)
            if response.status_code != 200:
                return []
            
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            print(f"Error listing Ollama models: {e}")
            return []
    
    @cached
    def get_capabilities(self, model_name: str) -> ModelCapability:
        """获取模型能力"""
        capabilities = ModelCapability.NONE
        
        # 检测LLM能力 - 用更可靠的方式检查，增加超时时间
        try:
            response = requests.post(
                f"{self.endpoint}/api/generate",
                json={"model": model_name, "prompt": "hello", "stream": False},
                timeout=10  # 增加超时时间
            )
            if response.status_code == 200:
                capabilities |= ModelCapability.LLM
                print(f"Model {model_name} supports LLM")
        except Exception as e:
            print(f"LLM capability check failed for {model_name}: {e}")
        
        return capabilities
    
    def generate(self, model_name: str, prompt: str, **kwargs) -> str:
        """生成文本"""
        stream = kwargs.pop("stream", False)
        if stream:
            # 流式实现略，根据需要补充
            raise NotImplementedError("Streaming not implemented")
        
        # 只传递Ollama支持的参数
        ollama_params = {
            "model": model_name,
            "prompt": prompt,
            "stream": False
        }
        
        # 映射常见参数到Ollama格式
        if "max_tokens" in kwargs:
            # Ollama使用num_predict而不是max_tokens
            ollama_params["options"] = ollama_params.get("options", {})
            ollama_params["options"]["num_predict"] = kwargs["max_tokens"]
        
        if "temperature" in kwargs:
            ollama_params["options"] = ollama_params.get("options", {})
            ollama_params["options"]["temperature"] = kwargs["temperature"]
        
        if "system_message" in kwargs:
            ollama_params["system"] = kwargs["system_message"]
        
        response = requests.post(
            f"{self.endpoint}/api/generate",
            json=ollama_params,
            timeout=kwargs.get("timeout", 60)
        )
        
        if response.status_code != 200:
            raise Exception(f"Ollama generation failed: {response.text}")
        
        data = response.json()
        return data["response"] 