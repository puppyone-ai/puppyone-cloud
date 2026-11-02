"""
Qllama API服务

提供获取可用模型信息的REST API
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import sys

# 确保能够导入qllama包
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import qllama
from qllama import ModelCapability, LLM

app = Flask(__name__)
CORS(app)  # 允许跨域请求

@app.route('/api/providers', methods=['GET'])
def get_providers():
    """获取所有提供商"""
    manager = qllama.get_manager()
    providers = manager.list_providers()
    return jsonify({
        "success": True,
        "providers": providers
    })

@app.route('/api/models', methods=['GET'])
def get_all_models():
    """获取所有模型"""
    manager = qllama.get_manager()
    models = manager.list_models()
    return jsonify({
        "success": True,
        "models": models
    })

@app.route('/api/models/llm', methods=['GET'])
def get_llm_models():
    """获取支持LLM的模型"""
    models = qllama.list_llm_models()
    return jsonify({
        "success": True,
        "llm_models": models
    })

@app.route('/api/llm/generate', methods=['POST'])
def generate_text():
    """生成文本"""
    try:
        data = request.json
        if not data:
            return jsonify({
                "success": False,
                "error": "没有提供请求数据"
            }), 400
        
        model = data.get('model')
        prompt = data.get('prompt')
        provider = data.get('provider')
        
        if not model or not prompt:
            return jsonify({
                "success": False,
                "error": "缺少必要参数：model或prompt"
            }), 400
        
        # 其他可选参数
        max_tokens = data.get('max_tokens', 1000)
        temperature = data.get('temperature', 0.7)
        system_message = data.get('system_message', "你是一个有用的AI助手。")
        
        # 创建LLM并生成文本
        llm = LLM(model, provider_name=provider)
        response = llm.generate(
            prompt, 
            max_tokens=max_tokens,
            temperature=temperature,
            system_message=system_message
        )
        
        return jsonify({
            "success": True,
            "text": response
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

if __name__ == '__main__':
    # 默认监听所有接口，方便局域网内其他设备访问
    app.run(host='0.0.0.0', port=42779, debug=True) 