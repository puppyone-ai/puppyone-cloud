# Web 工作区性能审计 — 2026-09-19

状态：审计快照与改造建议，**不是已落地架构，也不是性能验收通过证明**。

审计对象：`puppyone-cloud`，基线 HEAD `41ef8ada` 加当前尚未提交的 Web 工作区修改。覆盖 Files / Git / Access / Chat 的状态生命周期、数据请求、BFF、授权、活动列表、文件读取、历史读取和对象缓存。未修改这些业务实现、数据库或部署。

## 结论

卡顿不只是“前端切换状态慢”。当前至少有两项应首先处理的阻塞性问题：前端 effect 更新循环，以及后端 async 请求路径中的同步 I/O。它们再与重复请求、历史查询放大、页面卸载重建、不可取消的过时请求叠加，造成“点了没反应，随后整页重新加载”的体验。

不建议更换 Next.js / React / SWR / Zustand，也不建议为此拆微服务。应在现有模块化后端和 Git-native Version Engine 边界内，修正执行模型、状态归属和读取成本。

## 证据等级与验证边界

- **源码确认**：沿实际调用路径检查，而非按技术栈推测。
- **隔离复现**：执行从当前源码提取的函数/hook，依赖被替换为受控实现；不访问用户数据和外部数据库。
- **待实测**：浏览器 INP、React commit 时间、真实网络 p95、数据库执行计划、池等待、缓存命中率、部署地域差异。

本轮没有完成已登录浏览器的 Performance/React Profiler 录制，也没有运行生产流量压测。源码检查、类型检查和隔离实验不能代替上述验收。

## 1. P0：普通文件分支的 effect 更新不收敛

源码：

- `frontend/lib/hooks/useData.ts:341–363`，`useToolsByPath` 返回 `tableTools ?? []`。
- `frontend/app/(main)/projects/[projectId]/data/hooks/useStructuredNodeData.ts:61–132`。

普通 Markdown/PDF 等不需要结构化数据，工具查询被禁用，`tableTools` 没有数据。每次 render 返回新的空数组；依赖此数组的 effect 每次执行 `setAccessPoints([])`，又产生新的 state；下一次 render 再次触发相同过程。同时 accessPoints 被同步进 WorkspaceContext，扩大更新范围。

这是上一轮结构化数据按需加载修改使普通文件稳定进入的回归路径，不应把上一轮修改视为已经验证性能改善。

隔离验证：以实际 `useToolsByPath` 和 `useStructuredNodeData` 源码为输入，使用模拟 React 状态/依赖比较的有界调度器。普通 `note.md` 分支运行到 50 轮仍不收敛；仅在内存实验中将缺省空数组改为稳定引用，2 轮收敛。此实验不是 React DOM 渲染测试，但验证了源码中的状态—依赖反馈环。

建议：稳定缺省集合；让重置幂等；移除可派生数据的 effect → state → context 镜像；仅把可编辑草稿保存在 state。补真实 React 渲染测试，覆盖禁用查询、请求中、失败、空结果和项目切换。

## 2. P0：部分 async 接口直接执行同步数据库 I/O

源码：

- `backend/src/platform/activity/router.py:18–43`：async route 直接调用同步 `service.list_for_project`。
- `backend/src/platform/activity/service.py`、`repository.py`：同步授权后调用同步 PostgREST `.execute()`。
- `backend/src/version_engine/entrypoints/http/content_history.py:63–185,208–216`：async route 内直接调用同步 `ensure_project_access`。
- `backend/src/version_engine/read/admin.py:74–100`：async `get_commit_history` 内直接执行同步 `history.get_since`。
- `backend/src/infra/supabase/client.py`：使用同步 Supabase / httpx 客户端。

