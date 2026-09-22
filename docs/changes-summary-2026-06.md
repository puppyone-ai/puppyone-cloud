# PuppyOne V2 改动总结(2026-06-06 ~ 06-07)

分支:`feat/context-entrypoints`(未合并)
范围:**第一部分**(§0–7)Context 四大入口点收尾 · 父子 scope 同步 · Access 页 Git Remote 归一 · "Damaged folder" 数据完整性根因修复 + 工具 · 若干收尾清理;**第二部分**(§8–17)Sandbox 即访问面 V2 新功能 —— 需求/Provider 调研、E2B+Fly 实现、E2B SSH 实连与测试、SSH 短期凭证治理、bug 修复 + Fly 代码跟进;**第三部分**(§18–23)前端接线产品化 —— HTTP API、Access 页 Remote Dev (SSH) 卡片、Data 菜单 "SSH Terminal"、reaper 调度 + 多 writer 安全、合并 qubits

---

## 0. 提交清单

| 提交 | 主题 | 类别 |
|---|---|---|
| `ce9c8fdf` | Activity 只读端点(`context_activity_items` 视图) | 四大入口 |
| `7c5b3855` | 前端 Activity Stack 展示 Connect/Sync 运行 | 四大入口 |
| `ff6566f8` | 四大入口点真实状态核验文档 | 四大入口 |
| `43ea5608` | root scope = 项目全局视图,不再 carve 子 scope | 父子 scope |
| `6e0f4060` | scope head 被投影时留下可审计记录 | 父子 scope |
| `7ab4ee84` | scope 视图 exclude 改为 OID 级、容忍损坏 blob | 健壮性 |
| `d04a41ad` | 拆分 `git_remote`(Git Remote)与 `filesystem`(Local Folder Sync) | Access 页 |
| `8c87e7ea` | Local Folder Sync 专属图标 | Access 页 |
| `b96fc99a` | 清理已验证死代码 + 厘清共享 ARQ Redis | 收尾 |
| `30753573` | "Damaged folder" dangling 子树:防再发生 + 读时自愈 | 数据完整性 |
| `18a595f2` | Damaged folder 修复工具(判定可恢复性 + 重接) | 数据完整性 |
| `e5a16f30` | 迁移文件乱序修复(本会话起点) | 收尾 |

> 注:`ddc9f3e5`/`ca6c3186`/`8d93b248` 是 Git Remote 两张卡的早期"显示层合并"尝试,后被 `d04a41ad` 的正确方案(按真实功能拆分)取代。详见第 3 节。

---

## 1. Context 四大入口点(Upload / Import / Connect / Access)

**Feature 描述**
"上下文进入项目"的四种统一入口:
- **Upload** 上传文件(`upload_jobs` / `upload_items`)
- **Import** 一次性导入,如 GitHub 仓库(`import_jobs` + ARQ import worker)
- **Connect** 持续连接/同步外部源(`connections` / `sync_runs`)
- **Access** 访问面(`access_surfaces`:Git Remote / CLI / Local Folder Sync / Agent / MCP / Sandbox)

**之前状态**
后端表、target 表、`context_activity_items` 联合视图与 backfill 迁移基本已就绪(约 90% 实现),但有几处边缘没收口:Activity 没有读取端点、前端 Activity Stack 不展示 Connect/Sync 运行、Import worker 连错了 Redis 导致任务卡在 `queued`。

**做了什么**
- 新增只读端点 `GET /api/v1/activity`,基于 `context_activity_items` 视图,强制项目级鉴权(`ce9c8fdf`);
- 前端 `useActivity` / `ActivityStack` / `SyncJobsWidget` 把 Connect/Sync 运行也展示出来(`7c5b3855`);
- 厘清 Import 与 ETL worker **共用同一个 Redis、靠队列名区分**(`imports` vs `etl`),`IMPORT_REDIS_URL` 是无效变量;运营上把 import worker 的 `ETL_REDIS_URL` 对齐到 api 后,导入恢复正常(代码侧说明见 `b96fc99a` + `ingest/file/config.py` 注释);
- 在部署环境对四个入口做了**多文件实际同步的端到端深测**(非单测):Access ✅、Upload ✅、Import ✅(改 Redis 后)、Connect API ✅。

**效果**
四个入口端到端打通;三类作业(Upload/Import/Connect)在 Activity 里统一可见;导入不再卡 `queued`。

---

## 2. 父子 scope 同步

**期望行为**
同一个文件,无论从父 scope 还是子 scope 看,都应能看到最新版本、不丢失;且每次改动/同步都用**正确的 auth**(子 scope 同步父 scope 改动时不能用父的 auth,反之亦然)。同步发生在 **server 侧**:server 是 source of truth,client 只要拉取对应 scope 下 server 的最新内容即可。

