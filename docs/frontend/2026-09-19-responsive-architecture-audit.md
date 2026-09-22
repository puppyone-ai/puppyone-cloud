# Web 工作区响应式架构审查（重构前记录）

后续实现已落地，当前结构见 [响应式架构说明](./mobile-workspace-layout.md)。以下保留重构前诊断，作为前后对照。

复核补充：第一轮实现缺少统一的栏位宽度预算。后续已用统一分配器替换固定布局断点与 Agent 比例宽度，并补充真实浏览器几何回归；此前的状态/实例验收不能替代这部分验证。详见 [栏位尺寸复核与目标架构](./2026-09-19-workspace-pane-geometry-audit.md)。

日期：2026-09-19。范围：`puppyone-cloud/frontend` 的应用壳、项目工作区、文件视图、Agent、侧栏、响应式状态及相关生命周期。没有修改桌面软件项目。本轮增加诊断测试和审查记录，未继续修改生产布局代码。

结论：现有项目会话和文件状态有可保留的基础，但新增响应式层还不适合作为长期架构。主要问题是整页断点切换过于集中、组件布局责任重复、Context 订阅范围过大、侧栏依赖外部 DOM 结构。不能把“静态截图正确、草稿未丢失”当成完整验收。

## 1. 已验证的现象与证据边界

在浏览器中缩窄普通电脑窗口，可以看到两级左侧导航同时隐藏、Header 从单行变为两行、正文扩展占据原导航区域。页面仍显示同一个合同文件。代码在 CSS viewport 1024px 以下同时执行下列改变：

- `app/responsive-workspace.css:11`：应用根节点进入 `position: fixed` 并接管高度、顶部偏移和 safe area。
- 同文件 `:22`：项目 Flex 容器切换为 Grid；`:34` 将原主区域改为 `display: contents`。
- `components/ProjectsHeader.module.css:14`：Header 从桌面 46px 改为 100px；存在编辑工具栏时为 148px。
- `features/workspace/responsive.tsx:53`：任何布局档位变化都会在 effect 中关闭导航侧栏；侧栏原本打开时，会产生第二次状态更新。

这些操作能够解释整体几何结构突然切换的观感。CSS 盒子的变化不是 React 组件卸载；正常 resize 本来也需要浏览器重新排版。是否存在耗时过长的整页绘制、空白帧或额外的页面加载，仍需要 production build 的 Performance/Network 记录确认，不能只看截图断言。

新增 `frontend/tests/workspace/responsive-render-audit.test.tsx` 使用真实 `ResponsiveWorkspaceProvider`、`DataWorkspaceSurface`、`ResponsiveDrawer`，在 `EditorArea` 边界放置有本地草稿的测试组件，替换取数等叶子依赖。测得：

| 操作 | 编辑器测试组件额外 render | 仅使用 setter 的 memo 组件额外 render | 编辑器重新挂载 |
| --- | ---: | ---: | ---: |
| 1280 → 1200，同档位 | 0 | 0 | 0 |
| 打开不相关的 Projects 导航 | 1 | 1 | 0 |
| 关闭 Projects 导航 | 1 | 1 | 0 |
| 1200 → 1023，跨档位 | 1 | 1 | 0 |
| 1023 → 800，同档位 | 0 | 0 | 0 |
| 打开 Files 侧栏 | 1 | 1 | 0 |
| 800 → 639，Files 侧栏开着 | 2 | 2 | 0 |
| 639 → 1280 | 1 | 1 | 0 |

整个序列中测试编辑器挂载一次，DOM 实例和未保存草稿保留。这证明响应式 Context 会把不相关操作传播到编辑器边界；不证明真实 CodeMirror、Monaco 或 Chat SSE 的完整性能和网络行为。数字是同步测试中的函数 render 次数，不是浏览器绘制次数或耗时。

源码未发现 resize 链路直接调用 `location.reload()` / `router.refresh()`，文件查询 key 也未包含 viewport mode。Markdown 的 CodeMirror 实例在空依赖 effect 中创建，其 React key 由文件身份和编辑模式组成，未包含屏幕宽度。由此不能认定“每次 resize 都重建整个编辑器”。

## 2. 需要调整的责任边界

