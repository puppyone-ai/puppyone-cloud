# Railway 部署指南

本项目已配置使用 Railway Railpack 进行部署。

## 快速开始

### 方式一：Web 界面部署

1. **登录 Railway**
   - 访问 https://railway.app
   - 使用 GitHub 账号登录

2. **创建新项目**
   - 点击 "New Project"
   - 选择 "Deploy from GitHub repo"
   - 选择 ContextBase 仓库
   - Root Directory 设置为 `backend`

3. **配置环境变量**（见下方"环境变量配置"）

4. **部署**
   - Railway 会自动检测配置并部署

### 方式二：CLI 部署

```bash
# 安装 Railway CLI
npm i -g @railway/cli

# 登录
railway login

# 进入 backend 目录
cd backend

# 初始化项目
railway init

# 部署
railway up
```

## 环境变量配置

在 Railway 项目的 **Variables** 标签页配置以下环境变量：

### 必需配置

```bash
# === Supabase 数据库（必需）===
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key

# === S3 存储配置 ===
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_BUCKET_NAME=contextbase-storage
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=your-aws-access-key
S3_SECRET_ACCESS_KEY=your-aws-secret-key

# === JWT 安全配置 ===
JWT_SECRET=your-secure-random-secret-key
JWT_ALGORITHM=HS256

# === MineRU API ===
MINERU_API_KEY=your-mineru-api-key
```

### 可选配置

```bash
# 应用配置
DEBUG=False
APP_NAME=ContextBase
VERSION=1.0.0

# Template Registry（开源默认可保持 builtin）
TEMPLATE_REGISTRY_MODE=builtin
# 托管版启用独立 Registry 时设置以下三项：
# TEMPLATE_REGISTRY_MODE=remote
# TEMPLATE_REGISTRY_URL=https://templates.example.com
# TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON={"official-2026":"<base64url-ed25519-public-key>"}
TEMPLATE_REGISTRY_REQUIRE_SIGNATURE=true

# CORS（生产环境建议指定具体域名）
ALLOWED_HOSTS=https://your-frontend.com

# ETL 配置
ETL_QUEUE_SIZE=30
ETL_WORKER_COUNT=3
ETL_TASK_TIMEOUT=300

# S3 文件大小限制
S3_MAX_FILE_SIZE=104857600
S3_MULTIPART_THRESHOLD=10485760
S3_MULTIPART_CHUNKSIZE=5242880

# OCR Provider: "mineru" | "reducto" | "deepseek"
OCR_PROVIDER=deepseek

# DeepSeek OCR (通过 DeepInfra / Novita，推荐)
DEEPINFRA_API_KEY=your-deepinfra-api-key

# MineRU 配置 (备选 OCR Provider)
MINERU_API_KEY=your-mineru-api-key
MINERU_API_BASE_URL=https://mineru.net/api/v4
MINERU_POLL_INTERVAL=5
MINERU_MAX_WAIT_TIME=600

# LLM 配置
DEFAULT_TEXT_MODEL=openrouter/qwen/qwen3-235b-a22b-2507
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.3
```

## 获取环境变量

### Supabase 配置
1. 登录 Supabase 控制台
2. 进入项目 → Settings → API
3. 复制：
   - Project URL → `SUPABASE_URL`
   - anon/public key → `SUPABASE_KEY`

### S3 配置
**使用 AWS S3：**
- 在 AWS Console 创建 S3 存储桶和 IAM 用户

**使用 Cloudflare R2（推荐）：**
```bash
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
```

### JWT Secret
生成安全密钥：
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### OCR API Key
- **DeepSeek OCR (推荐)**: 访问 https://deepinfra.com 或 https://novita.ai 注册获取 API Key
- **MineRU (备选)**: 访问 https://mineru.net 注册获取 API Key（注意：Key 每 14 天过期）

## 数据库初始化

部署前需在 Supabase 中创建表结构：