**之前问题**
经实测,声明的子 scope 被 `carved_excludes` 隔离——root(父)视图把子 scope 的子树 carve 掉了,导致 root 看不到 / 不同步子 scope 的内容。

**做了什么**
- `compute_carved_excludes` 对 root scope(`path==""`)返回空——**root 即项目全局视图,能看见/写入/同步所有子 scope**;非 root 的 carving 行为不变(`43ea5608`);
- scope head 因项目根投影而推进时,写入 `version_transactions` + `audit_logs`(`source_channel="scope-sync"`,`operator_type="system"`),留下**可审计、可区分发起方**的记录(`6e0f4060`);
- 读路径继续遵循"从 canonical root 重派生 scope 视图"的 root-first 不变量。

**效果**
父子 scope 之间共享内容双向可见、可同步;每次投影同步都有审计痕迹,auth 边界清晰。

---

## 3. Access 页:两张 "Git Remote" 卡 → Git Remote + Local Folder Sync

**问题描述**
某个 scope 的 Access 页显示了**两张 "Git Remote" 卡**,其中一张是"功能完善"的,另一张是 raw / "Access setup is preparing"。

**之前的真实根因**
这两张卡**根本不是重复**,而是两个不同的内置功能被贴错了标签:
- `git_remote` = 原生 Git Remote(clone/pull/push);
- `filesystem` = **本地文件夹双向同步(Local Folder Sync / OpenClaw)**。

后端规范模型(`connector_service.PROVIDERS_BIDIRECTIONAL`)早已是这样,但 legacy 前端把 `filesystem` 误标成 "Git Remote"、又完全不认识 `git_remote`(title-case 成 "Git Remote" + preparing),于是两个不同功能都显示成 "Git Remote"。

**做了什么(Option A:对齐到规范模型)**
- 标签拆分:`git_remote → "Git Remote"`、`filesystem → "Local Folder Sync"`(`PROVIDER_LABELS`、连接器分组、type-line、创建弹窗、侧栏 chip);
- 新增 `isGitBuiltinProvider()`:两者同走 Git 传输,所以**共享卡片样式/图标/手动命令/setup prompt**,仅标签与描述按精确 provider 区分;
- **撤销**早期的显示层合并(那个把两者合成一张卡的做法会把 Local Folder Sync 藏掉);`(scope_id, kind)` 唯一索引本就保证每种 kind 每 scope 一行;
- Local Folder Sync 配**独立图标**(文件夹 + 双向箭头),与 Git Remote 的分支图标区分(`8c87e7ea`)。

**效果**
一个 scope 现在显示 **Git Remote + Local Folder Sync + CLI** 三张标签正确、图标各异的卡,不再有重复的 "Git Remote";后端无需改动,也未删任何数据。

---

## 4. "Damaged folder/file" 数据完整性(本次核心)

**问题描述**
数据浏览器里 `文档`、`docs` 等文件夹显示红色 **"Damaged"**,展开报 `VERSION_STORAGE_INTEGRITY_ERROR`。

**根因诊断(线上只读取证)**
"Damaged folder" = 某个 tree 条目引用的**子树对象在对象存储里缺失**,而父 tree 仍引用它。叶子 blob 还在(写入安全网保住了),但子树 tree 对象没了 → 父能列出文件夹名、展开却失败。共发现**三个独立缺口**:

1. **GC 不是 fail-safe(会永久删数据)**:`object_gc.mark_reachable_objects` 在遍历对象图时,**读某个对象失败就跳过**,导致那棵子树没被标记为 reachable → 被误判为孤儿 → 删除。
2. **写入安全网只查 blob、不查 tree 闭包**:`_verify_blobs_present` 只 HEAD 叶子 blob,从不校验已发布 root tree 的子树对象,builder 一旦回归就可能发布 dangling tree 而不被发现。
3. **读路径只展示、不自愈**:遇到 dangling 子树只标 "damaged",从不尝试从 canonical 重派生。

