# Web 工作区栏位尺寸复核与目标架构

日期：2026-09-19。以下保留实施前的诊断与设计。后续已在 Web 实施统一尺寸分配器，当前约定见 [响应式架构说明](./mobile-workspace-layout.md)。桌面项目仅作源码对照，没有修改。

## 1. 结论

第一轮响应式重构解决了组件身份、状态订阅和区域定位，但没有统一各栏的宽度分配。当前用户偏好宽度、CSS 实际宽度、停靠/抽屉策略仍由不同层决定。保留编辑器与 Chat 实例，并不能保证窗口缩放时的几何行为合理。

这次确认的右栏缩窄由显式 CSS 比例宽度造成；左侧两级导航则分别在固定断点退出布局，再使用抽屉宽度。这些区域已有 `flex-shrink: 0`，不能把问题笼统归结为 Flex 自动压缩，也不能通过给所有侧栏再加一次 `flex-shrink: 0` 修复。

## 2. 当前责任分布与冲突

| 区域/层 | 现有责任 | 缺口 |
| --- | --- | --- |
| App shell / `WorkspaceProjectRail` | Projects 偏好宽度默认 220px，收起时 56px | 没有参与 Files、正文、Agent 的统一预算 |
| `ResizableSidebarColumn` | Files 偏好宽度默认 200px，拖拽范围 200–480px，保存偏好 | 不知道正文和 Agent 至少需要多少空间 |
| `ResizablePanel` | Agent 偏好宽度默认 450px，拖拽范围 300–800px | 偏好保留，但 CSS 可以覆盖最终宽度 |
| `app/responsive-workspace.css` | Grid、宽度覆盖和抽屉切换 | 1023/899/639px 分别切换区域，没有求解总空间约束 |
| `features/workspace/responsive.tsx` | 订阅媒体查询，决定交互与焦点模式 | 重复维护 CSS 阈值；交互模式不是统一几何结果的消费者 |

直接证据：

- `frontend/app/responsive-workspace.css:65`：1023px 及以下，Projects 从占据布局宽度的左栏变为绝对定位抽屉。抽屉宽度为 `min(360px, 100% - 40px)`。
- 同文件 `:83`：Agent 宽度改为 `clamp(300px, 45cqw, 450px)`。这里的 `45cqw` 是项目容器宽度的 45%，不会读取用户保存的 Agent 宽度；窗口变窄便持续缩窄。
- 同文件 `:129`：899px 及以下，Files 也从左栏变为抽屉。
- 同文件 `:152`：639px 及以下，Agent 再变为 Header 下方的覆盖侧栏。
- Grid 的中心轨道是 `minmax(0, 1fr)`；`min-width: 0` 允许内容正确收缩，但它不等于“正文最小可用宽度”。当前没有业务上的正文宽度下限。

因此，“宽度偏好没有丢失”和“侧栏实际宽度没有改变”是两件事。第一轮测试覆盖了前者，没有充分覆盖后者。

## 3. 浏览器几何复现

使用当前 `responsive-workspace.css` 与 `ResizablePanel.module.css`，在独立浏览器 HTML 中重建必要的 Flex/Grid 包装；通过不同宽度 iframe 和 `getBoundingClientRect()` 测量。Projects、Files、Agent 偏好固定为 220/200/450px，抽屉关闭。数值单位为 CSS px。

这是当前 CSS 的隔离复现，不是完整应用端到端测量，不证明 React 重挂载、网络请求或绘制耗时。临时夹具位于 `/private/tmp/puppyone-responsive-audit-20260919.html`。

| 可用宽度 | Projects 占布局 | Files 占布局 | Agent 实际宽度 | 正文可用宽度 |
| ---: | ---: | ---: | ---: | ---: |
| 1280 | 220 | 200 | 450 | 410 |
| 1100 | 220 | 200 | 450 | 230 |
| 1024 | 220 | 200 | 450 | 154 |
| 1023 | 0 | 200 | 450 | 373 |
| 960 | 0 | 200 | 432 | 328 |
| 900 | 0 | 200 | 405 | 295 |
| 899 | 0 | 0 | 404.5 | 494.5 |
| 820 | 0 | 0 | 369 | 451 |
| 700 | 0 | 0 | 315 | 385 |
| 640 | 0 | 0 | 300 | 340 |

两类问题已经可以独立解释：

1. 1024→1023px 只减少 1px，正文却从 154px 增至 373px；900→899px 也发生类似释放整栏宽度的切换。这里无需发生 React 卸载，就会有大范围重新排版的观感。
2. 960→900→820px，Agent 从 432→405→369px，尽管偏好始终是 450px。这是比例规则的直接结果。

