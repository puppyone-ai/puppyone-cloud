# Files / Git 导航与前端状态架构调查

日期：2026-09-20。对象：`puppyone-cloud/frontend` 当前未提交工作树。

本轮是架构调查：检查实际调用链、对照本机 Next.js 15.5.9 实现和官方文档，并使用真实组件/hook 做隔离复现。**未修改业务实现，不是已完成导航重构的验收报告。** 桌面端未改动。

## 结论

现有布局层已经把侧栏尺寸、overlay/docked 和业务内容挂载分开，但**导航状态仍没有收口**：Header 自己维护选中意图，Next Router 维护已提交视图，Files 自己维护路径，浏览器 history 再维护地址，ProjectSession 另存返回 Files 的链接；入口还混用 Link、router.push、原生 pushState 和 effect 中的 replace。

这些工具本身均有合理用途。问题是同一个“用户现在在哪里”，由多处状态分别回答，且取消、返回、重新挂载时没有一致的恢复规则。慢网和冷编译会扩大不同步的可见窗口；后端慢不能单独解释错误的选中状态或 URL/正文身份不一致。

已隔离复现三项缺陷：

1. 路由仍是 Files 时，Git 已被画成选中；此时再点击 Files，Git 的选中样式仍残留。
2. 地址栏为 `/data/folder-b`，Files 从缓存的根路径参数重新挂载时，正文路径和记忆的 Files 链接都变成根目录。
3. 有未保存内容时，点击一个带自定义导航的面包屑，会连续调用两次离开确认。

## 当前组件层级与生命周期

```text
app/layout.tsx
  国际化 / Theme / SWR / SupabaseAuth
  (main)/layout.tsx
    Organization / Onboarding
    ResponsiveWorkspaceProvider
      布局 store / 抽屉开关 / 稳定 DOM refs
    WorkspaceLayoutFrame
      ProjectNavigationRegion       左侧项目列表
      main / 路由 children
        projects/layout.tsx         仅透传
        [projectId]/layout.tsx
          AgentProvider             key = projectId
          ProjectSessionProvider    key = projectId
          VersionWebSocketProvider
          ProjectWorkspaceShell     同一项目切换视图时保留
            ProjectsHeader          固定外壳
            primary body            Next 路由 children
              data/layout.tsx       Files 查询/context
                data/[[...path]]/page.tsx
                  文件树 / 编辑器 / 文件详情
              或 changes/page.tsx   直接导出 history/page，无重定向
                Git 历史 / 变更详情
            ProjectAuxiliarySidebar Chat / Access 项目侧栏
            ProjectSettingsDialog   设置弹窗
```

Files/Git 页面通过 `ProjectHeaderContribution` 的 portal 向固定 Header 填入路径和页面工具。Header 外壳不需要卸载，但它显示的路径依赖当前仍挂载的页面贡献。

同项目 Files ↔ Git：项目 Provider、Header 和项目侧栏保留；正文 route subtree 替换。SWR 缓存、ProjectSession 中的 Git 筛选等能保留，但页面内部 useState、编辑器 DOM 并不自动保留。Chat/Access 首次打开后在项目侧栏中保留挂载。

跨项目：`key={projectId}` 会重建项目 Provider/Session。这里的 Session 是“当前项目生命周期内的会话”，不是保存 A/B 多个项目实例的缓存；A → B → A 不保证所有临时 UI 状态恢复。编辑草稿另有持久化，不能据此认定必然丢失草稿。

## 当前操作与 URL 的关系

| 用户操作 | 执行机制 | URL / 正文行为 |
| --- | --- | --- |
| 左侧切 Project | `WorkspaceProjectRail → router.push('/projects/{id}/data')` | Next 提交项目路由，项目子树替换；按钮不先等目录 API |
| Files → Git | `<Link href='/projects/{id}/changes'>` | Header 的 pendingView 先更新；路由加载/提交后正文换成 Git；Git 再独立读取历史 |
| Git → Files | `<Link href={filesHref || '/projects/{id}/data'}>` | 恢复记忆的 Files URL，Next 重新渲染 Files route |
| 展开/收起左侧树的文件夹 | `ExplorerTreeRow → toggleExpanded` | URL 不变，仅树展开状态改变；必要时读取子目录 |
| 打开文件，或通过面包屑等进入目录 | `navigateTo → setClientPath + history.pushState` | 地址同步写入 history；Files 保持挂载，客户端解析/读取新路径；不是一次 Next route page 替换 |
| 浏览器后退/前进 | Next 的 history 恢复 + Files 自己的 popstate listener | 两个恢复机制同时参与；Files listener 只在 Files 挂载时存在 |
| Header 的 Chat / Access | ProjectSession `openPanel / closePanel` | URL 不变，切换右侧面板 |
| Header 的 Project settings | Shell 中 `settingsOpen` | URL 不变，显示弹窗 |
| 访问旧 `/access` 或 `/settings` 链接 | 页面 effect 打开侧栏/弹窗后 `router.replace(filesHref || /data)` | 先进入适配 route，再替换为 Files；仍有内部入口使用此绕行路径 |
| Git 中选择 commit / 筛选 | ProjectSession + 页面 state | 当前不写 URL；复制 Git URL 不会携带这些选择 |
| resize、侧栏展开收起 | 布局 store + UI state + CSS | URL 不变，不应触发 route replacement |

