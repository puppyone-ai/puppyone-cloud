## MODIFIED Requirements

### Requirement: 异步操作适配

系统 SHALL 以同步 boto3 客户端为 S3 的唯一实现；异步调用方 SHALL 通过统一的
`run_blocking` 边界调用它，不得在库代码中创建事件循环。

#### Scenario: 同步函数异步包装

- **WHEN** 异步路由调用任何 S3 操作（如 upload_file、download_file）
- **THEN** 同步 boto3 调用通过 `run_blocking` 在共享线程池中执行
- **AND** 不应阻塞 FastAPI 事件循环
- **AND** 调用方被取消时等待线程中的 I/O 结束后再传播取消

#### Scenario: 流式下载异步适配

- **WHEN** 执行流式下载操作
- **THEN** 应使用异步生成器返回数据块
- **AND** 底层同步读取操作应通过 `run_blocking` 执行
- **AND** 应支持 FastAPI StreamingResponse

#### Scenario: 线程池资源管理

- **WHEN** 多个并发请求访问 S3 服务
- **THEN** 请求级调用共享 `BLOCKING_THREAD_LIMIT` 指定的进程线程容量
- **AND** Version Engine 的对象并发读写使用一个有上限的存储 I/O 线程池
- **AND** boto3 连接池大小与上述线程数匹配，boto3 客户端由多个线程共享