**怎么修(`30753573`,均带测试)**
1. **GC fail-safe**:把**遍历(walk)**的读错误与无害错误分开;只要遍历对象图时出过读错误(闭包可能不完整),就**拒绝删除**该项目(仍报告"本会删哪些"供观测)。同时区分"读不到对象"(拦截)与"读到但不是 git 对象"(当不透明叶子,不拦截),避免合法的非 git 对象把 GC 永久卡死。
2. **写入 tree 闭包护栏**:新增 `find_missing_tree_objects`(只查存在性、不下载 blob 的闭包遍历);在 `ProductOperationAdapter._apply_operation` 提交后加一道**配置开关 `VERSION_VERIFY_TREE_CLOSURE_ON_WRITE`**(默认关,因为全树遍历有开销)的运行时护栏,发现 dangling tree 即 `MissingBlobError` 报错。builder 不产生 dangling 由 `test_tree_closure` 固定证明。
3. **读路径自愈**:`tree_reader.list_dir` 检测到 damaged 子项时,best-effort 调既有的 `_repair_project_root_from_scope_state` 从 canonical scope state 重派生——健康项目 no-op,损坏的子 scope 则重新 graft 回来或丢掉死条目。

**修复工具(`18a595f2`)**
新增 `scripts/repair_damaged_folders.py`,在后端环境运行:
- dry-run(只读)对每个 damaged 文件夹判定 **tier ①(可恢复)/ tier ②(已丢失)**;
- `--apply` 把可恢复的子树重新 graft 回 root(单次 CAS,**只 graft、从不删**,不会丢数据)。
- 内容寻址语义:某 hash 的对象没了就没有第二份,"恢复"= 找到该路径**存活的另一版本**(改写过的文件夹的历史版本、或存活的子 scope 树);只写过一次且唯一版本已丢的,正确报告 GONE。

**关于已损坏数据 + 新内容安全性的结论**
- 截图里的 `docs`/`文档`:dry-run 实测为 **tier ②(GONE)**——canonical、location index、旧命名空间 `mut/`、bundle 里都没有存活子树,内容(均为一次性写入的测试文件)无法恢复,**删掉死条目清理即可**。
- **新内容不会再 damaged**,依据:
  1. 生产里**所有自动删对象的路径都是关的**:GC(`VERSION_OBJECT_GC_ENABLED=False`)、bundle sweep(仅 GC 调用)、loose 完整性扫描 heal(`VERSION_INTEGRITY_SCAN_ENABLED/_HEAL=False`);命名空间迁移脚本是一次性、只 copy 不删、手动执行;
  2. 写入 builder 持久化**完整闭包**(已被单测证明);
  3. **实证**:近期 `019e9c*` 一批带 docs 子 scope 的项目全部干净,只有早期 `019e7*`/`019e792*` 批次损坏——说明损坏是历史残留,非在发生的机制;
  4. 纵深防御:万一以后开启 GC,它现在 fail-safe,绝不会再误删活对象。

---

## 5. 其它收尾

- **迁移文件乱序**(`e5a16f30`):`supabase db push --dry-run` 报 `20260531010000_shadow_snapshot_grep_shared.sql` 顺序错误。按"不绕过迁移检查"的原则,**重命名**为 `20260531030000_...` 排到已应用迁移之后,而非用 `--include-all` 绕过。
- **scope 视图 exclude 改 OID 级**(`7ab4ee84`):3 处 exclude 过滤从"下载 blob 再重建"改为 `tree_to_flat` + `build_tree_from_blob_ids`(OID 级、不下载 blob、容忍损坏叶子 blob),更快也更健壮。
- **死代码清理 + Redis 说明**(`b96fc99a`):删除经验证无引用的死代码(`PROVIDERS_SELF_AUTH`、`legacy_changes_from_tree_delta`、`SearchService.now_iso`/`ensure_namespace_schema`、`destroy_workspace`);在 `ingest/file/config.py` 写清 import + etl 共用一个 Redis、靠队列名区分,`IMPORT_REDIS_URL` 不被读取。

---

## 6. 测试与验证

- **单测**:GC fail-safe 回归(遍历读错 ⇒ 不删除)、`test_tree_closure`(builder/graft 持久化完整闭包、checker 能抓 dangling 子树/blob)、修复工具三例(恢复改写文件夹的历史版本 / 从存活 scope_hash 恢复 / 一次性写入丢失正确报 GONE)。相关 integrity/projection/GC 套件全绿。
- **线上**:四大入口端到端深测;`git-view /health`、`ap-fs/tree`、审计日志只读诊断定位损坏项目与根因;修复工具 dry-run 实测确认 tier ②。
- 已知无关失败:`test_engine_resolve::test_resolve_landing_pending_keeps_row_resolving` 引用了已删除的 `engine._submit_version_root_first`,是预存的失效测试,与本次改动无关。

---

## 7. 待办 / 边界

