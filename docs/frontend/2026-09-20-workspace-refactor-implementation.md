# 工作区导航与会话重构实施记录

日期：2026-09-20。范围：Web frontend；桌面项目未改动。

## 基线与分支

开始时在 `codex/cloud-desktop-theme-parity`，与本地 `qubits` 同指向 `41ef8ada`。用户要求先保存全部现有工作，再实施重构，并最终进入 `qubits`。

现有工作保存为 `85fd6254`。GitHub 拒绝直接推送受保护的 `qubits`，因此通过 [PR #1367](https://github.com/puppyone-ai/puppyone-cloud/pull/1367) 正常检查后合入，合并提交为 `9afc37fe`。本次重构基于该合并提交单独交付。

## 已实施的所有权与生命周期

- `features/workspace/navigation` 是工作区站内导航入口。真实链接、命令式导航采用同一离开保护；当前身份由 Next 提供，pending 单独反馈。取消不更新最后访问项目或关闭项目抽屉；过时文件创建回调不能覆盖用户的新位置。
- Header 按已提交路由显示当前 Files/Git，导航中显示独立进度图标；删除 `pendingView` 冒充 active 的逻辑。
- `data/layout.tsx` 持有 Files 查询容器和 FilesWorkspace。路径 page 只保留 URL 入口；文件树不再随文件路径 page 替换而重建。删除 `clientPath`、页面 `pushState/popstate` 和全局 pending 文件选中副本。
- Files、Access、Git 与项目导入相关业务从 app 路由目录迁入 features；Auth context 移入 contexts。Git 的时间线、筛选、提交详情和展示模型独立成模块。边界测试禁止 feature 反向依赖 app 路由实现。
- 文件树展开和创建状态改为项目内的 ExplorerSession，Files/Git 切换保留，项目/身份更换隔离，不再使用进程级全局 Set/Map。账户内保留最多 16 个项目的文件树会话，维持返回项目时恢复展开状态的行为。
- 草稿和保存任务由账户级 EditorSessionProvider 持有，按原有文件 key 分离。页面卸载只取消订阅；保存任务继续更新原文件会话。后来的输入仍保留 dirty，晚到保存不能清掉另一实例持久化的新草稿。空闲会话有 32 项容量上限；活跃订阅或进行中的保存不被逐出。
- 草稿键增加账户作用域。旧键在首次实际恢复时迁移到记录的旧草稿账户，之后其他账户不会读取该旧键；旧格式不具备历史账户归属信息，这是迁移本身的边界。
- 多个编辑器的离开提示合并为一次确认。Access 设置跨同项目 Files/Git 仍保留，离开项目时加入同一次提示；浏览器 Back/Forward 依靠草稿恢复，刷新/关闭保留 beforeunload 保护，不修改浏览器历史栈模拟拦截。
- 项目正文 loading 不再绘制第二条 Header；Git 有独立 loading/error 边界。项目 Header 与 Chat 不属于正文错误/加载占位。
- 业务内部 Access 打开动作直接操作侧栏，Settings 保持弹窗。旧 Access/Settings URL 保留一次性兼容转换，`/history` 重定向至 `/changes`。

布局分配器、正常 Header CSS、响应式 CSS 和侧栏动画契约保持原样。已有 SWR 查询、请求取消和 WebSocket 失效机制继续使用；本次没有改后端数据库读取链，也不把前端一致性修复描述成后端延迟优化。

## 验证

- 全量前端单测：28 个文件、141 项通过；其中新增路由、编辑会话及跨项目/账户文件树回归。
- 类型检查和 production build 通过；构建仍有项目原有的图片及 hooks lint 警告。
- BFF、hydration、deployment contract 和 theme audit 通过。
- 回归覆盖：Git 请求不能提前成为当前视图、快速反向请求、面包屑只确认一次、命令式项目导航取消、多个 dirty editor 合并确认、修饰键点击、按真实 URL 恢复文件、旧创建回调失效、跨视图保存任务和新草稿、账户草稿隔离及旧格式迁移。
- 使用独立 production server 在 Dia 的已登录会话中，实际观察 `/data` → `/changes`：正文先显示加载，再显示 Git 提交历史及 diff；观察 Files 返回后 URL 与正文一致，Header 保持单行与分隔线。

真实浏览器验证期间工具多次报告用户同时操作浏览器，因此没有将完整慢网故障注入、所有 Back/Forward 组合、真实未保存文档交互及全部折叠屏尺寸声明为已完成浏览器端到端验收。相关异步/草稿逻辑由组件和会话测试覆盖；现有布局测试继续通过。

## 持续约束

路由入口负责组装；feature 不 import app 实现。工作区内部跳转通过 WorkspaceLink/useWorkspaceNavigation/useWorkspaceRouter；不要重新引入页面自己维护 history 或 document click capture。新增持久状态必须说明所属账户、项目或文件，以及卸载和清理规则。
