# V2 Star 架构 Gap 分析（2026-05-31）

基于对全部架构文档（`docs/architecture/`、`docs/proposals/`）与实际实现的逐层交叉核查。
每条 Gap 均有代码级证据，不是推测。

---

## 快速索引

| ID | 层 | 严重性 | 一句话描述 |
|---|---|---|---|
| GAP-1 | L0/L1 | MEDIUM | upload-pack 把 clone pack 全量加载进 Python 堆 ✅ 已修复 |
| GAP-2 | L0/L6 | MEDIUM | GC 只扫 loose 对象，bundle/chunked 对象永不回收 ✅ 已修复 |
| GAP-3 | L1 | **LARGE** | 只支持 `refs/heads/main`，无分支/PR/tag 🟡 Phase 1 已实现（branch/tag push+advertise+serve；merge/PR 待 Phase 2） |
| GAP-4 | L1/L3 | **LARGE** | 嵌套 Scope `carved_excludes` 隔离 ✅ 已修复（admission git/CLI + MCP scoped_fs 读路径均自动 carve 子 scope；root 为项目全局视图不 carve） |
| GAP-5 | L5 follow-up | MEDIUM | child-scope merge 全量下载 subtree blob（O(N×S)） ✅ 已修复 |
| GAP-6 | L5 follow-up | MEDIUM | text index 删文件不清行；dedup key 退化为非内容寻址 ✅ 已修复 |
| GAP-7 | L5/L6 | SMALL | DB rename `mut_*`→`version_*` Phase 2/3 未做 |
| GAP-8 | L4 | SMALL | Agent/MCP/Sandbox AP 用量在 dashboard 永远为 0 ✅ 已修复（sync+agent；mcp/sandbox 无运行日志源） |
| GAP-9 | L4 | SMALL | Filesystem connector `fetch()`/`push()` NotImplementedError ✅ 已修复 |
| GAP-10 | L5 follow-up | SMALL | Shadow snapshot 无 TTL reaper，无限积累 ✅ 已修复 |
| GAP-11 | L5 follow-up | MEDIUM | `--ref local:` shadow grep 完全未实现 ✅ 已修复（V1：grep previews） |
| GAP-12 | L2 frontend | MEDIUM | PUP-3 策略仅覆盖 FileImportDialog；drag-drop 路径无过滤 ✅ 已修复 |
| GAP-13 | L5 core | MEDIUM | PUP-5 staged session / PR-like review 无后端原语 |
| GAP-14 | L1 | MEDIUM | 同 GAP-1，upload-pack 内存风险具体说明 ✅ 已修复 |
| GAP-15 | L6 | SMALL | `count()` 做全量 S3 LIST，O(n_objects) ✅ 已修复（bytes 不再恒为 0） |

---

## LARGE Gaps（整个子系统缺失）

### GAP-3：只支持单一 `refs/heads/main`，无分支/tag

**层**：L1 Git transport  
**文档来源**：`01-version-engine.md` §5, `05-git-remote-accesspoint.md`

**文档约定**：scope remote 暴露正常 Git 分支视图；feature-branch 和 tag 作为未来层规划，
`receive_pack.py` 注释中明确标注"PuppyOne does not yet persist separate Git branch refs"。

**实际代码**：`receive_pack.py` → `_ref_writability()` 硬 reject 所有非 `refs/heads/main` 的 ref，
tag 一律拒绝。只有 flat main push 被接受。

**影响**：完整 Git 工作流（feature branch、merge request、tag release）完全缺失。
这是与真实 Git 托管服务最大的功能差距。

**实现前提**：
- DB 需要 per-scope / per-AP ref store（`version_refs` 表或类似）
- branch 推送需路由到对应 scope 历史而非直接覆盖 root
- PR/merge 需 conflict resolution 原语
- 估计工作量：**大型功能（weeks）**，需独立设计文档

---

### GAP-4：嵌套 Scope `carved_excludes` 隔离未实现

**层**：L1/L3 admission  
**文档来源**：`01-version-engine.md` §"嵌套 Scope 拓扑"

**文档约定**：
```
父 Scope A 看不到 /A/C/*（已声明的子 scope 在父视图里自动隐藏）
准入层须计算 carved_excludes_for(scope_path)，枚举声明的子 scope 路径，
注入 TargetAdmission.scope_excludes
```
文档表格明确说明：父 scope push 触碰子 scope 路径 → admission-time reject。

**实际代码**：
- `repo_facade_from_auth()` 只读 `scope.get("exclude")`（用户手配）
- `admission/permission.py` 无任何 `carved_excludes_for` 函数
- `receive_pack.py` → `_excluded_changed_paths()` 只用 `scope_excludes`，不自动枚举子 scope
- **结果**：父 scope git push 可以写进子 scope 目录，数据隔离合约未执行

**影响**：多 scope 项目数据隔离的核心合约完全未执行。
父写子、子读兄弟均无防护（仅依赖用户手配 exclude）。

**实现**：
- 获取 project 下所有 scope 列表（DB 已有）
- 对当前 scope_path，过滤出 path 以 `{scope_path}/` 开头的子 scope
- 将这些路径加入 `scope_excludes` → 透传 admission 检查
- 估计工作量：**中等（days）**，改动集中在 admission 层

---

## MEDIUM Gaps

### GAP-12：PUP-3 上传策略漏了 drag-drop 路径（安全问题）

**层**：L2 frontend  
**文档来源**：`PUP-3-folder-upload-policy.md` §1 修复清单

**文档约定**：`dropFiles.ts` 的 `materializeEntry()` 和 `useExternalFileDropCatcher.ts`
都须接入 `applyPolicy(files, ...)`。