1. **清理已损坏的死条目**:在 UI 删除(或 `ap-fs/rm --recursive`,tree 级删除,不读缺失子树)`docs`、`文档`。`docs` 是声明的子 scope,如需连 scope 定义一起清,在 Access 页删除对应 scope。
2. **可选加固**:想要运行时实证,可临时设 `VERSION_VERIFY_TREE_CLOSURE_ON_WRITE=true` 一段时间——任何写入若产出 dangling tree 会当场报错 + 记日志(默认关因为有 O(tree) 开销)。
3. **未能 100% 锁定当年确切元凶**(日志和对象都已不在),但已逐一排除所有当前还活着的删除路径,这是更强的保证。
4. **合并**:本分支 `feat/context-entrypoints` 尚未合并;合并后部署即生效。

---
---

# 第二部分:Sandbox 即访问面(Sandbox as Access Point)— V2 新功能(2026-06-07)

> 与第一部分同分支 `feat/context-entrypoints`。这是一块**全新功能**的从 0 到可用:把 Access 面的 "Sandbox" 从 legacy 的 JSON-edit 一次性 exec,升级为**按 scope 共享的长存计算环境**——用户用 VSCode Remote-SSH 连进去,所有 git/CLI 在 sandbox 内跑(数据留服务端),会话长存且自动省成本,SSH 凭证短期可撤销。

## 8. 需求与 Feature 梳理

**目标场景**:企业受治理的"按 scope 的计算环境"。
- 用户(已证明对该 scope 有权限)用 **VSCode Remote-SSH** 连入一个 scope 专属 sandbox;
- **所有 git/CLI 在 sandbox 内执行**,scope 内容已就位(server 侧 clone),数据不落客户端;
- sandbox **按 scope 共享**(同 scope 的多用户复用一个箱);
- **会话管理**在「保温成本」与「冷重启成本」之间权衡:三态生命周期
  - **RUNNING** 计算开、工作副本在(全价)
  - **STOPPED** 计算关、**留盘**(只存储费;resume 是增量 `git fetch` 而非全量拉)
  - **DESTROYED** 全回收($0;下次需全量重拉)
- **治理**:短期 SSH 凭证签发/撤销(**离职即失权**)+ per-user 身份(**push 归属到人**)+ 全程审计;
- **两个 provider 用户可选**:Fly + E2B。

设计与调研文档(`docs/proposals/`):`PUP-sandbox-access-point.md`(主设计)、`sandbox-provider-comparison-2026-06.md`、`sandbox-vps-approach-2026-06.md`、`sandbox-roadmap-2026-06.md`、`sandbox-validation-results-2026-06.md`、`sandbox-collab-session-results-2026-06.md`。

## 9. 多 sandbox / VPS 方案对比

调研了 E2B / Modal / Cloudflare Containers / **Fly.io Machines** / 自建 VPS,结论:

| 方案 | 隔离/形态 | SSH(VSCode)| 成本/运维 | 定位 |
|---|---|---|---|---|
| **Fly.io Machines** | 托管 Firecracker microVM,API 秒级开关、scale-to-zero | ✅ **原生**:sshd 跑内部 2222 → Fly Proxy 暴露成公网 TCP 22,官方有 sshd blueprint | 需专用 IPv4(~$3.60/mo)+ 计费;托管省心 | **推荐默认**(原生 TCP、最对口) |
| **E2B** | Firecracker,pause/resume 优雅,**唯一可自托管** | ⚠️ **DIY**:无原生 TCP,自建 sshd + websocat over `wss://`,官方未文档化 VSCode 流程 | 不自托管 $150/mo 起;自托管免套餐费但要运维 | **自托管/数据主权/合规**选项 |
| Modal / Cloudflare | 函数/容器优先 | 部分支持/DIY | — | 本场景不如上两者对口 |

**决策**:两个 provider 都实现,**前端按项目/企业选择**(env 只作默认/兜底);Fly = 推荐(原生 SSH、便宜),E2B = 自托管/合规终局方案。

## 10. 架构与模块(`backend/src/platform/scope_sandbox/`)

当前统一位于 `platform/scope_sandbox`:长驻 workspace provider 与一次性
`execution` mode 生命周期不同，但共享一个产品边界和执行策略:

