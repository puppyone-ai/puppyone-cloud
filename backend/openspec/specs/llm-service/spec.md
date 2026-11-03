# llm-service Specification

## Purpose
TBD - created by archiving change add-etl-pipeline-module. Update Purpose after archive.
## Requirements
### Requirement: LLM 服务配置管理

系统 SHALL 提供灵活的 LLM 服务配置管理,文本模型通过 OpenAI 兼容接口接入,接入地址和密钥由配置决定。

#### Scenario: 配置项完整性

- **WHEN** 应用启动并初始化 LLM 服务
- **THEN** 应从配置中读取以下参数:
  - 文本模型列表配置
  - 接入地址 `LLM_API_BASE`
  - API 密钥 `LLM_API_KEY`
  - 请求超时时间(默认 60 秒)
  - 重试次数(默认 3 次)
  - 温度参数(默认 0.3)
- **AND** 所有配置应可通过环境变量覆盖

#### Scenario: 默认模型配置

- **WHEN** 配置模型
- **THEN** 默认文本模型为 `openai/deepseek-flash-1017`
- **AND** `openai/` 前缀表示通过 LiteLLM 以 OpenAI 协议访问 `LLM_API_BASE`
- **AND** 用户可覆盖默认模型

#### Scenario: 接入配置缺失

- **WHEN** 调用文本模型时缺少 `LLM_API_BASE` 或 `LLM_API_KEY`
- **THEN** 应直接抛出 API 密钥错误
- **AND** 不应回退到其他供应商

### Requirement: 文本模型调用

系统 SHALL 支持调用文本大模型进行数据转换和清洗。

#### Scenario: 成功调用文本模型

- **WHEN** 调用文本模型接口
- **AND** 提供有效的 prompt 和模型名称
- **THEN** 应使用 litellm SDK 异步调用模型
- **AND** 返回模型生成的文本内容
- **AND** 响应时间应小于配置的超时时间

#### Scenario: 支持系统提示词

- **WHEN** 调用文本模型时指定 system_prompt
- **THEN** 应在消息列表中添加 system 角色的消息
- **AND** system_prompt 应在 user 消息之前

#### Scenario: 支持温度参数

- **WHEN** 调用文本模型时指定 temperature 参数
- **THEN** 应使用该参数控制输出随机性
- **AND** temperature 范围应为 0.0-2.0
- **AND** 默认使用 0.3（更确定性的输出）

#### Scenario: JSON 模式输出

- **WHEN** 调用文本模型时指定 response_format="json_object"
- **THEN** 模型应返回有效的 JSON 字符串
- **AND** 如果模型不支持 JSON 模式，应在 prompt 中明确要求 JSON 输出

### Requirement: 错误处理和重试

系统 SHALL 提供统一的错误处理和重试机制，提高 LLM 调用的可靠性。

#### Scenario: API 限流错误

- **WHEN** LLM API 返回 429 (Rate Limit) 错误
- **THEN** 应自动重试（最多 3 次）
- **AND** 使用指数退避策略（1s、2s、4s）
- **AND** 如果所有重试失败，抛出 LLMRateLimitError

#### Scenario: 模型不可用错误

- **WHEN** LLM API 返回 503 (Service Unavailable) 错误
- **THEN** 应自动重试（最多 3 次）
- **AND** 如果所有重试失败，抛出 LLMServiceUnavailableError

#### Scenario: API 密钥错误

- **WHEN** LLM API 返回 401 (Unauthorized) 错误
- **THEN** 不应重试
- **AND** 立即抛出 LLMAPIKeyError
- **AND** 错误信息应说明 API 密钥无效或缺失

#### Scenario: 请求超时

- **WHEN** LLM API 请求超过配置的超时时间
- **THEN** 应取消请求
- **AND** 抛出 LLMTimeoutError
- **AND** 错误信息应包含超时时间和模型名称

#### Scenario: 模型不存在

- **WHEN** 请求的模型名称不存在
- **THEN** 不应重试
- **AND** 立即抛出 LLMModelNotFoundError
- **AND** 错误信息应列出可用模型

#### Scenario: 错误日志记录

- **WHEN** 发生 LLM 调用错误
- **THEN** 应记录详细的错误日志:
  - 模型名称
  - 请求参数（隐藏敏感信息）
  - 错误类型和消息
  - 堆栈跟踪
- **AND** 日志级别应为 ERROR

### Requirement: 异步操作适配

系统 SHALL 使用 litellm 的异步接口（acompletion）实现非阻塞的 LLM 调用。

#### Scenario: 异步调用文本模型

- **WHEN** 调用 LLMService.call_text_model() 方法
- **THEN** 应使用 litellm.acompletion() 异步接口
- **AND** 不应阻塞 FastAPI 事件循环
- **AND** 支持多个并发请求

#### Scenario: 并发请求处理

- **WHEN** 多个 ETL 任务同时调用 LLM 服务
- **THEN** 应能并发处理请求
- **AND** 不应相互阻塞
- **AND** 每个请求独立处理错误和重试

### Requirement: 依赖注入支持

系统 SHALL 提供 FastAPI 依赖注入支持，便于其他模块使用 LLM 服务。

#### Scenario: 单例模式

- **WHEN** 通过依赖注入获取 LLMService
- **THEN** 应返回全局单例实例
- **AND** 避免重复初始化
- **AND** 线程安全

#### Scenario: 依赖注入示例

- **WHEN** ETL 模块需要调用 LLM 服务
- **THEN** 应使用 `Depends(get_llm_service)` 注入
- **AND** 无需手动管理服务生命周期

### Requirement: 配置验证

系统 SHALL 在启动时验证 LLM 配置的有效性。

#### Scenario: API 密钥验证

- **WHEN** 应用启动
- **THEN** 应检查必需的 API 密钥是否配置
- **AND** 如果缺失关键密钥，应打印警告日志
- **AND** 不应阻止应用启动

#### Scenario: 模型名称验证

- **WHEN** 配置模型列表
- **THEN** 应验证模型名称格式正确（provider/model-name）
- **AND** 如果格式错误，应打印警告日志

#### Scenario: 超时时间验证

- **WHEN** 配置请求超时时间
- **THEN** 应验证超时时间为正整数
- **AND** 建议范围为 30-300 秒
- **AND** 如果超出建议范围，应打印警告