停靠与覆盖的转换本来就会释放一栏宽度，不能要求所有宽度变化都连续。应修复的是：转换前正文已不可用、转换条件忽略面板开关及用户宽度、不同区域缺少统一优先级；并将必要变化限制在布局区域内。

## 4. 桌面版为何不同

桌面实际使用 `src/features/app-shell/layout/desktopPaneLayout.ts` 的 `resolveDesktopPaneLayout()`，调用位置是 `src/components/DesktopCloudShell.tsx:187`，并非未接入的辅助函数。

它接收工作区实际宽度、Explorer/Agent 偏好、各栏最小/最大宽度和打开状态，统一求解：

1. 正文消化一般的窗口收缩。
2. 正文触及下限后，只从 Agent 回收必要宽度，直到 Agent 下限。
3. 仍然不足，才从 Explorer 回收宽度，直到 Explorer 下限。
4. 不因被动 resize 改写用户的打开/收起意图。

所以桌面侧栏并非绝不缩窄，而是按明确顺序、按缺口缩窄，不是两边都跟随窗口比例缩放。

此外，Desktop shell 把包含 Projects rail 的总最小宽度传给原生窗口；`electron/main/ipc/window-layout-ipc.mjs` 调用 `BrowserWindow.setMinimumSize()`，必要时还会增大当前窗口。Web 无法限制用户浏览器窗口大小，必须在各栏下限仍放不下时提供抽屉/覆盖策略，不能直接复制原生窗口保底行为。

桌面 `packages/shared-ui/src/sidebar/CollapsiblePaneFrame.tsx` 还把 Frame、Viewport、Content Plane 分开：收起动画改变外框宽度，内容保持展开宽度并被裁切，避免文字逐帧挤成窄列。这与窗口尺寸不足时的正常内容重排是不同问题。

## 5. 目标责任划分

```text
Workspace layout owner
├─ Inputs: 实际宿主宽度、区域存在性、用户偏好、最小/最大尺寸、分屏能力
├─ resolveWorkspaceLayout(): 纯函数，唯一的栏位预算与呈现策略
└─ Resolved layout
   ├─ Projects / Files / Agent: renderedWidth、docked/overlay、拖拽边界
   ├─ Main: 剩余宽度及可用性约束
   ├─ Frame: 写入 CSS variables / data attributes
   └─ Region interaction: 据同一结果处理焦点、inert、手势和按钮

Project session / File editor / Chat runtime
└─ 保留业务身份及稳定父级，不持有或订阅逐像素布局状态
```

这个 owner 必须能看到 Projects、Files、正文和 Agent 的共同预算；只在 Agent 内添加一个 hook，或只测量扣除导航后的正文，不能解决四个区域互相争空间的问题。非 Files 路由应明确报告 Files 不存在；关闭 Agent 时也应立即释放其预算。

### 尺寸与状态

- `preferredWidth` 是用户拖拽产生的偏好；`renderedWidth` 是当前约束下的计算结果。被动 resize 不把后者回写到前者，重新放大应恢复偏好。
- 用户打开/收起意图、当前停靠/覆盖呈现、临时抽屉是否展开分别表达，避免自动挤压等价于永久关闭。
- 原有 Web 桌面正常宽度下的尺寸、Header 对齐和视觉保持；不要把 Desktop 的 320/560px 默认值直接搬过来。
- 分配器给出同一帧的全部区域结果；CSS 不得再用 `45cqw` 或另一套媒体规则覆盖框架宽度。

### 空间分配与不足时的策略

先检查 `Projects + Files + Main minimum + Agent + gutters` 是否能容纳当前偏好。可容纳时侧栏保持偏好，仅正文伸缩。不能容纳时，借鉴桌面的单侧回收顺序，先 Agent，再 Files，且保护各自下限。

当总最小空间仍不足，Web 需要显式呈现降级：优先让 Projects 转为窄 rail/抽屉；仍不足时 Files 转为左侧抽屉；在正文和 Agent 的最低双栏空间也不足时，Agent 转为 Header 下方右侧覆盖栏。Projects 究竟先用现有 56px rail 还是直接抽屉，是可独立替换的产品策略，不能散落到 CSS 中。

这些转换条件由当前打开区域、偏好与最小宽度推导，不能仅由设备品牌或固定的“手机/桌面”标签决定。正文下限应通过现有编辑器和文件视图验收确定，不把某个整数断点当作通用最佳实践。支持真实分屏段的设备还需把每个可用段和铰链作为约束；不支持时采用连续宽度布局。

### 组件与性能

