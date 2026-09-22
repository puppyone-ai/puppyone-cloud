# 多用户协同 + Session 管理 实测结果(2026-06-07)

自主驱动 SSH/sandbox,在真实 E2B + 真实 PuppyOne git remote 上测了「多端多用户协同编辑」与「sandbox session 管理」。

---

## 1. Session 管理(`ScopeSandboxManager` + 真实 E2B)

| 验证项 | 结果 |
|---|---|
| **同 scope 多用户共用一个 sandbox** | ✅ alice/bob/carol acquire 同一 scope → 同一 `sandbox_id`,`connected_users={alice,bob,carol}` |
| **warm 复用** | ✅ 第 2、3 个用户 reuse **0.00s**(冷创建 1.03s)|
| **不同 scope 隔离** | ✅ scopeB(dave)是另一个 sandbox |
| **idle → stop,有人连着不回收** | ✅ release A 的用户 + reap → A 停;B(dave 连着)保留(`stopped=1 kept=1`)|
| **resume(有人回来)** | ✅ 0.28s,同一 sandbox 复用(via=resumed)|

→ session 管理的核心(scope 共享、热复用、隔离、按连接状态回收、快速 resume)实环境成立。

## 2. 多端并发 SSH

✅ 3 个 SSH 会话**同时**连入同一个共享 sandbox,全部并行成功(1.5–2.2s)。单个 websocat 转发(`ws-l:8081→tcp:22`)能接多个并发连接。

## 3. 协同编辑正确性/效率(真实 git remote)

测了一个 sandbox 内 alice/bob 各自 working tree + 中立验证克隆,经真实 PuppyOne git remote 并发编辑。

### 🔑 关键发现:PuppyOne git remote 强制线性历史
- **拒绝 non-fast-forward 推送**(标准):并发推同一 ref,第二个先 `! [rejected] (fetch first)`。
- **还拒绝 merge commit**:推合并提交报 `...merge commits are not supported; fetch and rebase onto the remote main branch`。
- **结论:并发协同的正确客户端工作流是 `git pull --rebase`(线性),不能用 merge。** 这对 Version Engine 的干净投影是合理设计。
- **落地影响**:sandbox 里 clone 时应默认 `git config pull.rebase true`(已在测试中采用);文档/CLI 引导用户用 rebase;同源 Git race 的文本冲突在本地 rebase 中解决。

### 用 rebase 工作流的实测(server 端中立克隆验证真相)

| 场景 | 流程 | server 真相 | 正确性 |
|---|---|---|---|
| **不同文件并发** | alice 推 a.txt;bob 推 b.txt→rejected→`pull --rebase`→push | `a=alice-v1 b=bob-v1` | ✅ 收敛,线性历史无 merge commit,无丢失 |
| **同文件并发** | 都改 shared.txt;alice 先推;bob rejected→`pull --rebase`→**冲突**→解决(keep-both)→`rebase --continue`→push | `shared.txt = base/alice-line/bob-line` | ✅ 两边改动都在,无丢失,两端收敛 |

**效率(耗时)**:push ~1.4–2.8s、`pull --rebase` ~1.6–2.4s、冲突解决 0.23s。交互体验可接受。

---

## 4. 结论

- **Session 管理**:scope 共享 + warm/resume + 隔离 + 按连接回收,实环境验证通过;效率高(reuse 0s、resume 0.3s)。
- **协同正确性**:基于 PuppyOne 的线性历史模型,**用 rebase 工作流**时多端并发编辑**正确收敛、无数据丢失**(不同文件自动、同文件冲突可解);server 是 source of truth,中立克隆验证一致。
- **并发性**:多用户共享 sandbox + 并发 SSH 均 OK。

## 5. 据此要做的(补进待办)
- **sandbox provision 时默认 `git config pull.rebase true`**(否则用户用默认 merge 会被 server 拒、困惑)。已是测试用法,应固化进 in-sandbox provisioning。
- 在 CLI/文档/前端引导:并发协同用 `git pull --rebase`;同源 Git race 的同文件冲突在本地 rebase 中解决。
- (承接之前的 5.1/5.2/5.3)session 状态外部化 + reaper 排程 + E2B 超时续期,才能让这套在生产长期稳定。
- 真正的「sandbox 内每用户独立 working tree + 每用户身份/auth」(共享盒里 push 归属到人)仍需结合凭证层落地。