影响：阻塞当前 worker 的事件循环，不只是让当前 API 变慢；同 worker 的其他请求、WebSocket 和心跳也可能等待。`async def` 不会自动把内部同步工具函数转移到线程池。FastAPI 对普通 `def` 路由/依赖的线程池处理，不能扩展理解为所有同步调用都安全。[FastAPI 官方说明](https://fastapi.tiangolo.com/async/)

隔离验证：提取实际 activity route 和 history service，注入一次 200ms 同步 I/O 等待；10ms 周期的事件循环心跳出现约 215ms 最大间隔。证明的是阻塞机制，不是线上数据库延迟。

建议：逐条调用链归类。纯同步 I/O 读路由使用普通 `def`；混合 async 路由在完整同步服务边界做有界 offload，或按模块迁移原生异步驱动。保持授权先于数据读取，不只 offload 最后的对象存储调用。统一并发预算、排队观测与背压，不用无限 `to_thread` 或堆 worker 掩盖问题。

边界：`content_read.py` 中的 ls/cat/stat 本身是普通 `def`，不能把所有 API 都说成事件循环阻塞。现有认证依赖也为同步依赖；本地 JWT 校验已存在，不能归因为“每个请求都联网登录”。

## 3. P1：历史列表与 WebSocket 初始化的读取放大

源码：

- `frontend/lib/versionWebSocketClient.ts:198`：首次订阅用 `getProjectHistory(projectId, 1)` 建立 head。
- `frontend/lib/contentTreeApi.ts:889`：调用默认 linear history，不是 topo history。
- `backend/src/version_engine/read/admin.py:99,275`：底层查询上限为 `max(limit * 20, 1000)`。
- `backend/src/version_engine/infrastructure/supabase/history_repository.py:748`：查询完整历史记录，再在 Python 过滤。
- `backend/src/version_engine/read/history_facts.py:46`：逐条读取 commit 对象补 parent ids。

因此请求 1 条历史会以 1000 条为查询上限；历史页请求 100 条会以 2000 条为上限。实际返回多少取决于数据库记录数及服务端限制，并非每次必然取满。隔离探针确认 `limit=100` 实际传入 repository 的是 2000。

之后 parent 读取是串行的。热对象缓存会降低成本；冷缓存下可能增加最多与结果条数相当的远程对象读取。把整个循环放到线程池仅解决事件循环阻塞，不会消除串行 I/O。

建议：

- WebSocket 初次握手读取轻量、权威的 root/head 快照，不通过“大历史接口取一条”代替；保留现有 subscribe/reconcile 缓冲和断线补偿契约。
- 将用户可见历史过滤与有界游标分页下推到读模型/SQL；保持线性 catch-up 的顺序、锚点和无遗漏语义，不能直接把 limit 改小。
- page 元数据先返回；parent facts 优先复用不可变缓存和现有批量对象读取能力，缺失时有界并发。
- topo graph 已有 `HistoryGraphCache` 的有界 LRU/TTL/single-flight；默认 linear 路径不会自动享受到它。不能为提速把 linear 接口偷偷换成 topo 接口，两者语义不同。

## 4. P1：授权和文件元数据需要减少往返，而不是放松权限

源码：`platform/authorization/repository.py:load_project_facts`、`version_engine/entrypoints/http/content_read.py`、`version_engine/read/tree_reader.py`。

普通单项目授权依次请求 projects、org_members、project_members，共 3 次同步数据库往返。请求内授权缓存已存在，但不同 API 请求不会共用。项目列表已有 batch facts 路径，不能笼统称为列表逐项目 N+1。

ls/cat 获取内容后另外获取 head；stat 分别获取 head、scope head、entry。除重复读取之外，并发写入时分开采样的元数据未必属于同一时点。目录列举还会逐子目录读取 tree，计算 children_count；这是另一种冷缓存扇出。普通 ls 默认不计算 blob 大小，且 blob 完整性检查已有 `exists_many`，不是“列目录必然下载所有文件”。

建议建立请求级 `AuthorizedReadSnapshot`（建议名称，并非现有对象）：一次受控查询读取授权 facts 与需要的 canonical root/head，以同一 snapshot 驱动本次 ls/stat/cat。SQL 返回 facts，权限策略仍由 canonical AuthorizationService 判定。目录计数按需或批量读取。对不可变对象缓存按项目/物理 namespace + OID 隔离；不得用长期权限缓存绕过成员撤销检查。

## 5. P1：页面状态生命周期与用户期望不匹配

源码：项目 `layout.tsx`、`ProjectsHeader.tsx`、各 Files/history/access page、`ProjectChatSidebar.tsx`、`WorkspaceContext.tsx`。

- 项目 layout 持有 Provider 和 WebSocket 保活，但 Header、视图选择、文件编辑器和 Chat 主体仍主要由各 route page 持有。
- 切换 Files/Git/Access 会替换页面子树；SWR 中的服务器数据可能还在，但编辑器实例、滚动位置、选中项、Chat 的本地状态不因此自动保留。
- Files 导航链接指向 `/data` 根，并不恢复最后打开文件。
- `pendingView` 先改变按钮选中样式，正文仍由路由提交驱动；这不等同于立即切换正文。pointer-down 后取消/失败的复位路径也不完整。
- WorkspaceContext 将数据、派生状态和动作放在一个未 memo 的 value 对象中；更新广播给全部消费者。仅给 value 加 useMemo 无法消除同一 context 字段变化造成的广泛更新。

建议：在项目 layout 建立稳定 WorkspaceShell，持有身份区、一级视图导航和 Chat 容器。正文使用独立 loading/error 边界，点击后先呈现目标视图框架。URL 仍是可分享的路由事实；UI 状态按 projectId/view 分区恢复。重组件首次按需加载，不默认把全部页面永久挂载。编辑器 session 与草稿保护移出易卸载 route，或通过有界会话机制恢复。

保真边界：当前 `useManualSave` 有 localStorage 草稿恢复，不能声称每次切换都必然丢字；但内部 route Link 不经过 `beforeunload`，已有文件内导航 guard 不等于覆盖一级视图切换。应统一导航 guard，并验证正在保存时的切换。

## 6. P1：请求键、预取、失效策略不统一

源码：`useData.ts`、Files `data/layout.tsx`、`useAccessData.ts`、`NeedsActionSection.tsx`、`useActivity.ts`。

- Files layout 挂载即请求 sync status、MCP endpoints、sandbox endpoints、scopes、connectors、repo identity，再加 tools。需要审查首屏是否真的依赖全部数据。
- Files 与 Access 的 scopes/connectors/endpoints 已共享 SWR key，应保留；不是所有请求都重复。
- NeedsAction 的 conflict 和 pending-review 使用不同 key，各自调用同一个 pending-conflicts API，不能被 SWR 按 key 去重。
- `useToolsByPath` 的 key 只有 path，缺少 project/account 维度；对应旧 API 本身也只传 path，需要一起核对契约。不能只换缓存 key 就宣称解决资源隔离。
- `dedupingInterval` 不是数据保鲜期，不意味着一分钟内挂载必然零请求。页面重挂载、显式 mutate、事件失效和轮询应统一设计。[SWR 官方说明](https://swr.vercel.app/docs/revalidation)
- Header 定时预取所有三个入口，包括当前入口，而且随着 Header 重挂载重复调度。**本机 Next 15.5.9 的 `createPrefetchURL` 在 development 返回 null**；上一轮注释中“显式 prefetch 对开发模式有效”不成立。开发冷编译不能作为生产路由响应基线。
- 文件 stat 与 cat 已并行，但 `usePathResolver` 用 `Promise.all` 一起等待；cat 已返回时仍会被较慢 stat 延迟交付。它绕开 SWR，重复访问文件仍重新请求。

建议使用统一 query-key 工厂，区分 mutable HEAD、immutable commit/OID、控制面元数据和临时 activity。不可变内容按版本长期复用，mutable 数据使用版本事件与明确重验证规则。失败重试按状态分类：取消不重试，认证恢复受控，权限/确定性 404 不机械重试，临时故障采用退避。预取以真实使用意图和生产测量为依据，优先关键小资源。

## 7. P1：过时请求没有完整取消与超时链路

源码：`usePathResolver.ts`、`frontend/lib/apiClient.ts:248–285`、`frontend/app/api/backend/[...path]/route.ts`。

路径切换只设置 cancelled 布尔值防止旧结果写回 UI，并不停止 stat/cat 网络工作。BFF 上游 fetch 没有显式绑定请求 signal，也没有上游 deadline；浏览器不再等，不代表后端已经停止工作。

apiClient 的计时在取得 token 后开始、收到 headers 后清除；传入外部 signal 时不会同时建立自身 timeout。因此当前“30 秒超时”不是包含 token/响应体的全链路总预算。平台断开连接是否会进一步取消具体运行时工作，需要实测，不能假定。

建议组合用户取消和 deadline，贯通 browser → BFF → backend；对同步线程/数据库设置独立超时，因为取消 coroutine 并不能中断已运行线程。限制前台 read 并发、丢弃过时排队任务。写入不能盲目随导航取消：保存应由 session 追踪结果，使用幂等/事务身份与状态查询。

## 8. P1/P2：前端 CPU 与长列表缺少完整预算

源码：history/page.tsx 的 `lineDiff` / `FileDiffBlock`，`useManualSave.ts`。

- diff 使用同步 LCS，时间复杂度 O(m×n)，在 render 中计算，未按内容版本 memo。
- 4000 行阈值约束算法，但超过阈值时返回全部删除行加全部新增行；新增/删除文件也全量 `.map` 成 DiffRow，没有总渲染行数预算。
- 离线运行实际 lineDiff：两边各 2000 行、内容完全不同，本机 Node 六次约 21–31ms，仅计算；两边各 50000 行时 fallback 输出 100000 行。后者不是测得 100000 个 DOM 的实际耗时，但明确说明阈值没有保护 DOM 规模。
- commit-content 在后端取完整 bytes 后才判定 binary；前端仍可能下载不适合文字 diff 的大文件，只为显示 binary 占位。
- 每次 `setDraft` 同步序列化并把完整文档写入 localStorage；大文档高频编辑的主线程成本需要单独测量。

建议：diff 在 Worker 执行，结果按项目/路径/版本对缓存；有行数、字节数、计算时长和 DOM 预算，超限明确降级，使用已有虚拟列表依赖。先判断可 diff 元数据，再取有界文本。草稿采用内存即时更新 + 有界合并持久化，并在切换/pagehide 等时机 flush；大型草稿评估 IndexedDB，不能用简单 debounce 牺牲恢复可靠性。

## 9. P2：事件与轮询同时存在，但失效语义尚未闭环

活动 feed 在没有 active items 时停止轮询，且关闭 focus 重验证；另一个客户端发起的新任务不一定使本页再次查询。现有 Version WebSocket 主要是 commit 更新，不自动等于 activity lifecycle 事件。

Files 的 commit 失效处理注册在 data/layout 中；离开 Files 后，项目 layout 的保活订阅是 noop。仍保持 socket，不代表后台视图的缓存已经更新。直接禁用 remount 重验证可能把性能问题换成陈旧数据问题。

建议：项目级 event coordinator 统一按 project/revision 标记失效，当前可见视图受控刷新，隐藏视图回到前台再补读。沿用现有重连历史补偿，不取消它。活动任务补生命周期通知，或者保留低频兜底轮询；批量事件合并、防止每个文件变化触发全项目刷新。

## 10. 可观测性不足以证明已经优化完成

后端已有 RequestLoggingMiddleware 的请求耗时与 request id，对象存储也有 trace phase；这些值得沿用。当前 BFF 的请求/响应头白名单不传递 request id、traceparent 或 Server-Timing，浏览器难以把一次卡顿对应到授权、SQL、对象读取和排队。

建议补小而连续的追踪：

- 浏览器：点击到选中反馈、目标正文首次绘制、INP/long tasks、React commit、请求数与字节数。
- BFF：认证等待、上游 TTFB/响应体、重定向、取消、端到端 deadline。
- Backend：授权 facts 查询、数据库/连接池等待、线程池等待、event-loop lag、对象 GET/cache、序列化。
- 数据库：最慢 query 的 EXPLAIN / rows scanned / rows returned / payload bytes，测试环境采样后再决定索引。

不在 trace、查询参数日志或报告中记录 token、文件正文和原始私有路径；控制标签基数。没有真实计划之前，不将“缺索引”“地域不一致”“服务器不够大”认定为既定根因。

## 建议的状态与后端职责边界

| 数据/职责 | 建议归属 | 约束 |
| --- | --- | --- |
| 当前项目、视图、可分享文件路径 | URL + 稳定 WorkspaceShell | 导航立即反馈；正文独立加载 |
| 目录、版本、连接器等服务端事实 | 统一 SWR query 层 | 不再复制到多套 Context/Zustand |
| 滚动、展开、筛选、上次选择、Chat 开关 | 分项目 UI/session store + selector | 有界保留；切项目不串状态 |
| 编辑草稿、保存中状态、错误恢复 | EditorSession | 不随一级页面卸载丢失；持久化有预算 |
| 授权与 root/head 读取 | 请求级授权快照服务 | 权限检查不能为了缓存而省略 |
| 不可变 Git objects / commit facts | 现有对象缓存与读模型 | namespace 隔离、按字节有界、可重建 |
| 写入 | 现有 ProductOperationAdapter → Version Engine | canonical root / CAS / outbox 不变 |
| 全局数据失效 | 项目级事件协调器 | 版本有序、断线补偿、刷新合并 |

可渐进提取 `WorkspaceShell`、`projectQueryKeys`、`projectQueries`、`projectViewSession`、`projectEventCoordinator` 和请求级 snapshot 服务。以上是职责建议，不是要求一次创建一整套新框架。

## 实施顺序与验收

1. **止血**：修 effect 循环、修 async/sync 执行边界，并补最小回归测试。修复前不应宣称当前版本性能已达标。
2. **打基线**：在生产构建、固定网络和数据集下记录冷/热路径；建立贯通 trace。
3. **缩短关键读取**：轻量 head、受控历史分页、批量 parent 读取、授权 facts 聚合、snapshot 一致读取。涉及新 API / SQL / 分页语义，先按 OpenSpec 做变更方案。
4. **稳定交互状态**：Shell 常驻，query/session/draft 分离，受控失效、取消和共享请求；随后优化大 diff、大文档和 bundle。

建议验收预算（提案，不是已达成数据）：

- 固定测试设备上点击可见反馈 p95 < 100ms；缓存命中的视图恢复 p95 < 200ms。
- 同项目连续切换 20 次不出现重复渲染循环，不丢选中/滚动/草稿状态；导航失败可恢复。
- 热缓存、无版本变化的返回路径不重取文件正文；允许策略明确的后台轻量重验证。
- 注入 500ms 慢 DB 的并发测试中，同 worker 的独立轻量请求与 event-loop 心跳不被同等时长阻塞。
- diff 超限时显示有界降级，不创建与完整大文件行数等量的 DOM。
- 记录 p50/p95、SQL 数、对象 GET 数、传输字节、cache hit rate、peak RSS；以这些数据比较改造前后，而非只看平均耗时。
- 覆盖 100/1000/10000 文件项目、深目录、冷对象缓存、长历史、大文本/二进制、慢网、取消导航、断线重连、后台外部写入和成员权限撤销。

## 应保留的现有设计

- Git-native canonical root 与统一写入链；缓存是派生数据，不是第二套文件事实源。
- 请求内授权 cache、列表授权 batch facts、本地 JWT 验证与前端 token single-flight。
- `CachedStorageBackend` 的 namespace 隔离和 512MB 字节预算；但预算按进程存在，多 worker 的总内存仍应测量。
- `HistoryGraphCache` 的有界缓存与 single-flight、已有批量对象读取能力。
- Files/Access 已共享的 SWR key、合并后的 activity feed、按展开加载 diff、Chat 动态导入、WebSocket 重连补偿。

这些已有优化不抵消上面发现的缺口；同样，也不应因为存在缺口而整体重写。

## 官方参考（2026-09-19 核对）

- [React：Effect 无限循环的条件与排查](https://react.dev/reference/react/useEffect#my-effect-keeps-re-running-in-an-infinite-cycle)
- [Next.js 15：导航、预取、loading 与流式加载](https://nextjs.org/docs/15/app/getting-started/linking-and-navigating)
- [FastAPI：async、同步路由/依赖与普通工具函数的区别](https://fastapi.tiangolo.com/async/)
- [SWR：重挂载重验证与不可变数据](https://swr.vercel.app/docs/revalidation)