```bash
# 按顺序执行 sql/deepwide/ 目录下的文件：
user.sql
project.sql
table.sql
mcp.sql
etl_task.sql
etl_rule.sql
profile.sql
```

## 部署验证

### 1. 健康检查
```bash
curl https://your-app.railway.app/health
```

预期响应：
```json
{"status": "healthy", "service": "ContextBase API"}
```

### 2. API 文档
- Swagger UI: `https://your-app.railway.app/docs`
- ReDoc: `https://your-app.railway.app/redoc`

### 3. 查看日志
```bash
railway logs
```

## Railpack 构建说明

项目使用 **Railpack** 构建系统，具有以下特点：

✅ **自动检测：**
- 自动读取 `.python-version` (Python 3.12)
- 自动检测 `uv.lock` 并运行 `uv sync`
- 自动安装系统依赖

✅ **智能缓存：**
- 依赖层智能缓存
- 构建速度比 Nixpacks 快 30-50%

✅ **零配置：**
- 无需 `requirements.txt`
- 无需手动指定系统包
- 自动优化镜像大小

## 性能优化

根据实例大小调整 ETL 配置：

**小实例（512MB）：**
```bash
ETL_WORKER_COUNT=1
ETL_QUEUE_SIZE=10
```

**中实例（2GB）：**
```bash
ETL_WORKER_COUNT=3
ETL_QUEUE_SIZE=30
```

**大实例（4GB+）：**
```bash
ETL_WORKER_COUNT=5
ETL_QUEUE_SIZE=50
```

## 常见问题

### Q: 提示 "SUPABASE_URL 和 SUPABASE_KEY 环境变量必须设置"
**原因：** 环境变量加载问题

**解决：**
- 确认在 Railway Variables 中已正确配置 `SUPABASE_URL` 和 `SUPABASE_KEY`
- 变量名必须完全匹配（区分大小写）
- 变量值不要有多余的空格或引号
- 重新部署：`railway up` 或在 Web 界面触发重新部署

**注意：** 本项目已正确配置环境变量加载：
- 本地开发：使用 `.env` 文件（需自行创建）
- 生产环境：使用 Railway Variables（自动注入到系统环境）

### Q: 502 Bad Gateway
**原因：** 应用未正确启动或端口配置错误

**解决：**
- 检查日志：`railway logs`
- 确认环境变量已配置
- 确保应用监听 `$PORT` 环境变量

### Q: 构建失败
**解决：**
- 确保 `uv.lock` 已提交
- 检查 `pyproject.toml` 格式正确
- 查看构建日志排查错误

### Q: 内存不足
**解决：**
- 在 Railway Settings 中增加内存限制
- 减少 `ETL_WORKER_COUNT`

### Q: 文件存储丢失
**说明：** Railway 使用临时文件系统

**解决：**
- 所有持久化数据必须存到 S3
- `.mineru_cache` 等临时目录会在重启后清空（正常现象）

## 成本估算

Railway 按使用量计费：

- 小实例（512MB）：约 $5-8/月
- 中实例（2GB）：约 $10-15/月
- 大实例（4GB）：约 $20-30/月

## 安全建议

1. **生产环境配置：**
   - `DEBUG=False`
   - `SKIP_AUTH=False`
   - `ALLOWED_HOSTS` 指定具体域名

2. **密钥管理：**
   - 使用强随机密钥
   - 定期轮换 API Keys
   - 不在代码中硬编码敏感信息

3. **数据库安全：**
   - 配置 Supabase RLS 策略
   - 限制 API Key 权限

## 部署检查清单

- [ ] 环境变量已配置
- [ ] Supabase 数据库表已创建
- [ ] S3 存储桶已创建
- [ ] JWT Secret 已生成
- [ ] OCR API Key 已获取 (DeepInfra 或 MineRU)
- [ ] 健康检查通过
- [ ] API 文档可访问
- [ ] 日志无严重错误

## 支持

- Railway 文档: https://docs.railway.app
- Railpack 文档: https://docs.railway.app/guides/builds
- Railway 社区: https://discord.gg/railway
