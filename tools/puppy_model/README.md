# Qllama

统一LLM接口，支持多种模型提供商。

## 特性

- 🎯 **统一接口** - 一套API调用不同提供商的模型
- 🚀 **简单易用** - 最少的代码即可开始使用
- 🔌 **插件化** - 支持扩展新的模型提供商
- 🔧 **能力检测** - 自动检测模型支持的功能（LLM等）

## 安装

```bash
pip install -e /path/to/qllama
```

或者使用requirements.txt:
```bash
pip install -r requirements.txt
```

## 快速开始

```python
from qllama import LLM

# 使用LLM生成文本
llm = LLM("gpt-3.5-turbo")
response = llm.generate("告诉我Python的优点")
print(response)
```

## 支持的提供商

使用模型注册表查看支持的提供商和模型：

```python
from qllama import LLM, ModelRegistry

# 获取所有提供商
registry = ModelRegistry()
providers = registry.list_providers()
print("支持的提供商:", providers)

# 获取支持LLM的模型
llm_models = LLM.list_models()
print(f"支持LLM的模型: {llm_models}")
```

## API 服务

项目包含一个REST API服务，方便其他应用调用：

```bash
python api_server.py
```

然后访问 http://localhost:8080 查看演示页面。

## 更多示例

查看 `examples/` 目录了解更多使用示例：

```python
from qllama import LLM

# 指定提供商
llm = LLM("gpt-4", provider_name="openai")
response = llm.generate("你好")
``` 