- 工作区只在稳定宿主边界测量空间；用 ResizeObserver 驱动纯几何计算，避免反过来测量已经受分配结果影响的子栏造成反馈循环。忽略未变化的结果。
- 尺寸变化只通知区域外框；业务子树保持稳定。交互消费者订阅离散的 presentation，不订阅所有像素变化。
- Frame 决定占位与定位，Viewport 决定裁切/滚动，内容组件负责本身排版。收起动画可保持内容平面宽度；正常 resize 应允许文本合理重排，不能永久冻结内容。
- 工具栏和路径用自身容器宽度收缩/溢出；触控目标按 pointer/hover 能力增强。局部 CSS 继续负责内容布局，不把所有样式搬进 JavaScript。
- Header、Files、Agent 的 DOM 父级保持；不因尺寸切换路由、key、Portal 宿主或编辑器/Chat 实例。
- VisualViewport 的键盘/可见高度职责与栏位横向预算分开。

## 6. 实施与验收要求

实施顺序：先给纯分配器确定契约和场景测试，再接入唯一宿主测量与区域外框；随后删除旧框架宽度覆盖，并让焦点/手势使用同一呈现结果；最后回归现有桌面基线、手机与折叠屏场景。不同时重写文件读写、Chat 会话或路由业务。

必须覆盖以下几何不变量，而不是只测偏好值和 React 身份：

- 预算足够时左右栏保持用户宽度；关闭 Agent、收起 Projects、不存在 Files 时正确释放预算。
- 预算不足时遵守既定回收顺序，每一栏不低于相应呈现模式的下限。
- 拖拽宽度的最大值与实际布局约束一致，不允许 UI 显示可拖而 CSS 静默覆盖结果。
- 缩小后放大恢复偏好；显式打开/关闭与被动 resize 不混淆。
- 从普通窗口到窄窗口连续扫描，并测试每个实际派生阈值两侧；浏览器读取真实 computed style / bounding rect，与分配器结果对照。
- 固定桌面宽度的视觉基线不变；窄屏 Files 在左、Agent 在 Header 下方右侧，Header 保持可用。
- 原有实例、草稿、焦点、滚动、网络生命周期测试继续保留；它们不能替代几何验收。

第一轮 113 项单元测试的通过记录仍有效，但不构成上述尺寸契约已实现的证据。此诊断阶段只新增了文档和临时复现；后续实现与验证记录另列于下方，避免把设计当作已完成的验证。

## 7. 依据与适用范围

- [web.dev：Responsive web design basics](https://web.dev/articles/responsive-web-design-basics)：按内容需要选择断点，而不是针对设备型号。
- [web.dev：Container queries](https://web.dev/learn/css/container-queries)：组件响应自身容器空间。
- [MDN：flex-shrink](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/flex-shrink)：用于区分 Flex 负剩余空间分配与显式宽度覆盖；这里已定位到后者。
- [web.dev：Interaction](https://web.dev/learn/design/interaction)：输入能力与屏幕尺寸分开考虑。

统一尺寸分配器和具体回收/降级顺序是针对可拖拽多栏工作区提出的工程方案，不是所有网页都必须引入 JavaScript 布局引擎。现有 Next.js、React、Zustand 和 Grid 可继续使用。

## 8. 后续实现记录

统一分配器已接入 Web 的实际应用壳。删除 Agent 的 45cqw 覆盖及 Projects/Files/Agent 各自的布局断点；区域宽度、拖拽上限和交互呈现共用一个计算结果。原生窗口最小宽度约束没有移植到 Web。正常宽度下保留原 Web 尺寸。

实施时读取实际 `DataExplorerPane` 的默认与最小宽度为 220px，因此保留 220px。上文诊断表使用的是独立夹具中的通用 200px 列宽，只用于复现旧 CSS 机制，不作为当前桌面基线。

新增的真实 React/CSS 浏览器夹具在 Safari 验证了 26 个宽度及往返序列：实际栏宽与分配器一致，正文下限有效，Header 下方覆盖定位正确，编辑器/Chat DOM 与草稿保留，缩放额外业务 render 为 0；键盘拖拽、偏好恢复、关闭/重开 Agent 后的空间分配通过。该夹具不加载鉴权和文件/Agent 后端，不冒充整站端到端或折叠屏实机验收。

最终自动化验证：25 个测试文件、124 项单元/组件测试通过；TypeScript、主题颜色审计与 production build 通过。构建保留原有的 Supabase Edge Runtime、图片与依赖提示，未为本次布局重构升级依赖。设置弹窗的异步焦点断言改为等待焦点 effect 完成，避免并行构建负载下把内容已出现误当作焦点已完成。

文件版本/同步/局部编辑器面板也已加入统一预算，其宽度偏好从 Files 业务控制器下沉到区域外框。Agent 和局部编辑器同时打开时，两者都参与约束；覆盖层的焦点归属由同一分配结果决定。浏览器夹具追加 8 个局部检查面板宽度场景。
