# B1：Cloud 数据库基线候选

**已生成并通过真实 Supabase 验证，尚未切换现有部署。**

- 覆盖 `20260306085814` 到 `20260923000000`，共 102 个 SQL 迁移。
- `baseline.sql`：最终业务结构，75 张 public 表，以及函数、索引、触发器、RLS 和权限。
- `manifest.json`：来源 commit、每个迁移的 SHA-256、候选文件校验值。
- `required_data.sql`：唯一必要初始化记录；存储盘点状态仍是“未完成”。
- `verification.json`：机器生成的验证结果；`adoption_verified: false` 表示没有做生产切换。

本候选从全新临时库重放代码生成，没有导出生产/测试库的用户数据，也没有包含 PuppyPay 私有表。
它要求已有 Supabase PostgreSQL 17 平台基础设施（Auth、标准角色等），不是安装 Supabase 平台本身的脚本。

## 已完成验证

[生成与验证的 CI 记录](https://github.com/puppyone-ai/puppyone-cloud/actions/runs/36234944746)：

- 原始迁移链与 baseline 的完整 public DDL、所有者、ACL/RLS 一致。
- `auth.users` 上的应用触发器及其状态一致。
- 必要初始化数据一致，其余业务表为空；没有伪造历史数据回填凭证。
- baseline 全新安装：8 个 pgTAP 文件、214 项断言通过。
- 从 `20260720000000` 带合成用户/组织数据升级：数据保留，另一次 214 项断言通过。
- Supabase CLI `2.107.0`，PostgreSQL `17.6`。

102 个原文件合计 800,612 bytes；候选为单个 513,588 bytes 的 SQL。
这是将最终结构固定下来，保留当前对象；并没有顺便清理历史遗留的表或函数。

复现：在仓库根目录执行

```bash
python3 scripts/database_baseline.py verify --candidate supabase/baselines/b1
```

工具只创建、重放和清理自己的临时 Supabase 栈。未来新增迁移时，校验同时检查
“原历史＋新增迁移”和“B1＋新增迁移”两条路径。

## 预研中解决的三个问题

1. Supabase 管理员角色的默认权限已经由平台安装，普通迁移角色无权重新设置。
   生成器确认这些权限没有被历史迁移改变，然后保留平台原值；仍参与全量对比。
2. 应用可以创建 Auth 触发器，但不能任意改变平台表属性。
   普通启用状态通过 CREATE TRIGGER 自然恢复，不额外执行需要表所有者权限的 ALTER。
3. 平台默认授权可能在恢复表时重新带入旧迁移已经撤掉的权限。
   基线先清除业务角色的新对象默认授权，再由导出 SQL 恢复实际 ACL 和最终默认授权。
   对比没有忽略权限差异，也没有提高运行账号权限。

## 正式启用前还要做什么

| 现有依赖 | 为什么不能直接删历史 SQL |
| --- | --- |
| `backend/src/infra/data_migrations/policy.py` | 现有规则校验每个旧文件及其 hash；需要显式的基线替代规则 |
| `backend/src/infra/data_migrations/historical_release.py` | 历史补迁会加载旧文件；需保留公开的过渡版本与升级入口 |
| `backend/src/infra/data_migrations/runner.py` | 数据任务要求具体历史 schema ID；需要定义基线覆盖关系及任务退役规则 |
| `supabase/releases/`、部署 workflow | 需先验证旧库结构和必要数据转换，再对齐迁移历史；不能直接给旧库执行建表 SQL |
| `docker/docker-compose.yml` | 当前只挂载最早的初始化 SQL，需与新的安装及升级入口一起改造 |

正式切换时，可执行文件移入 `supabase/migrations/<version>_baseline_b1.sql`，
本目录不再保留第二份可编辑 SQL。旧文件只有在升级衔接完成后才从当前迁移目录移除，
并在不可变的公开发布版本中保留。这次预研没有删除任何历史迁移，也没有修改生产库记录。
