from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from qllama.capabilities import ModelCapability

class Provider(ABC):
    """模型提供商基类"""
    
    @classmethod
    @abstractmethod
    def list_models(cls, **kwargs) -> List[str]:
        """
        列出可用的模型
        
        Returns:
            模型ID列表
        """
        pass
    
    @abstractmethod
    def get_capabilities(self, model_name: str) -> ModelCapability:
        """
        获取模型支持的能力
        
        Args:
            model_name: 模型名称或ID
            
        Returns:
            ModelCapability: 模型能力枚举
        """
        pass
    
    def generate(self, model_name: str, prompt: str, **kwargs) -> str:
        """
        生成文本
        
        Args:
            model_name: 模型名称或ID
            prompt: 提示文本
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        raise NotImplementedError(f"Provider {self.__class__.__name__} does not implement generate") 