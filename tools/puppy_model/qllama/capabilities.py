import enum
import functools
from typing import Any, Dict, Callable, Optional

class ModelCapability(enum.Flag):
    """模型能力枚举"""
    NONE = 0                # 无能力
    LLM = 1                 # 语言模型能力 - 文本生成
    
    # 复合能力可以用按位或运算（|）组合
    ALL = LLM  # 所有能力

def cached(func: Callable) -> Callable:
    """函数结果缓存装饰器"""
    cache: Dict[str, Any] = {}
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 基于函数名、参数和关键字参数创建缓存键
        key = str((func.__name__, args, frozenset(kwargs.items())))
        
        # 如果缓存中有结果，则返回
        if key in cache:
            return cache[key]
        
        # 否则调用函数并缓存结果
        result = func(*args, **kwargs)
        cache[key] = result
        return result
    
    # 为装饰器添加清除缓存的方法
    def clear_cache():
        cache.clear()
    
    wrapper.clear_cache = clear_cache
    return wrapper 