关键区别：**地址改变、页面组件替换、业务数据到达，是三个事件。** 原生 pushState 可以改变地址而不替换 route page；Next 路由提交也不代表 SWR 业务数据已读取完毕。

## 缺陷一：Header 把导航意图画成已提交视图

位置：`components/ProjectsHeader.tsx:47`。

```ts
const [pendingView, setPendingView] = useState<ProjectView | null>(null);
const displayedView = pendingView ?? activeView;
```

Git 的点击处理先 `setPendingView('git')`，选中样式由 `displayedView` 决定。正文则来自 `ProjectWorkspaceShell` 的 route children，`activeView` 来自 `useSelectedLayoutSegment`。两者不是同一个提交点。

可确定的序列：

1. 路由/正文为 Files。
2. 点击 Git，Next 尚未提交；Git 已被画成 selected，正文仍是文件夹。
3. 再点击 Files。代码要求 `view !== activeView` 才更新 pendingView；此时 `activeView` 本来就是 Files，因此没有清理 Git 意图。
4. pendingView 只在 activeView/projectId 变化的 effect 中清除。若路由没有离开 Files，错误选中可以一直残留。

同时，`aria-current` 仍按真实 activeView 标记 Files。于是同一个 Header 的视觉选中和无障碍当前页也会互相矛盾。

这不是 Git 接口把 folder 数据返回来了。`changes/page.tsx` 直接导出历史页；历史页数据未到时显示 PageLoading，报错时显示错误，不包含 Files 文件树分支。

边界：以上已在真实 Header 的隔离渲染中复现，模拟的是“路由尚未提交”。未将它冒充为当前浏览器每次异常的完整运行时追踪；若 URL 已是 `/changes` 且正文长时间仍为 Files，还需关联 route commit、Suspense、运行时异常和 RSC/chunk 加载。

## 缺陷二：Files URL 与路由参数有两种路径事实

位置：`data/hooks/useDataRouteController.ts:69,137,143,171`。

- 普通文件导航改 `clientPath` 并调用原生 `pushState`。
- 初次/重新挂载却用 `useState(path)`，只从地址栏取 typeHint，不从地址栏恢复 path。
- 路由参数变化的 effect 也再次 `setClientPath(path)`。
- 只有已挂载的 popstate listener 会读取完整浏览器路径。
- 随后的 effect 又把 clientPath 写进 ProjectSession.filesHref。

Next 支持原生 History API，并同步 `usePathname/useSearchParams`，但这不等于重新请求 route page。检查本机 `next/dist/client/components/app-router.js` 可见，原生 pushState 复制现有 router tree 并 dispatch RESTORE；`useParams` 则由 tree 派生。

因此应覆盖的场景是：

```text
Next 加载 Files 根目录，params.path = []
  → Files 内部 pushState 到 folder-b，clientPath = ['folder-b']
  → 去 Git，Files 卸载
  → 历史恢复带旧 route tree 的 Files 记录
  → Files 重新挂载：浏览器路径 folder-b，缓存的 params.path 仍可能是 []
  → 控制器选择旧 params，正文回根目录，filesHref 也被覆盖
```

隔离 hook 测试直接构造“浏览器 folder-b + 缓存 params []”这一状态，确定复现正文路径 `[]` 和 filesHref `/data?type=folder`。完整 Next back/forward 时序仍需集成测试，但这个输入下控制器的错误恢复已被确认。

