# Scope-Sandbox 实环境验证结果 + 代码深度分析(2026-06-07)

针对 `backend/src/platform/scope_sandbox/`(两版本 Fly + E2B)的实环境连通测试、实际例子(成本/耗时/效果),以及当前实现的 bug / 未实现项 / HTTP·SSH 连接有效性分析。

---

## 1. 实环境连通结果

| 项 | 结果 | 说明 |
|---|---|---|
| **E2B 全链路** | ✅ 通过 | create/exec/pause/resume(connect)/kill 全部实连成功(经 E2BProvider + ScopeSandboxManager)|
| **E2B SDK 适配** | ✅ 已修正并验证 | 真实 SDK 无 `resume`;`kill/pause/connect/get_info` 是 by-id class-method variant;已据此改 `SdkE2BClient` |
| **Fly HTTP/auth/wire** | ✅ 通过 | `GET /v1/apps/puppyone-sandboxes/machines` → 200 `[]`;`status(不存在)` → DESTROYED(404 映射对);token(FlyV1,647 字符)有效 |
| **Fly create** | ⚠️ 仅缺镜像 | HTTP 400 `manifest unknown: unknown tag=latest` —— 请求被 Fly 接受,**只卡在镜像 `registry.fly.io/puppyone-sandboxes:latest` 不存在**;token/auth/billing 均非阻碍 |
| **专用 IPv4 / SSH 实连** | ⛔ 未做 | 需绑支付 + 构建带 sshd 的镜像;见 §4 |

---

## 2. 成本 / 耗时 / 效果(E2B 实测,2 次运行取第二次)

经 `scripts/scope_sandbox_e2b_smoke.py`,默认 E2B sandbox(~2vCPU/1GiB):

| 阶段 | 耗时 | 效果 |
|---|---|---|
| acquire(冷创建)| **0.94s** | 拿到 sandbox + 连接信息 |
| exec `echo` | 0.47s | rc=0 |
| exec `python3 --version` | 0.92s | **Python 3.13.13** |
| exec `uname -a` | 0.41s | Linux 6.1.158 x86_64 |
| exec `git clone --depth1 Hello-World` | 0.88s | rc=0,克隆成功(有 internet)|
| release + reap → **pause**(manager 驱动)| 0.38s | registry → STOPPED |
| acquire 再次 → **resume(connect)** | **0.33s** | **via=resumed** ✓ |
| exec `ls /tmp/hw`(resume 后)| 0.45s | **README 仍在 → 工作副本跨 stop/resume 存活** ✓✓ |
| destroy(kill)| 0.27s | 回收 |
| **总墙钟** | **5.03s** | |
| **估算计算成本** | **~$0.0002** | 仅 RUNNING 时计费;pause→$0 |

**关键效果验证**:
- **「stop 留盘」核心设计成立** —— pause 后 resume,`/tmp/hw/README`(git clone 的产物)仍在;resume 仅 0.33s(对比冷创建 0.94s + 重新 clone)。这就是 session 管理省 re-pull 的依据。
- 冷创建 ~1s、exec 亚秒级、pause/resume 亚秒级 —— 交互体验可接受。
- 成本极低(单次 5s 量级 ~$0.0002);真实成本由"warm 时长"主导,正是 session 策略要优化的。

---

## 3. HTTP 连接有效性

- **E2B:✅ 有效**(全链路实连验证)。
- **Fly:✅ 有效**(auth + base URL + 路径 + 头格式 + create 请求 + status 404 映射,均实连验证;create 仅因镜像缺失返回 400,wire 本身正确)。
- **现存 legacy `connectors/sandbox_endpoint` 的 exec(clone→跑→写回)** 是另一套 HTTP 路径(stateless),与 V2 并存(见 §5.7)。

---

## 4. SSH 连接有效性

- **E2B:✅ 已实环境打通(2026-06-07)。** 机制:sandbox 内 sshd:22 + websocat 服务(`ws-l:8081 → tcp:22`),客户端用 websocat 当 SSH `ProxyCommand` 经 `wss://8081-<id>.e2b.app` 隧道连入。本地 `ssh` 实连成功并执行命令(`hostname=e2b.local, user=user`)。VSCode Remote-SSH 用同一 `ProxyCommand`。固化在 `ssh_e2b.provision_e2b_ssh` + `scripts/e2b_ssh_demo.py`(create 0.92s + provision 6.67s)。默认模板(Debian13/非root+passwordless sudo/sshd预装),仅下载 websocat。
  - 生产优化:把 sshd+websocat **烤进自定义 E2B 模板**(免每次 6.7s 运行时安装)。
  - ⚠️ **安全发现(2026-06-07)**:默认模板的 socket-activated sshd(systemd `ssh.socket`)**接受 SSH `none` 认证方法** —— 客户端无需任何密钥即可登录(`Authenticated using "none"`),authorized_keys 形同虚设。这会静默瓦解凭证治理(grant/revoke/expiry 全部无意义)。**已修复** `ssh_e2b.provision_steps`:先 `systemctl stop ssh.socket ssh.service` + `pkill sshd` 释放 :22,再 `mkdir /run/sshd` 并启动我们自己的硬化 sshd(`AuthenticationMethods publickey` / `UsePAM no` / `PasswordAuthentication no` / `PermitEmptyPasswords no`)。**踩坑**:① 默认 sshd 由 systemd socket 占着 :22,我们的 `sshd -f` 静默绑不上 → 必须先释放;② 停服务会清掉 `/run/sshd` privsep 目录,启动前要重建。
