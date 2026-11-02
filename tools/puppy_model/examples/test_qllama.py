"""
Qllama 包的综合测试示例
"""
import os
import sys

# 确保能够导入qllama包
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qllama
from qllama import LLM, ModelCapability

def test_basic_import():
    """测试基本包引入"""
    print("=== 基本包引入测试 ===")
    try:
        print("✅ qllama包引入成功")
        print(f"✅ 可用类: {dir(qllama)}")
        return True
    except Exception as e:
        print(f"❌ 包引入失败: {e}")
        return False

def test_model_manager():
    """测试ModelManager功能"""
    print("\n=== ModelManager测试 ===")
    
    # 获取管理器实例
    manager = qllama.get_manager()
    
    # 列出所有提供商
    providers = manager.list_providers()
    print(f"已注册的提供商: {providers}")
    
    # 使用便捷函数列出所有模型
    all_models = qllama.list_models()
    print(f"所有可用模型:")
    for provider, models in all_models.items():
        print(f"  - {provider}: {len(models)} 个模型")
        for model in models[:3]:  # 只打印前3个
            print(f"    - {model}")
        if len(models) > 3:
            print(f"    - ... 及其他 {len(models)-3} 个模型")
    
# 列出支持LLM的模型
    llm_models = qllama.list_llm_models()
    print(f"\n支持LLM的模型:")
    for provider, models in llm_models.items():
        print(f"  - {provider}: {len(models)} 个模型")
        if models:
            print(f"    - 示例: {models[0]}")

def test_provider_capabilities():
    """测试提供商能力检测"""
    print("\n=== 测试提供商能力检测 ===")
    
    manager = qllama.get_manager()
    providers = manager.list_providers()
    
    for provider_name in providers:
        print(f"\n测试提供商: {provider_name}")
        try:
            provider = manager.get_provider(provider_name)
            models = provider.list_models()
            
            if not models:
                print(f"  - 未找到模型")
                continue
                
            # 测试第一个模型的能力
            model = models[0]
            print(f"  - 测试模型: {model}")
            
            capabilities = provider.get_capabilities(model)
            print(f"  - 模型能力: {capabilities}")
            
                
            if capabilities & ModelCapability.LLM:
                print(f"    ✅ 支持LLM")
            else:
                print(f"    ❌ 不支持LLM")
        except Exception as e:
            print(f"  - 测试{provider_name}能力时出错: {e}")

def test_llm_class():
    """测试LLM类"""
    print("\n=== 测试LLM类 ===")
    
    # 获取支持LLM的模型
    llm_models = qllama.list_llm_models()
    
    if not any(models for models in llm_models.values()):
        print("未找到支持LLM的模型")
        return
    
    # 找到第一个可用的LLM模型
    provider = next((p for p, models in llm_models.items() if models), None)
    if not provider:
        return
    
    model = llm_models[provider][0]
    print(f"使用模型: {model} (提供商: {provider})")
    
    try:
        # 创建LLM
        llm = LLM(model, provider_name=provider)
        
        # 生成文本
        prompt = "Tell me a short joke."
        response = llm.generate(prompt, max_tokens=100)
        
        print(f"✅ 生成文本成功:")
        print(f"  - 提示: {prompt}")
        print(f"  - 回复: {response}")
    except Exception as e:
        print(f"❌ LLM测试失败: {e}")

def test_convenience_functions():
    """测试便捷函数"""
    print("\n=== 测试便捷函数 ===")
    
    try:
        # 测试直接调用便捷函数
        providers = qllama.list_providers()
        print(f"✅ list_providers(): {providers}")
        
        models = qllama.list_models()
        print(f"✅ list_models(): {len(models)} 个提供商")
        
        llm_models = qllama.list_llm_models()
        print(f"✅ list_llm_models(): {len(llm_models)} 个提供商")
        
        
    except Exception as e:
        print(f"❌ 便捷函数测试失败: {e}")

def run_all_tests():
    """运行所有测试"""
    print("🧪 开始运行Qllama包测试\n")
    
    # 基本测试
    if not test_basic_import():
        print("❌ 基本引入测试失败，停止后续测试")
        return
    
    # 功能测试
    test_model_manager()
    test_provider_capabilities()
    test_convenience_functions()
    test_llm_class()
    
    print("\n🎉 测试完成！")

if __name__ == "__main__":
    run_all_tests() 