| 优先级 | 现状 | 问题 | 应调整为 |
| --- | --- | --- | --- |
| 高 | 一个 1024px 断点同时改变应用壳、Header、文件树、面板、编辑器密度 | 电脑窄窗口也出现整体切换；局部空间不足变成全页模式跳变 | 按内容最小可用空间逐层收缩；工作区处理栏位，组件用自身容器宽度调整工具栏 |
| 高 | `ResponsiveContext` 同时承载 mode、Projects/Files 开关、actions | 即使只用一个稳定 setter，也订阅了所有字段；`memo` 无法屏蔽 Context 更新 | 拆分布局能力、局部开关和稳定 actions；或沿用现有 Zustand，以 selector 订阅必要字段 |
| 高 | `DataWorkspaceSurface:60` 在编辑器父级订阅整个响应式 Context | 打开 Projects 导航也使文件内容树重新执行 | 由 `FileNavigationRegion` 订阅 Files 开关；编辑器作为稳定的独立内容区域 |
| 高 | Header 在 `ProjectWorkspaceShell` 内，Agent 是它的外部兄弟；移动 CSS 再展平包装 | DOM 层级没有直接表达区域关系，靠媒体规则修补 | 在固定的 Project frame 中显式声明 Header、MainPane、AgentRegion；同一套 DOM 贯穿所有宽度 |
| 高 | `ResizablePanel` 内联 position/width/height，全局 CSS 再用 `!important` 改写；通用选择器影响所有面板 | 大小、停靠、覆盖行为缺少单一负责人 | 侧栏 region 负责 docked/overlay；尺寸组件仅负责桌面宽度与拖拽，不负责另一套移动定位 |
| 中 | `100/148px` 在 Header 与全局布局中重复；drawer 为 fixed，手算 Header 下方偏移 | 工具栏、字体、safe area 等变化容易造成遮挡和空隙 | 使用 Grid 的实际 Header 行和其下方 body 区域作定位基准；overlay 在 body 内 `inset: 0` |
| 中 | drawer 向上遍历 DOM 并把各层兄弟设为 inert；Agent 通过 class 查询 body 和全局 dialog/menu | 焦点和可交互范围依赖偶然 DOM 结构，包装层改动会影响行为 | 用明确的区域 ref / overlay owner 管理被覆盖区域；统一 Escape、返回焦点和嵌套弹层顺序 |
| 中 | `WorkspaceShellProvider` 每次 render 创建新的 value 对象 | 外层有变化时再次通知所有 shell Context 消费者 | 稳定 provider value，并缩小订阅边界；这与 responsive Context 拆分是两件事 |
| 中 | compact 模式对所有 window/VisualViewport resize 更新根 CSS 变量 | 键盘处理、窗口尺寸处理与应用壳耦合；是否有性能损耗尚未量化 | 独立 Viewport adapter；只在高度/偏移实际变化时更新需要避让键盘的区域 |

`display: contents`、Context、`position: fixed`、媒体断点或内联样式本身都不是错误。这里的问题是它们共同承担同一套布局，又在一个断点集中改变。增加更多 `memo`、debounce 或全局覆盖规则，不能替代职责整理。

## 3. 可以保留的现有基础

- `ProjectSessionProvider` 在项目级持有稳定 Zustand store，按 `projectId` 而不是屏幕尺寸确定生命周期。这是正确的状态归属。
- 文件草稿、保存、导航保护已经在 `features/files/` 中分离；无需为移动端复制一套业务状态。
- SWR 查询主要按项目、目录、文件等业务身份组织，响应式状态没有进入查询 key。全局和主要文件查询关闭了 focus revalidation。
- 文件查看器由稳定 registry 分发；当前实现没有通过 `MobileEditor` / `DesktopEditor` 两棵树切换内容。
- Chat 当前关闭时保留已挂载实例，Agent 状态属于项目；应保留这一点，并进一步隔离纯布局订阅。
- Next.js 持久 layout、现有设计 tokens 和桌面尺寸均可继续使用。无需为解决此问题更换框架或状态库。

这不是对全站每个页面的完整审计。Files 页面仍承担大量编排工作（当前约 1090 行），有进一步拆分的空间；本次优先处理能直接解释 resize 行为的工作区边界。

## 4. 建议的组件结构

以下是目标责任关系，不是新增移动页面。业务内容在断点切换时不能重新选择父级、改变 key，或切换 Portal 容器。

