# Database baselines

```text
supabase/
├── migrations/
│   ├── 20260926000000_baseline_b1.sql  # 唯一可执行 B1
│   └── <timestamp>_<change>.sql       # B1 之后只追加新迁移
├── archive/before_b1/                # 102 个旧 SQL，内容不变
└── baselines/b1/                     # 来源清单、校验值、验证记录
```

Supabase 只自动执行 `migrations/*.sql`。B1 是普通的时间戳迁移，
`baseline_b1` 只是名称。归档供追溯、旧版本升级和回归测试，不参与日常部署。
不维护第二份 baseline SQL，也不另外维护 current-schema snapshot。

## 新库

要求已有 Supabase PostgreSQL 17 的 Auth、标准角色及平台基础设施。
运行普通 `supabase start` / `supabase migration up --local`，或使用正式的
`supabase db push` 部署到空的 Supabase 项目。无需逐一运行旧 SQL。

## 已有库：先验证，再切换历史

连接通过 `DATABASE_URL` 环境变量提供，不把密码放进命令参数。

```bash
python3 scripts/database_history.py check
python3 scripts/database_history.py adopt
```

`check` 不提交变化。`adopt` 必须先核对全部 102 个历史版本、业务结构、
函数、索引、触发器、RLS 和权限；不一致就停止。核对通过后，在同一事务中：

1. 把原迁移记录完整保存在 `public.migration_log` 的 `schema_baseline_b1` 记录中。
2. 将覆盖范围内的迁移历史替换为 B1 的版本记录。
3. 保留业务数据和原有数据任务凭证；不执行 B1 的建表 SQL。

正式部署 workflow 在 `db push` 之前调用此步骤。重复运行不会重复改写历史或
新建业务表。Supabase 原生 GitHub 集成不会调用我们的前置步骤；已有分支必须
先通过受保护的发布流程完成 adoption，再重试原生集成，不能强行 include-all。
本 PR 的验证只使用临时数据库，仓库切换不表示生产库已切换。

## 更早版本的库

缺少任何被 B1 覆盖的迁移时，adoption 返回 `BASELINE_UPGRADE_REQUIRED`。
公开仓库自带完整的旧升级链，不需要 PuppyPay 私有代码：

```bash
python3 scripts/database_history.py stage --output /tmp/puppyone-before-b1
```

该命令只生成临时升级目录，不连接数据库。使用此目录与原有的分阶段升级流程
补齐 schema 和必要数据任务，再执行 `check` / `adopt`。历史凭证转换仍需原来的
密钥和验证；不能仅因为结构相似就补盖“已完成”的标记。

## 验证和维护

```bash
python3 scripts/database_baseline.py verify --candidate supabase/baselines/b1
```

验证器在独占临时 Supabase 栈中比较“归档历史重放”和“B1 新建”，并验证带数据
升级、历史切换、拒绝结构/权限漂移及之后的增量迁移。CI 不使用生产凭据。

旧 SQL 和已启用基线保持不可修改；新变更只追加。到稳定版本节点再评估 B2，
不按月自动删文件。`docker/docker-compose.yml` 的旧 PG15 启动入口仍固定在原始
归档 SQL，它不是 B1 的安装入口；其完整 PG17 升级需另行验证，不能直接换镜像
并复用旧数据卷。B1 的新库安装和 CI 使用 Supabase PostgreSQL 17。