**实际代码**：
- `FileImportDialog.tsx` ✅ 正确接了 `applyPolicy`
- `frontend/lib/dropFiles.ts` ❌ 无 `uploadPolicy` import，无限递归 `FileSystemDirectoryEntry`
- `frontend/lib/hooks/useExternalFileDropCatcher.ts` ❌ 同样无过滤

**影响**：用户把本地 git 仓库拖进页面 → `.git/config`（含 remote URL + credential）、
`.env`、`node_modules` 全量进入 object store。PUP-3 的 P0 安全动因只修了 dialog 路径。

**实现**：在 `dropFiles.ts` 的 `materializeEntry` 返回文件列表后调 `applyPolicy`，
在 `useExternalFileDropCatcher.ts` 同样接入。改动 < 20 行。

---

### GAP-6：text index 删文件不清行；dedup key 退化

**层**：L5 follow-up  
**文档来源**：`PUP-cloud-grep.md` §7

**实际代码**：
- `text_indexer.py` → `index_commit_delta()` 只处理 `action in (None, "add", "update")`，
  delete 事件跳过；`TextIndexRepository` 无 delete 方法
- dedup key：无 `content_hash` 时退化为 `{commit_id}:{path}`，每个 commit 都是唯一 key，
  破坏 `UNIQUE(project_id, content_hash, chunk_idx)` 的跨 commit 去重

**影响**：grep 命中越来越多已删文件；dedup 无效，index 行数随 commit 数线性增长。

---

### GAP-2：GC 只扫 loose 对象，bundle/chunked 不可见

**层**：L0/L6  
**实际代码**：`S3StorageBackend.all_hashes()` 只列 `version/{project}/objects/`，
不枚举 `version/{project}/object-bundles/`。批量写走 bundle 路径，GC 基本无效。

---

### GAP-5：child-scope merge 全量 S3 下载（性能）

**层**：L5 follow-up  
**实际代码**：`hooks.py` → `_merge_project_root_delta_into_child_scope()` 调
`flatten_tree_to_bytes` × 3（old/new/current subtree），每次下载所有 blob。
在请求路径同步执行，N 个子 scope × 大 scope = 多秒延迟。
文档明确标注"intended optimization = tree-diff + path-patch"。

---

### GAP-11：shadow snapshot `--ref local:` grep 未实现

**层**：L5 follow-up  
**文档来源**：`08-shadow-snapshots.md` §1  
**影响**：shadow snapshot 的核心产品能力（云端 agent 对队友未 push 工作树 grep）完全缺失。

---

### GAP-1/14：upload-pack 把 clone pack 全量加载进 Python 堆

**层**：L0/L1  
**实际代码**：`router.py` → `git_upload_pack` 用 `await request.body()` 全量内存。
receive-pack 已做磁盘 spool，upload-pack 未做。大仓 clone 可能耗尽服务器内存。

---

### GAP-13：PUP-5 staged session / PR-like review 无后端原语

**层**：L5 core  
**文档来源**：`PUP-5-needs-action-design.md` §9 显式 defer  
**影响**：需要 `staged_commits` 表和 draft-pending-land 状态机；
两类 NeedsAction kind 的整个 backend primitive 缺失。

---

## SMALL Gaps

### GAP-7：DB rename `mut_*`→`version_*` Phase 2/3 未做
`db_names.py` 全部仍是 `mut_*`。Phase 1（additive views）已做，Phase 2（实际 rename）和
Phase 3（drop compat）待执行。代码层干净（单文件配置），纯运营操作。

### GAP-8：Agent/MCP/Sandbox AP 用量 dashboard 永远 0
`dashboard_router.py` 注释："APs don't write to sync_runs yet, so they get zero buckets."

### GAP-9：Filesystem connector NotImplementedError
`FilesystemConnector.fetch()`/`push()` raise NotImplementedError。设计如此（数据面在 Git/AP-FS），
但违反 `BaseConnector` 契约。

### GAP-10：Shadow snapshot 无 TTL reaper
S3 `shadow-snapshots/` 前缀和 DB 表无限积累。sandbox_reaper 存在但 snapshot 无对应 job。

### GAP-15：`count()` 全量 S3 LIST，O(n_objects)
`S3StorageBackend.count()` 调 `all_hashes()` 全量遍历，第二返回值（bytes）永远为 0。

---

## 实现优先级

```
立即（安全 + 正确性）
  ✅ GAP-12  dropFiles.ts 接入 applyPolicy（< 1 day）
  ✅ GAP-6   text index 删除事件 + content_hash dedup 修正（~1 day）

高（架构合约）
  ✅ GAP-4   nested scope carved_excludes admission（~2–3 days）
  ✅ GAP-2   GC 枚举 bundle/chunked 对象（~1 day）

中（性能）
  ✅ GAP-5   child-scope merge 改 OID 级合并（消除 blob 下载）
  ✅ GAP-1   upload-pack 流式响应 + 请求体磁盘 spool

  ✅ GAP-11  shadow grep --ref local:（V1：server-side grep over previews）

已完成 SMALL
  ✅ GAP-8 (dashboard 用量桶：sync+agent)、✅ GAP-9 (FS connector fetch skip)、
  ✅ GAP-10 (shadow snapshot TTL reaper)、✅ GAP-15 (count() bytes 修正)

Roadmap（大型功能 — 仍待排期）
  GAP-3   multi-branch Git refs（独立设计文档，weeks；已写设计草案 + 改进拒绝信息）
  GAP-13  staged commits / PR-like review（独立设计文档）

剩余 SMALL（纯运营，需 DB 迁移协调）
  GAP-7 (DB rename mut_*→version_* Phase 2/3)
```

---

*文档由代码级 audit 生成，每条均有 `backend/src/` 内的具体文件和行号支撑。*
*最后更新：2026-05-31*