- **Fly:⛔ 未实连(等绑支付 + 专用 IPv4),但代码已补齐(只写不测)。** `FlyMachinesProvider.exec`(Machines exec API,以 SSH 用户身份运行使 `~`/属主一致)+ 镜像 `sandbox/scope-fly/`(Dockerfile 烤入 publickey-only sshd@2222 + git + CLI + `puppy` 用户,README + 参考 fly.toml)。Fly 原生 raw TCP,无需 websocat,`proxy_command=None`;sshd 烤进镜像(非运行时硬化)。凭证/provision 与 E2B **共用** `ssh_credentials`+`scope_provision`(都走 `provider.exec`)。
- **SSH 凭证签发/撤销层:✅ 已实现 + E2B 实环境验证(`ssh_credentials.py`)。** per-user public key 进 authorized_keys,带 `puppyone:user=<id>` 标签 + OpenSSH `expiry-time` 原生 TTL(短期);grant=加行/续期、revoke=删行(离职即失权)、过期=sshd 自动拒。per-user working tree `~/<user_id>` + git 身份(归属到人)。manager `revoke_hook` 接入。**实测**:valid→可连、expired→拒、revoke→拒,独立 bootstrap key 全程可连(证明拒绝是 per-key 而非 sshd 坏了)。

→ **E2B 的 SSH 闭环 + 短期凭证签发/撤销已验证可用(含 VSCode 路径、离职即失权);Fly 代码已跟进待绑支付实连。**

---

## 5. 发现的 Bug / 健壮性缺口 / 未实现

### 已发现的真实缺口(建议尽快处理)

**5.1 [中] acquire REUSE 的 registry 漂移 + E2B 超时自杀**
manager 的 `acquire` 对 RUNNING 记录直接 reuse、不校验 provider 真实状态。若 sandbox 被**带外停掉/回收**,registry 会漂移:
- **E2B 尤其危险**:E2B sandbox 到 `timeout`(我们设 300s)会**自动 kill**(默认 lifecycle,非 pause)。300s 后 manager 仍以为 RUNNING → acquire reuse → exec(connect)发现已 gone → 报错。
- Fly machine 默认不自动停(除非配 auto_stop),漂移风险低;但若配了 auto_stop 同样会漂移,且 Fly reuse 一个 stopped machine 会失败(不像 E2B connect 会自动唤醒)。
- **修复方向**:(a) E2B create 设长 timeout + `lifecycle=pause`(或 manager 在 `touch` 时 `set_timeout` 续期);(b) acquire REUSE 失败时回退到 `start`(reconcile);(c) 让 manager 的 reaper 超时永远早于 provider 自身超时。

**5.2 [中] reaper 未排程**
`manager.reap()` 写好且测过,但**没有定时任务调用它**(scheduler 未接)。→ 生产里 sandbox 不会自动 stop/destroy。需加一个调度 job(类似 object_gc 的 scheduler 接法)。

**5.3 [中] 会话状态未外部化**
`InMemorySandboxSessionStore` 是进程内存 → **多 worker / 重启会丢全部 session**(与 server-centralized 原则冲突)。生产需 DB/Redis 实现 `SandboxSessionStore`(协议已留好)。

**5.4 [中] 离职撤权只做了一半**
`revoke_user` 只把用户从 session 跟踪里移除,**没有撤销 SSH 凭证 / 强制断开**(凭证层还不存在)。治理承诺(离职即失权)依赖 §4 的 SSH 凭证签发+撤销层。

**5.5 [中] sandbox 内 git/CLI 未自动 provision**
`spec.env` 能传 git remote 凭证,但**create 后没有自动:配 git remote(用 scope access key)+ clone scope 内容**。"所有 git/CLI 在 sandbox 内执行"还需一个 bootstrap 步骤(create 后 exec 初始化)。现在 sandbox 是裸的。

### 较小 / 行为说明

**5.6 [低] E2B `exec` 有唤醒副作用**:`SdkE2BClient.exec` 走 `Sandbox.connect(id)` → 对 paused sandbox 会**自动 resume**。即 exec 隐含"确保运行"。属预期但要知道(别在 stopped 状态下做 exec 当只读探活)。

**5.7 [已收敛] Sandbox 单一产品边界**:`infra/sandbox` 已退役。一次性 agent/endpoint 执行成为 `platform/scope_sandbox/execution` 模式，长驻 IDE workspace 使用同目录的 provider/manager；命令策略集中，长驻 session 默认使用 Supabase store，一次性执行在每个请求结束时写回并销毁。

### 未实现(对照 PUP §7,属计划内、非 bug)
HTTP API + 鉴权;SSH 凭证签发/撤销;前端 provider 选择 + 项目设置存储;DB-backed registry;reaper job;sandbox 镜像(sshd+git+CLI);Fly 专用 IPv4;可观测(写 sync_runs / 修 GAP-8);自适应 metrics 调参(目前阈值是静态默认)。

---

## 6. 已验证可用的部分(可放心建在其上)
- provider 抽象 + 三态生命周期(E2B 实连验证;Fly wire 验证)。
- session 策略(纯逻辑,52 单测)+ manager 全流程(warm/cold/stop/destroy/驱逐/离职跟踪)。
- 两 provider 的 HTTP/SDK 适配(E2B 全验证;Fly 除 create-需镜像外全验证)。
- factory 选择(fly|e2b)。

---

## 7. 建议下一步优先级
1. **修 5.1**(E2B 超时自杀 + reuse reconcile)——影响正确性,先做。
2. **5.3 DB-backed registry + 5.2 reaper job** —— 让 session 管理真正在生产生效。
3. **Fly 镜像(sshd+git+CLI)** + **5.5 in-sandbox provision** —— 打通"sandbox 即工作面"。
4. **SSH 凭证签发/撤销 + §4 SSH 实连(Fly 先行,需 IPv4)** + **5.4 离职撤权闭环**。
5. HTTP API + 前端 provider 选择。
6. 可观测 + 自适应策略调参(用实测数据)。