- **`provider.py`** — `SandboxProvider` ABC:`capabilities / create / start / stop / destroy / status / exec / extend`;`SandboxState` 三态枚举、`SandboxSpec`、`ConnectionInfo`(host/port/username/**proxy_command**)、`ProviderCapabilities`(声明是否支持 stop-留盘 / 原生 TCP / 可自托管)。
- **`policy.py`** — 纯函数会话策略:`decide()`(RUNNING-idle→STOP、STOPPED-长idle→DESTROY)、`adaptive_stop_timeout()`(按最近用户数/拉取成本/热度延长保温)、`eviction_score()` + `select_for_eviction()`(容量压力下淘汰最低价值会话)。完全可单测。
- **`registry.py`** — `SandboxSession`(scope/用户/活动/时间/连接/拉取成本等)+ `SandboxSessionStore` Protocol + 内存实现。
- **`supabase_store.py`** + 迁移 `20260607000000_scope_sandbox_sessions.sql` — 持久 session(一行一 scope,epoch 浮点时间),多 worker/重启不丢(roadmap #3)。
- **`manager.py`** — `ScopeSandboxManager`:`acquire`(报告 REUSED/RESUMED/CREATED——量化保温价值)、`touch`、`release`、`reap`、`revoke_user`、`kill_scope`、`evict_to_capacity`;per-scope 异步锁防并发双创。
- **`fly_provider.py` / `e2b_provider.py`** — 两个 provider 实现。
- **`factory.py`** — provider/store 按配置选择;**`reaper.py`** — 回收循环(待接入 app 调度)。

## 11. E2B 实现 + SSH 连接打通 + 实环境测试(本块重头)

**Provider**:`E2BProvider` 包 e2b SDK(同步 SDK 全程 `asyncio.to_thread` 卸载)。修正了 SDK 真实调用——**SDK 无 `resume`,`Sandbox.connect(id)` 即 resume** 一个 paused 箱;`pause/kill/get_info` 是按 id 的类方法变体(`7925025c`)。

**SSH(`ssh_e2b.py`)**:E2B 无原生 TCP ingress → 自建隧道:
- sandbox 内 **sshd:22** + **websocat 转发**(`ws-l:8081 → tcp:127.0.0.1:22`);
- 客户端用 websocat 当 SSH `ProxyCommand`,经 E2B 公网 wss 代理 `wss://8081-<id>.e2b.app` 连入;
- VSCode Remote-SSH 用同一 `ProxyCommand`(`vscode_ssh_config_block` 生成 `~/.ssh/config` 块)。
- **踩坑**:websocat 转发器必须 `nohup … &` 脱离(`background=True` 在 SDK 断开时会被杀,导致 502 Bad Gateway)。

**实环境验证(2026-06-07,真连)**:
- create ~1s、exec 亚秒、pause/resume 亚秒;**工作副本跨 stop/resume 存活**;
- 本地 `ssh` + VSCode Remote-SSH 路径**实连成功**并执行命令;
- **真实 PuppyOne git 往返**:从线上 repo clone 项目进 sandbox → 看到最初文件 → 在箱内改动 push → 对端 pull 看到改动;
- **多用户协同实测**(`sandbox-collab-session-results`):发现 **PuppyOne git remote 强制线性历史**(拒非 ff + 拒 merge commit)→ 协同**必须 `git pull --rebase`**;固化为 provision 默认 `git config pull.rebase true`,rebase 工作流收敛、无数据丢失。

**Roadmap #1–#4 落地**(`cdf72ab6` / `29579df4`):
- **#1** in-sandbox provision(clone scope + rebase 默认 + per-scope git 身份)+ manager bootstrap;
- **#2** E2B **超时续期 `extend`** + acquire **reconcile**(修 E2B 到 timeout 自动 kill / 会话漂移:idle 超阈值才校验 provider 真实状态);
- **#3** Supabase 持久 store;
- **#4** reaper 回收循环(单测;调度接入待 app manager 单例)。

## 12. #5+#7 SSH 短期凭证 + per-user 身份(治理核心,实环境验证)

**`ssh_credentials.py`**(`1b66bedb`):
- 用户的 public key 进 `authorized_keys`,带 `puppyone:user=<id>` 标签 + OpenSSH **`expiry-time` 原生 TTL**;
- **grant** = 加行/续期 · **revoke** = 删行(**离职即失权**)· **过期** = sshd 自动拒;
- 纯 helper(`authorized_key_line / strip_user_lines / upsert_user_line / granted_users / format_expiry`)+ 运行时 grant/revoke/`provision_user_workspace`(走 `provider.exec`,base64 read-modify-write);
- **#7**:per-user working tree `~/<user_id>` + per-user git 身份 → push 归属到真人;
- manager **`revoke_hook`** 接入 `revoke_user`,离职时连 SSH 一并撤(best-effort,失败也照样摘除追踪)。

**⚠️ 安全发现 + 修复(关键)**:E2B 默认模板的 **systemd socket-activated sshd 接受 SSH `none` 认证方法** —— 客户端**无需任何密钥**即可登录(`Authenticated using "none"`),`authorized_keys` 形同虚设,会**静默瓦解整个凭证治理**。修复 `ssh_e2b`:先 `systemctl stop ssh.socket ssh.service` + `pkill sshd` **释放 :22**,再启动我们自己的 **publickey-only sshd**(`AuthenticationMethods publickey` / `UsePAM no` / `PasswordAuthentication no` / `PermitEmptyPasswords no`)。两个踩坑:① 默认 sshd 由 systemd socket 占着 :22,我们的 `sshd -f` 静默绑不上 → 必须先释放;② 停服务会清掉 `/run/sshd` privsep 目录,启动前要重建。

**实测**(`scripts/ssh_credentials_live.py`,真连真 sshd):`valid grant → 可连`、`expired grant → 拒`、`revoke → 拒`,独立 bootstrap key **全程可连** → 证明"拒绝是 per-key,而非 sshd 坏了"。

## 13. Fly 版本代码跟进(只写不测,#10 代码)

按设计文档补齐 Fly 侧代码(**不实测**,待绑支付 + 专用 IPv4):

- **`FlyMachinesProvider.exec`**(`dca2246a`):走 Fly Machines exec API,**以 SSH 用户身份**(`su - <user> -c`)运行,使 `~`/属主与 VSCode 登录账号一致 → `scope_provision` + `ssh_credentials` 在 Fly 上**原样复用**;非零退出抛错(对齐 E2B)。
- **镜像 `sandbox/scope-fly/`**:Dockerfile **烤入** publickey-only sshd@2222(与 E2B 实测倒逼出的同款硬化)+ git/CLI + 免密 sudo 的 `puppy` 用户;README(专用 IPv4 设置、build/push、provider 切换)+ 参考 `fly.toml`(22→2222 机器形态)。
- Fly **原生 raw TCP**,**无需 websocat**,`ConnectionInfo.proxy_command=None`;sshd 烤进镜像(非运行时硬化)。

## 14. 复查(bug / 死代码 / legacy)+ 修复

对全模块做了一轮审计:

- **实 bug(`39e76e14`)**:`E2BProvider` 默认 `ConnectionInfo.username="puppy"`,但 **E2B 账号是 `user`**(`/home/user` 下放 authorized_keys 与工作区)→ 经 `ConnectionInfo` 组的 VSCode Remote-SSH 会用 `User puppy` 连失败(demo 因直接传 `user="user"` 绕过未暴露)。改默认 `"user"`,加回归测试。
- **加固三连(`daa9b009`)**:
  1. **撤回不能鍵**:`provision_e2b_ssh` 的 `public_key` 改**可选**——本番留 None(仅建文件),authorized_keys 全交凭证层管理,**每把鍵都可撤销/短期**;传鍵仅用于 admin break-glass / 单用户 demo;
  2. **`expiry-time` 时区依赖**:`format_expiry` 加 **`Z` 后缀(UTC 明示)**——OpenSSH 默认按箱**本地时区**解析裸时间戳,`Z` 钉死 UTC 使 TTL 与镜像 TZ 无关;**实 sshd(9.1p1)验证 honor**:valid-Z→可、expired-Z→拒;
  3. **默认 provider** 改 `e2b`(已验证路径),避免未选择的项目静默落到未实连的 Fly。
- **未配线的 seam(意图性,非有害死代码,保留)**:`factory.store_from_settings`、`scope_provision.provision_scope_workspace`、`reaper.start_reaper`、`manager.record_pull_cost/evict_to_capacity/touch` —— 均为 **#9(HTTP API + 前端选择)将配线的公开 API**,现仅无调用方。
- **收敛完成(2026-07-11)**:`infra/sandbox` 已退役；agent/endpoint 的一次性执行迁入 `scope_sandbox/execution`，每请求写回并销毁，长驻 workspace 默认使用 Supabase session store。
- **本地清理**:删除遗留的 `~/.ssh/config` puppy-e2b 块与 `.e2b_ssh_test/`,保留 websocat + flyctl;**线上测试项目按用户要求保留**。

## 15. 提交清单(第二部分)

| 提交 | 主题 | 类别 |
|---|---|---|
| `b85faf28` | 两个可选 provider(Fly + E2B)+ 会话管理骨架 | 架构 |
| `62b29fca` | 设计 + provider 调研文档 | 文档 |
| `7925025c` | 修正 `SdkE2BClient` 对齐真实 e2b SDK + exec(实证) | E2B |
| `1f8255e0` | 实环境验证结果 + 代码分析文档 | 文档 |
| `c0fdaf6d` | E2B SSH provision(sshd + websocat)— VSCode 实连验证 | E2B SSH |
| `569c6a78` | 标记 E2B SSH 已验证 | 文档 |
| `4032ad77` | 多用户协同 + 会话管理实测结果 | 文档 |
| `cdf72ab6` | in-sandbox provision + E2B 续期/reconcile + reaper(#1/#2/#4) | Roadmap |
| `29579df4` | Supabase 持久 session store(#3) | Roadmap |
| `9574b57a` | 标记 roadmap #1–#4 完成 | 文档 |
| `1b66bedb` | per-user 短期/可撤销 SSH 凭证(#5/#7)+ sshd 硬化 | 治理核心 |
| `dca2246a` | Fly provider exec + 烤入式 publickey-only 镜像(#10 代码) | Fly |
| `39e76e14` | 修复 E2B SSH 登录名应为 `user` | bug 修复 |
| `daa9b009` | 关闭 3 个 SSH 治理加固缺口(撤回鍵/TZ/默认 provider) | 加固 |

## 16. 测试与验证总结(第二部分)

- **单测**:`backend/tests/platform/scope_sandbox/` **97 全绿**(policy/manager/两 provider/factory/scope_provision/ssh_e2b/ssh_credentials/reaper/supabase_store)。
- **实环境(真 E2B + 真 sshd)**:全链路 create/exec/pause/resume/kill;SSH(VSCode Remote-SSH 路径);真实 PuppyOne git 往返;多用户 rebase 协同;**SSH 凭证 grant→expiry→revoke 多次端到端验证(含 `Z` 后缀复验)**。
- **Fly**:代码完成、**未实连**(待支付 + 专用 IPv4)。

## 17. 待办 / 边界(第二部分)

1. **#6** 自定义 E2B 模板(烤入 sshd+websocat,免每次运行时硬化/安装,启动 6s→~1s);
2. **#8** 可观测(写 `sync_runs` / 修 GAP-8)+ 用真实数据调 session 策略阈值(现为静态默认);
3. **#9** HTTP API + 前端 provider 选择 + 项目设置存储 —— 配线现有 seam(`provider_from_settings`/`store_from_settings`/`provision_scope_workspace`/reaper 调度);
4. **#10** Fly **实连**(绑支付 + `fly ips allocate-v4` + 推镜像后跑一轮基准:创建→exec→SSH 往返);
5. **#11 已完成**:`infra/sandbox` 退役，endpoint 作为统一 subsystem 的 execution mode 保留;
6. **跨实例同 scope 竞争**:Supabase store 现为 last-write-wins + 进程内锁,多 writer 生产前需行锁/乐观版本;
7. **合并**:同属 `feat/context-entrypoints`,未合并。

---
---

# 第三部分:Sandbox 前端接线(产品化最后一公里,2026-06-08)

> 历史记录说明：本节记录的是 2026-06 当时的 key-in-path 实现。当前 Sandbox
> 已改为 canonical Project/Scope locator + 短期 Git HTTP credential，旧
> `/git/ap/<key>.git` 仅为有界兼容路由。现行契约见
> `docs/architecture/05-git-remote-accesspoint.md`。

> 之前 sandbox 功能是「后端库就绪 + 实环境验证,但前端只有 "Coming soon" 占位」。这一部分把它接成用户能点的真实流程:Access 页 scope 卡片直接连 SSH,Data 菜单的 "SSH Terminal" 也通了。配套补齐了后端 HTTP API(前端要调它)+ reaper 调度 + 多 writer 安全,并合并了 qubits 最新代码。

## 18. 后端 HTTP API(前端契约,#9 backend)

前端无端点可调,所以先补后端契约层(`b85faf28` 之后新增):
- **`scope_sandbox/service.py`** `ScopeSandboxService` —— 进程级 manager(按 provider 缓存,共享持久 store),把 `scope → /git/ap/<access_key>.git` 解析好,编排 connect:acquire/复用 scope 的 sandbox(冷启动 bootstrap = E2B 装 sshd + clone scope)→ 建 per-user working tree + git 身份 → 签发短期可撤销 SSH 密钥 → 返回连接信息 + 可粘贴的 VSCode Remote-SSH config 块。
- **`scope_sandbox/router.py`** —— `POST /api/v1/scope-sandboxes/connect`、`GET /status`、`POST /revoke`,JWT + 项目鉴权,provider 按请求选择(默认 `settings.SCOPE_SANDBOX_PROVIDER`)。
- 注册进 `main.py`。**单测**:service 流程(E2B proxy / Fly direct TCP、复用、status、revoke)+ router(鉴权/信封/解析),fake 无实连。

## 19. 前端接线(本次「前端改动」主体)

**API client** —— `frontend/lib/scopeSandboxApi.ts`:
- `connectScopeSandbox` / `getScopeSandboxStatus` / `revokeScopeSandbox` 三个类型化函数,打 `/api/v1/scope-sandboxes/*`(`apiClient` 自动拆 `{code,message,data}` 信封 → 返回 `data`)。

**Access 页 scope 级卡片** —— `access/components/SandboxConnectCard.tsx`(注入 `ScopeDetailPanel` 连接器列表下方):
- 这是一个 **scope 级卡片**(不是 connector 行 —— sandbox 会话按 scope keyed,access_surfaces 里没有它的行);
- 流程:选 provider(E2B 默认 / Fly 标 "needs setup")→ **粘贴 SSH 公钥**(私钥永不离开本机)→ Connect;
- 成功后展示:连接信息(host/port/user/过期时间)、**可粘贴的 `~/.ssh/config` 块**、`code --remote …` 提示、websocat 提示(E2B 隧道)、**Revoke 按钮**;
- 用 SWR 拉实时 status;复用现有 `ui-blocks`(`KvBlock`/`CommandBlock`/`SectionLabel`)+ tokens,风格与其它 access 卡一致。

**Data 菜单 "SSH Terminal"** —— 从 disabled "Coming soon" 变成真实入口:
- 点击路由到 Access 页 `?remote=ssh&path=<folder>`,Access 页**按 folder 预选对应 scope**(root/first 兜底),用户直接落到该 scope 的 Remote Dev (SSH) 卡片;
- 新 `onCreateSshTerminal` action 串过 `useDataCreateFlow → DataPageOverlays → CreateMenu`。

**验证**:前端 `tsc --noEmit` **0 错误**。

## 20. 配套后端:reaper 调度 + 多 writer 安全(#3)

- **reaper 进 app**:`manager.reap(only_provider=…)`(共享 store 混 provider 各自回收)+ `ScopeSandboxService.reap()`(扫所有 provider)+ `main.py` lifespan 启停,由 **`SCOPE_SANDBOX_REAPER_ENABLED`(默认关,会真实 stop/destroy 计费)+ `_INTERVAL_S`** 控制。
- **跨实例 create 竞争**:`store.insert()` 原子创建(InMemory dict;Supabase 靠 `scope_id` PK → 23505 唯一冲突 → False);`manager._create_session` 对全新 scope 用 insert 抢占,输了就销毁自己刚建的重复箱、改用赢家(reuse/resume)—— 不留双活、不泄漏;reconcile 重建路径(已持锁、行已存在)仍 upsert,带外 kill 恢复不受影响。

## 21. 合并 qubits 最新代码

`origin/qubits`(领先 17 commit)合并进 `feat/context-entrypoints`:
- **唯一真冲突 `main.py`**:qubits 在 "local access" 改造里**删了整个 filesystem connector**(连带删了 `filesystem_router` 注册),我这边在它后面加了 `scope_sandbox_router`。**解决**:尊重 qubits 的删除(留着那个 import 会 ImportError),只保留 `scope_sandbox_router` 注册;其余 5 个重叠文件自动合并,我的 SSH 卡片/菜单/reaper/config 全部保留。
- **关键依赖未受影响**:service clone 用的 `/git/ap/<key>.git` 仍由 `version_engine` git 协议路由(prefix `/git`)提供。
- 合并后:**116 个 scope_sandbox 后端测试全绿**、前端 `tsc` 0 错误、无残留 filesystem 引用。

## 22. 提交清单(第三部分)

| 提交 | 主题 | 类别 |
|---|---|---|
| `d79fb76e` | scope-sandbox HTTP API(#9 backend)connect/status/revoke | 后端契约 |
| `c937e520` | Access 页 Remote Dev (SSH) 卡片(#9 前端) | **前端** |
| `c8736f57` | 启用 Data 菜单 "SSH Terminal" → 路由到 scope SSH 卡片(#2) | **前端** |
| `a12be6ce` | reaper 调度 + 多 writer create-race 安全(#3) | 后端 |
| `bb335886` | 合并 origin/qubits(解 main.py 冲突) | 合并 |

## 23. 现状与剩余

- **可用闭环**:用户在 Access 页 scope 卡片(或 Data 菜单 "SSH Terminal")粘贴公钥 → Connect → 拿到 VSCode Remote-SSH config → 连入;Revoke 可撤。后端 116 测试绿,前端 0 类型错误。
- **剩余**:
  1. **#1 实连验证**:启动后端、真账号点 Connect → E2B 真起箱 → VSCode 连入(会计费,未擅自跑);
  2. **#10 Fly 实连**(待绑支付 + IPv4);
  3. **与 qubits "local access" 模型的语义对齐复核**:本次合并在类型/测试层面兼容,但前端旧的 `sandboxEndpointsApi.ts` / `quick-connect.tsx` 里的 legacy sandbox/filesystem 分支是否需要跟进,待扫一轮;
  4. **#6 自定义 E2B 模板** / **#8 可观测+调参** / **#11 legacy 退役**。