[Next.js Native History API](https://nextjs.org/docs/app/getting-started/linking-and-navigating#native-history-api) 说明它同步 pathname/searchParams；不能把 native history 本身说成框架不支持，也不能把 URL 已更新视为 route params 已换新。

## 缺陷三：导航保护入口分散

- 文件内部导航：`navigateToWithEditorGuard` 手动确认。
- 页面 Link：`useEditorSaveGuards` 在 document capture 阶段匹配 `a[href]` 并确认。
- 面包屑：本身是 Link，但其自定义 onClick 又进入手动确认路径。
- 左侧 Project：是 button + router.push，不匹配上述 anchor 拦截。
- 浏览器 back/forward：不经过 anchor click；现有 popstate listener 没有这层离开确认。
- 右侧配置 panel 还有自己的 `panelNavigationGuard`，其职责不是保护整个文件工作区导航。

已经隔离复现一次面包屑点击出现两次 `window.confirm`。同时可以从源码确认，项目 button 的导航不会经过 anchor guard。这里指出的是确认覆盖不一致，不声称草稿一定丢失：当前仍有本地草稿恢复机制。

## 加载边界和旧路由适配还没有随布局改造收口

### 加载壳仍假定自己拥有 Header

`ProjectWorkspaceShell` 已持有真正 Header，但 `[projectId]/loading.tsx` 和 `data/loading.tsx` 仍渲染 `ProjectPageLoadingShell`。后者通过 `HeaderedPageLoadingShell` 再预留一条 46px Header 带。

因此已有 Header 下显示 route fallback 时，会额外出现一条空标题带。这是加载态布局契约与常驻 Shell 不一致，不能靠再调正常 Header CSS 解决。

Git 的 `changes/`、旧 `history/` 没有自己的 loading.tsx，使用项目级祖先边界。`loading.tsx` 中“点击的瞬间 URL 一定变、完全无布局位移”的注释过度承诺。Next 官方说明即时 fallback 依赖预取完成；React Transition 也可能保留已经显示的内容直到新内容可提交。[Next 15 loading](https://nextjs.org/docs/15/app/api-reference/file-conventions/loading#navigation)、[React Suspense](https://react.dev/reference/react/Suspense#preventing-already-revealed-content-from-hiding)。

### 旧入口仍用路由模拟侧栏动作

`access/page.tsx` 和 `settings/page.tsx` 作为旧书签兼容入口合理，但现有内部代码仍 router.push 到 `/access?...`，然后由适配页面的 effect 再 replace 回 Files。例如 `useDataPanelController.openAccessFullSettings`。

结果是“打开一个侧栏”也可能走页面卸载、URL 改变、effect 再跳回的过程。内部入口应直接调用项目层动作；路由适配器只负责旧链接。异步动作也应避免在用户已经发起新导航后再把页面拉回旧目标。

## 为什么当前测试通过仍会有这些问题

现有 Header 测试 mock 了 Next Router；Session 测试通过 rerender children 验证状态保留；浏览器几何 fixture 主要验证宽度、动画和 DOM 挂载。这些测试各自有价值，但不覆盖真实的 Next 路由、原生 history、取消和 back/forward 的组合。

通过布局/组件测试，可以证明对应布局或组件契约；不能扩展成“整个导航架构没有问题”。

## 建议收口的职责

| 职责 | 唯一事实来源 / 所有者 |
| --- | --- |
| 当前项目、Files/Git、文件路径 | canonical route；Header 和正文都从同一已提交身份派生 |
| 导航中状态 | 由实际路由 transition 驱动；明确区分 pending 和 active，取消/失败/连续点击必须收敛 |
| Files 稳定挂载 | Files layout 持有文件树和工作区骨架，path page 保持薄；避免用另一套路由状态换取不卸载 |
| 文件草稿、保存进行中 | 具备文件身份的 editor session；统一离开确认与恢复，不依赖 document 上的 anchor 猜测 |
| Chat/Access 开关、设置弹窗 | Project Shell/Session；普通内部操作不改 URL |
| 服务端目录、历史、项目元数据 | query 层/SWR；加载失败在当前正确视图内呈现 |
| 面板宽度、overlay/docked、折叠屏分栏 | workspace layout store；只管理几何，不参与导航身份 |
| 旧书签路由 | 边界适配器；业务组件不继续依赖“先跳转再跳回来” |

倾向采用完整的 Next 路由管理 Files/Git 和文件路径，并把稳定 Files 容器上移到 layout，减少 clientPath 镜像。若继续采用浅层原生 history，也必须用一个专门的地址适配层从实际 URL 派生 Files 身份，完整覆盖首次挂载、恢复、push/replace/pop；不能让各页面自己同步多份 path。

这不要求更换 Next/React/Zustand，也不要求把所有东西放进一个巨大的全局 store。需要统一的是相同事实的所有权，以及 navigation 的 guard → request → commit/cancel/error 过程。

## 实施与验收顺序

1. 修复已复现的 Header 意图残留和 Files 恢复旧路径；将这两项改为期望正确行为的回归测试。
2. 建立一致的导航/草稿保护入口，修双重确认与 button/back 路径覆盖。
3. 将加载态改成 Shell 内的正文 fallback，审查 Git 的边界；正常 Header 与 loading 不再重复占高。
4. 让内部 Access/Settings 直接打开项目侧栏/弹窗；保留旧 URL 的兼容职责。
5. 在实际 Next 路由中覆盖 Files → Git → Files、慢路由快速反向点击、文件 pushState 后离开再返回、刷新深链接、跨 Project、草稿取消、API 失败/延迟、侧栏打开时导航。记录点击反馈、route commit、正文身份、数据就绪四个时刻。

导航确认正确后，再独立优化前一份调查中已经测得的后端读取延迟。不能把优化数据库当作 UI 状态一致性的替代。

## 本轮验证记录

执行真实 `ProjectsHeader`、`useDataRouteController`、`useEditorSaveGuards` 和 `ProjectHeaderBreadcrumbs` 的诊断测试；mock 外部路由提交/文件读取以控制输入，3 个缺陷现状断言均复现。

临时探针已移出正式测试目录至 `/private/tmp/puppyone-navigation-investigation-20260920.test.tsx`，避免把“断言旧缺陷存在”的测试留作通过基线。可以复制回 `frontend/tests/workspace/navigation-investigation.probe.test.tsx` 后运行：

```sh
npm run test:unit -- tests/workspace/navigation-investigation.probe.test.tsx
```

这不是生产浏览器端到端验证，不代表当前 Git/Project 所有卡顿都已得到唯一归因，也不代表完成修复。
