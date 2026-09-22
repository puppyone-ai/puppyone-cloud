# Project 切换无反馈与读取延迟调查

调查对象：`puppyone-cloud` 当前工作树、运行中的 Next.js 15.5.9 开发服务和 FastAPI 服务。运行日志样本为 2026-09-19 23:20–23:58（Asia/Singapore）。

状态：**调查记录，不是修复完成或性能验收报告。** 本轮未修改业务实现、数据库、权限策略或桌面端。临时浏览器诊断代码已移除。

## 结论与证据等级

1. **源码确认：项目切换缺少路由提交前的反馈。** 点击调用 `router.push`，选中样式仅取决于已提交路由。路由等待期间仍显示原项目，没有独立的 pending 状态。
2. **真实日志确认：后端基础读取耗时数秒。** 多次串行远程 PostgREST 访问、重复项目读取和基础元数据附带入口统计，共同增加请求延迟。
3. **尚未锁定：“一直点击仍不切换”的具体前端根因。** 当前没有完整的同一次点击 → 路由请求 → React commit 关联记录。不能把数秒的数据读取，直接当作 URL 和选中项目始终不变的原因；也不能宣称 React 状态死循环、遮罩挡点击或数据库故障已经得到证实。

## 1. 前端实际调用链

`components/sidebar/WorkspaceProjectRail.tsx:58` 的点击顺序：

```text
button click
  → setProjectsOpen(false)
  → rememberLastProject(projectId)
  → router.push('/projects/{id}/data')
  → App Router 提交新路由
  → (main)/layout 的 useSelectedLayoutSegments 更新 activeBaseId
  → 侧栏选中项目更新，目标 Project 子树读取数据
```

点击处理函数没有等待项目详情 API、目录 API 或数据库。`rememberLastProject` 捕获本地存储异常，不会因禁用 localStorage 阻止后续导航。

侧栏 `active` 判断在 `WorkspaceProjectRail.tsx:193`，只比较 `project.id === activeProjectId`。因此路由尚未提交时没有“切换中”反馈，这是可确定的交互缺口，但不能单凭源码认定它就是永久不切换的根因。

`[projectId]/loading.tsx` 有 Suspense fallback，但它不能保证用户点击的同一时刻 URL 必然变化。该文件现有注释把这一点说得过于绝对。SWR 数据在客户端挂载后读取，与路由模块加载是不同阶段。

当前 Project 详情和目录读取是独立通道：`(main)/layout.tsx` 的 `useProject` 不会先等待项目详情再允许挂载所有文件页面。不能把几个并发 API 的耗时简单相加成“总切换耗时”。

文件夹/文件内部导航由 `useDataRouteController` 的本地状态和 `history.pushState` 执行；跨 Project 由 App Router 执行。需要在运行时验证两者衔接，但目前没有证据说明调用 `pushState` 本身导致本次故障。

## 2. 后端真实请求样本

来源：`backend/logs/app.log` 中 RequestContextMiddleware 的 `request_id` / `latency_ms`。只统计样本窗口内已完成请求，排除 307 重定向。以下是开发环境观察值，样本很小，**不是生产 p95，也不是受控基准**。

| 接口 | 样本数 | 中位耗时 | 最大耗时 |
| --- | ---: | ---: | ---: |
| Project 列表 | 4 | 3.22 s | 5.79 s |
| Project 详情 | 4 | 4.71 s | 5.61 s |
| 目录 ls | 8 | 3.05 s | 4.34 s |
| Agent 配置列表 | 5 | 2.86 s | 6.76 s |
| Project access-point | 6 | 3.97 s | 8.57 s |

### 目录读取样本

请求 ID：`825d8473-767a-46b4-aafa-07b973b6910b`。结束时间 23:52:07.192，总耗时 **2934.76 ms**。

| 顺序 | 远程表访问 | 相对请求开始的完成时间 |
| --- | --- | ---: |
| 1 | projects | 1078.8 ms |
| 2 | org_members | 1524.4 ms |
| 3 | project_members | 1999.3 ms |
| 4 | projects | 2457.1 ms |
| 5 | version_commits | 2933.4 ms |

日志只有远程调用完成时间，不能把相邻时间差当成纯 SQL 执行时间；其中还可能包含应用处理和排队。源码确认了这些调用的串行关系：授权先取三组 facts，读取内容和 head 再进入版本读取链。

目录内容来自 Git-native Version Engine/tree objects；以上不是“从 content_nodes 表读所有文件”。该样本不能推断对象缓存冷启动成本或目录越大就必然越慢。

### 项目详情样本

请求 ID：`63c2d406-cb76-483f-a4ff-12528164fbf5`。结束时间 23:52:09.189，总耗时 **4941.64 ms**。

