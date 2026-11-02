import os
import time
from typing import List, Dict, Any, Optional

from qllama.capabilities import ModelCapability, cached
from qllama.providers.base import Provider

# 尝试导入transformers库，没有安装则忽略
try:
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from transformers import pipeline
    import numpy as np
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

class HuggingFaceProvider(Provider):
    """HuggingFace提供商实现"""

    def __init__(self, cache_dir: Optional[str] = None):
        """
        初始化HuggingFace提供商
        
        Args:
            cache_dir: 模型缓存目录，默认为None(使用transformers默认缓存目录)
        """
        self.cache_dir = cache_dir
        self._loaded_models = {}
        
    @classmethod
    def list_models(cls, cache_dir: Optional[str] = None) -> List[str]:
        """列出本地已缓存的模型"""
        if not HAS_TRANSFORMERS:
            return []
            
        if not cache_dir:
            # 使用huggingface_hub库中的默认缓存位置
            try:
                from huggingface_hub import constants
                cache_dir = constants.DEFAULT_CACHE_DIR
            except (ImportError, AttributeError):
                # 回退到transformers常见缓存位置
                cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        
        # 这里实现有限制，只能检测到已下载的模型
        # 更完整的实现应该通过API查询可用模型
        if os.path.exists(cache_dir):
            try:
                models = []
                for item in os.listdir(cache_dir):
                    if os.path.isdir(os.path.join(cache_dir, item)):
                        models.append(item)
                return models
            except Exception as e:
                print(f"Error listing HuggingFace models: {e}")
        
        return []
    
    @cached
    def get_capabilities(self, model_name: str) -> ModelCapability:
        """获取模型能力"""
        if not HAS_TRANSFORMERS:
            return ModelCapability.NONE
            
        capabilities = ModelCapability.NONE
        
        # 尝试作为LLM模型加载
        try:
            model = AutoModelForCausalLM.from_pretrained(model_name, cache_dir=self.cache_dir)
            tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=self.cache_dir)
            if hasattr(model, "generate"):
                capabilities |= ModelCapability.LLM
                print(f"Model {model_name} supports LLM")
                
            # 清理以释放内存
            del model
            del tokenizer
        except Exception as e:
            print(f"LLM capability check failed for {model_name}: {e}")
            
        return capabilities
    
    def _get_model(self, model_name: str, capability: ModelCapability):
        """获取模型实例，有缓存功能"""
        cache_key = f"{model_name}_{capability.name}"
        
        if cache_key not in self._loaded_models:
            if capability == ModelCapability.LLM:
                # 加载语言模型
                model = AutoModelForCausalLM.from_pretrained(model_name, cache_dir=self.cache_dir)
                tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=self.cache_dir)
                self._loaded_models[cache_key] = (model, tokenizer)
            else:
                raise ValueError(f"Unsupported capability: {capability}")
        
        return self._loaded_models[cache_key]
    
    def generate(self, model_name: str, prompt: str, **kwargs) -> str:
        """生成文本"""
        if not HAS_TRANSFORMERS:
            raise RuntimeError("transformers library is not installed")
            
        # 获取或加载模型
        model, tokenizer = self._get_model(model_name, ModelCapability.LLM)
        
        device = kwargs.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        model = model.to(device)
        
        # 设置生成参数
        gen_kwargs = {
            "max_length": kwargs.get("max_length", 100),
            "num_return_sequences": 1,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "top_k": kwargs.get("top_k", 50),
            "do_sample": kwargs.get("do_sample", True),
        }
        
        # 对提示文本进行编码
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        
        # 生成文本
        with torch.no_grad():
            generated_ids = model.generate(**inputs, **gen_kwargs)
            
        # 解码生成的文本
        generated_text = tokenizer.decode(generated_ids[0], skip_special_tokens=True)
        
        # 返回生成的文本，去除输入提示
        if generated_text.startswith(prompt):
            return generated_text[len(prompt):].strip()
        return generated_text.strip() 