```text
AppShell / 现有 Auth、Org、SWR 等 Provider
├─ ProjectNavigationRegion
└─ ProjectSessionProvider (identity = projectId)
   └─ ProjectWorkspaceFrame
      ├─ WorkspaceHeader
      ├─ MainPane
      │  ├─ FileNavigationRegion
      │  │  └─ FileExplorer
      │  └─ EditorViewport
      │     └─ EditorHost (identity = projectId + filePath)
      ├─ AgentRegion
      │  └─ AgentRuntime / ChatSession
      └─ DialogLayer
         └─ ProjectSettingsDialog
```

`ProjectWorkspaceFrame` 是稳定 Grid。桌面 Agent region 可以跨 Header 行与正文行，保持现有右侧 Chat 顶部对齐；窄屏下它只占 Header 下方区域，右侧覆盖展开。改变的是 grid area、宽度和停靠方式，React 位置不变。不要为了这个层级图把桌面 Header 改成跨越 Chat 的新视觉设计。

`FileNavigationRegion` 在 MainPane 内具有稳定定位宿主：宽屏停靠左侧，空间不足时从 body 左侧覆盖展开。目录树只挂载一份，EditorViewport 不订阅导航开关。Overlay 的定位跟随实际 body 几何，不用复制 Header 高度常量。

`AgentRegion` 负责呈现及侧栏开关；`AgentRuntime` 负责消息、草稿、流式请求。区域尺寸变化可以让编辑器/聊天滚动区重新测量，但不得销毁 session、重建连接或清空内容。已有编辑器自动布局能力优先复用；只有缺失时才在编辑器宿主使用局部 ResizeObserver。

`DialogLayer` 承载真正的模态设置。Files/Agent 保持 Header 可操作时，应按非模态侧栏设计；模态 dialog 则统一实现背景隔离、焦点约束、Escape 和焦点返回，不能只增加 `aria-modal`。

## 5. 响应式与状态规则

1. 布局按可用空间适配。电脑窄窗口同样应能使用紧凑布局，不能改用 user-agent 判断来掩盖问题；触控目标与 hover 行为按输入能力另行增强。
2. 容器查询负责组件密度和工具栏收缩；媒体查询负责应用级展示约束。阈值应来自最小可用文档宽度、Agent 宽度和导航占用，而不是先规定“1024 以下都是手机”。
3. 内容布局优先由 CSS 完成。只在侧栏交互、焦点管理等确实需要 JS 行为时订阅离散断点。`useSyncExternalStore + matchMedia` 可以保留，但不应把结果混入通知全工作区的 Context。
4. 用户期望的打开状态、桌面宽度偏好，与当前可用空间计算出的 docked/overlay 状态分开。切断点不应顺带清空用户状态；临时导航的关闭规则应明确且局部化。
5. 根壳保持同一种尺寸/定位策略。`dvh`、safe area、VisualViewport 是视口适配层；键盘补偿不应承担整页响应式切换。
6. 折叠屏优先保证连续 resize 和左右栏的基本适配；Viewport Segments / hinge 作为能力增强。没有实机验收，不能承诺某个品牌的折叠行为已经正确。

## 6. 实施顺序与验收

先记录固定桌面窗口、Chat 开关、长路径、Markdown/CSV、深层目录的视觉基线。随后整理 frame 的稳定区域结构，拆分订阅，收口侧栏与 overlay 的责任，最后清理覆盖样式。每一步保持既有桌面几何，不同时重写文件业务流程。

验收不仅看几个静态宽度：

- 连续拖动窗口，覆盖断点两侧以及来回跨越；不出现全页 loading、短暂白屏或上下文跳转。
- 单独打开 Projects、Files、Agent，不触发无关编辑器内容树 render；若确需响应尺寸，仅调用其布局测量能力。
- 同一文档 resize 前后，编辑器实例、草稿、undo、选区、滚动位置保持；同一 Chat 保留草稿和流式回复。
- Network 确认 resize 不产生新的 document navigation、不因布局状态重新读取文件、不重建业务 SSE/WebSocket。
- 用 production build 的 Performance 和 React Profiler 区分 CSS layout/paint、React update、React remount；开发态 Fast Refresh 结果另记。
- 手机软键盘、旋转、折叠/展开、safe area、触控滚动及嵌套弹层做实机验证。