| 顺序 | 远程表访问 | 相对请求开始的完成时间 |
| --- | --- | ---: |
| 1 | projects | 522.8 ms |
| 2 | org_members | 1089.2 ms |
| 3 | project_members | 1564.4 ms |
| 4 | projects | 2045.9 ms |
| 5 | connections | 2898.1 ms |
| 6 | access_surfaces | 3451.8 ms |

最后一个可见数据库事件到请求结束还有约 1.49 秒，现有日志不能精确归因，需分阶段 span 才能区分后处理、排队等原因。

对应源码：

- `backend/src/platform/authorization/repository.py:41`：`load_project_facts` 串行读取 projects、org_members、project_members。
- `backend/src/platform/authorization/dependencies.py:40`：授权后 `project_repository.get_by_id` 再取一次项目。
- `backend/src/platform/project/router.py:221`：单项目详情无条件调用 `_count_user_access_points`。
- `backend/src/platform/project/router.py:83`：入口统计继续查询 connections 和 access_surfaces。
- `backend/src/version_engine/entrypoints/http/content_read.py:149`：ls 先授权、读目录，再取 head。

Project 列表已有批量授权；列表接口也已支持 `include_access_counts=false`。**单项目详情仍始终统计入口**，不能把列表优化当作详情也已优化。

授权服务按请求创建，FastAPI 在同一请求中复用依赖。不同 API 各自重新授权，所以多个装饰性接口会放大远程访问总量。不能用长期权限缓存掩盖这个成本，避免成员权限撤销后继续访问。

## 3. 前端后台读取放大

`frontend/features/files/useFileWorkspaceQueries.ts` 先读根目录，根目录成功后启用 tools、sync、MCP、sandbox、scopes、connectors、identity 七类元数据读取。AgentProvider 另有配置读取。

这些请求不是全部阻塞文件首屏，也不是在遍历所有项目，但会增加后台并发和授权查询量。需要按真实可见性和数据用途继续削减；在补队列测量前，不认定浏览器连接池或线程池已耗尽。

## 4. 本次未能确认的前端环节

Next `.next/trace` 中目标 Project RSC 路由有约 193 ms、3.33 s 的完成记录，也有诊断期间约 15.26 s 的开发服务记录。它们不能替代浏览器 click-to-commit 测量，重复 handle-request span 也不应一律当作两个独立网络请求。

本机 Next.js 15.5.9 的 `createPrefetchURL` 在 development 返回 null；因此不能指望侧栏 `onMouseEnter → router.prefetch` 在当前开发模式消除冷编译等待。生产预取效果需另测。

浏览器中观察到原项目仍显示，但受控点击多次被工具以“用户切换了应用/窗口”为由拒绝；原标签与新加载标签也没有得到同一条完整的点击诊断链。临时探针只获得挂载/路由初始提交记录，未取得可关联的项目点击记录。因此没有把工具点击失败当作应用点击处理器失败，也没有据此宣称已复现 React 状态故障。

旧页面存在大量 WebSocket 错误以及 Files href 与已显示路径不一致的观察。这些需要运行时跟踪，尚未证明与 Project 导航失败存在因果关系。

此前性能审计发现的空数组 effect 循环和部分 async 路由同步 I/O 阻塞，在当前工作树已有修复；不能用旧审计结论直接代替本次诊断。当前 Project/ls 读取为同步 def 路由，不能称其阻塞事件循环。

## 5. 下一步应如何定位和修复

顺序按因果关系安排，不先更换框架或数据库：

1. 在一个加载当前代码的受控标签内，为同一次项目点击关联 `click → route request → route commit → metadata/ls completion`，同时采样 long tasks。分别测试空项目、普通文件打开时、Chat 打开时，以及冷/热切换。URL 不变时优先检查 route transition/异常/未提交渲染，URL 已变时检查数据读取。
2. 为跨 Project 导航补明确 pending 状态和失败恢复，快速连续点击以最后一次意图为准。导航提交前不能把旧项目内容标成新项目。存在编辑草稿时，统一处理确认和恢复；不能只加一个假选中效果。
3. 把 Project 轻量元数据与入口数量统计分开；复用已授权项目读取结果，消除同一请求重复查询。随后按现有 AuthorizationService 边界聚合 facts 与所需 root/head 读取，保持权限检查和版本快照一致性。
4. 装饰性元数据按消费者需求加载；保留共享 query key、取消过时读取和明确的缓存失效语义。增加授权、远程数据库、对象存储、排队分段耗时，再决定是否需要 SQL/索引或部署地域调整。
5. 在生产构建中比较修复前后点击反馈、路由提交、首目录可用时间与请求数；开发冷编译和热更新时间单独记录。

本轮结论可以支持第 2–4 项的工作设计，但尚不足以声称它们将解决所有“点击完全没反应”的情况。