本轮未声称这些完整验收项已经通过，也未修复生产布局。诊断测试只验证了上文列出的响应式传播与组件身份。

## 7. 官方依据

- [React：状态与组件在树中的位置](https://react.dev/learn/preserving-and-resetting-state)：用于区分 rerender 与状态销毁。
- [React：Context 更新与 memo 的边界](https://react.dev/reference/react/useContext)：对应本次实测的无关消费者更新。
- [Next.js 15：layout 的持久性](https://nextjs.org/docs/15/app/api-reference/file-conventions/layout)：持久路由布局不会阻止其内部客户端组件因 Context/state 更新而 render。
- [web.dev：按内容选择断点](https://web.dev/learn/design/media-queries)：支持内容驱动的断点设计。
- [MDN：Container queries](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Containment/Container_queries)：支持组件基于自身可用宽度适配。
- [MDN：VisualViewport](https://developer.mozilla.org/en-US/docs/Web/API/VisualViewport)：区分布局视口与可见视口。
- [W3C APG：Modal dialog](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)：明确模态背景和焦点责任。
- [web.dev：布局成本的测量](https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing)：说明需要浏览器性能记录，不能从 React render 次数推导绘制成本。

以上文档给出基础约束；具体的 frame、region 和状态拆分方案是结合本项目提出的工程建议，不是声称官方规定了唯一组件树。

## 8. 重构完成记录（2026-09-19）

上述记录保留的是重构前状态；本次实现已完成工作区响应式责任拆分，当前代码约定见 [响应式架构说明](./mobile-workspace-layout.md)。改动只落在 Web 项目，没有修改桌面应用。

- 固定 Project Grid 中的 Header、正文、Agent 区域；侧栏由实际正文区域定位。桌面保留原有 Header、导航与 Agent 几何；窄屏使用自然 Header 行高。
- 导航状态使用稳定 store 和字段 selector，视口能力订阅下沉到交互区域。文件控制器排除 Agent/Access 面板通知，Chat runtime 与尺寸容器分离。
- 用户侧栏开关意图和桌面宽度偏好跨断点保留。统一显式区域 ref、背景 inert、焦点返回和手势关闭；VisualViewport 仅更新必要的 CSS 变量。
- Web Markdown 编辑器通过公共 CodeMirror scroll snapshot API 保留阅读锚点，处理展开时内容变短导致浏览器将滚动位置归零的情况。未修改 shared-ui 编辑器。

验证结果：

| 验证 | 结果 |
| --- | --- |
| `npm run test:unit` | 24 个文件、113 项测试通过。 |
| TypeScript | 独立 `tsc --noEmit` 和最终 production build 的类型检查通过。 |
| Production build | `PUPPYONE_NEXT_DIST_DIR=.next-responsive-check npm run build` 通过；保留既有非阻断依赖/图片 lint 警告。 |
| 主题与 diff | `npm run theme:audit`、`git diff --check` 通过。 |
| 响应式传播回归 | 真实 provider/surface 配合状态探针：导航开关及断点序列中，编辑器和仅使用 actions 的消费者额外 render 均为 0，编辑器只挂载一次，草稿/DOM 保留。 |
| Agent 回归 | 实际 auxiliary shell/Header 配合 runtime 探针：390/820/1280px 之间 runtime 无额外 render/卸载，草稿和桌面宽度偏好保留。 |
| 浏览器几何和交互 | 桌面固定几何、连续窗口缩放、320/390px 左右侧栏、820px 文档/Agent 双栏、自然三行编辑 Header、左右滑动关闭与焦点返回通过。 |
| Production Network | 390→820→1280→639→390px 序列没有新增 document navigation 或文件内容读取；原有活动轮询及失败的后端 WebSocket 重试仍存在。 |
| Markdown 阅读位置 | 实际编辑器在 390→820→1280→390px 后保留原阅读位置；该最终适配器另有 3 项回归测试。 |

边界：浏览器模拟不等于折叠屏实机验收，真实 iOS/Android 键盘、IME 和铰链几何尚未验证。测试项目没有可用 Agent 配置，且原后端 WebSocket 已在重试，因此不声称验证了活跃 SSE/WebSocket 流不中断。未采集可量化的 Performance/React Profiler 耗时记录；零额外 React render 不代表零浏览器 layout/paint。原有字体审计失败不属于本次响应式改动，未借此更改桌面字体。
