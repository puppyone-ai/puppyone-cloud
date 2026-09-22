# Scope-Sandbox 实施 Roadmap(优先级 + 优缺点,2026-06-07)

里程碑标尺:**M1 单用户可用 demo**(已达成,手动/脚本)· **M2 多用户受治理试点** · **M3 生产硬化**。
优先级:**P0** 阻塞正确性/feature 成立 · **P1** M2/M3 重要 · **P2** 优化/后置。

| # | 事项 | 优先级 | 价值 | 代价/风险 | 依赖 |
|---|---|---|---|---|---|
| 1 | in-sandbox git provision + rebase 默认 | **P0** | 让"连进去 scope 就在、协同正确"自动化(feature 本体)| 运行时安装慢、与 #6 模板重叠(现为临时版)| — |
| 2 | E2B 超时续期 + acquire reconcile (5.1) | **P0** | 修 15min 自杀/会话漂移,会话真正长存 | 碰 E2B lifecycle,需实测 | — |
| 3 | session 状态外部化 DB/Redis (5.3) | P0(生产)/P2(demo) | 多 worker/重启不丢会话 | schema 易返工(API 未定型) | — |
| 4 | reaper 排程 (5.2) | **P0(成本)** | 自动 stop/destroy 省钱;reap 逻辑已就绪 | 生产正确性依赖 #3;需 app manager 单例 | 3 |
| 5 | SSH 短期凭证签发/撤销 | P1(治理核心) | "离职即失权" + 审计到人 | 工作量大;demo 用静态 key 可绕 | — |
| 6 | 自定义 E2B 模板(烤入 sshd+websocat+sidecar) | P1 | 启动 6s→1s、resume 后 SSH 仍在 | 构建流水线;仅 E2B 侧 | 1 | **✅ 代码就绪**:`sandbox/scope-e2b/`(e2b.Dockerfile+toml+build.sh)烤入 sshd(硬化)+websocat+python+sidecar+`puppyone-ssh-up` 启动器;设 `SCOPE_SANDBOX_E2B_TEMPLATE` → provider 用该模板 + bootstrap 走 `fast_provision_steps`(免运行时下载/keygen/配置)。**唯 `e2b template build` 是外部计费步,待人工执行** |
| 7 | per-user working tree + 身份/auth | P1 | push 归属到人、协同归属正确 | 依赖凭证层 | 5 |
| 8 | 可观测(写 sync_runs/GAP-8)+ 调参 | P1/P2 | 用量可见 + 调 session 策略 | 调参需真实数据量 | — | **✅ 可观测已实现**:sidecar 持久化计数器(checkpoints/publishes/conflicts/integrations/holds/compactions/push_races + last_*_ts + chain_ahead),`status`(人读)/`metrics`(JSON)子命令暴露;服务端 `GET /scope-sync/stats` 聚合事件日志(publish 量、distinct origins/paths、per-source、latest head)。**调参半待真实用量数据** |
| 9 | HTTP API + 前端 provider 选择 | P1(产品化) | 用户能用的最后一公里 | 核心未稳前做=返工 | 1,2,5 |
| 10 | Fly 路径(镜像+IPv4+SSH) | P1/P2 | 推荐默认、原生 TCP SSH、便宜 | 卡绑支付+计费;E2B 已可验证可缓 | — | **代码侧就绪**:provider 生命周期+凭证+SSH 连接已 live-validated(2026-06-13);镜像烤入 sshd(硬化)+python3+git+procps+coreutils(sidecar 依赖齐全);connect 自动起 sidecar 走 self-detach(`setsid&`,Fly exec SSH 式存活,非 E2B background),`su -c` 嵌套引用已单测验证。**仅待**:生产公网 IPv4(绑卡 ~$3.6/mo)+ VSCode IDE 实走一遍 |
| 11 | legacy 与 V2 退役 (5.7) | **完成** | 单一 sandbox 产品边界 | 一次性与长驻生命周期仍是两个明确 mode | — | `infra/sandbox` 已迁入 `platform/scope_sandbox/execution`;所有调用方已切换。Agent turn 不再跨请求复用进程内 sandbox，长驻 scope session 默认 Supabase store。 |

## 推进顺序
- **第一梯队(让 demo 变真功能 + 会话长存,单进程即见效)**:**#1 + #2**。
- **第二梯队(M2 试点)**:**#3+#4**(外部化+reaper,后者依赖前者)、**#5+#7**(凭证+per-user 身份)。
- **第三梯队(M3)**:**#6 模板** → **#8 可观测+调参** → **#9 API+UI** → **#10 Fly**。
- **最后**:**#11** legacy 退役。

## 进度(2026-06-07)
- ✅ **#1** in-sandbox provision + rebase 默认(`scope_provision` + manager bootstrap)—— 实环境验证
- ✅ **#2** E2B 超时续期 extend + acquire reconcile —— 实环境验证
- ✅ **#3** Supabase 持久 session store + 迁移 `20260607000000_scope_sandbox_sessions.sql`(`SCOPE_SANDBOX_STORE=memory|supabase`)—— 单测(含 manager 跑在 DB store 上);**改 supabase 前需先应用迁移**
- ✅ **#4** reaper loop(`reaper.py`)—— 单测;调度接入 app 待 manager 单例(现 #3 已就绪,可接)
- ✅ **#5+#7** SSH 短期凭证签发/撤销 + per-user 身份(`ssh_credentials.py`)—— **实环境验证**:per-user public key 进 authorized_keys,带 `puppyone:user=<id>` 标签 + OpenSSH `expiry-time` 原生 TTL;grant=加行、revoke=删行(离职即失权)、过期=拒;per-user working tree `~/<user_id>` + git 身份。manager `revoke_hook` 接入 `revoke_user`(离职即撤 SSH,best-effort)。
  - ⚠️ **安全发现 + 修复**:E2B 默认模板的 socket-activated sshd **接受 SSH `none` 认证方法**(任何人都能进,无视 authorized_keys),静默瓦解凭证治理。已硬化 `ssh_e2b`:释放 systemd socket 占用的 :22,启动我们自己的 publickey-only sshd(`AuthenticationMethods publickey` / `UsePAM no` / `PasswordAuthentication no`)。实测 grant→可连、过期→拒、revoke→拒,bootstrap key 全程可连(拒绝是 per-key)。
- ✅ **#10 Fly —— 实环境验证(2026-06-13,免费额度)**:`FlyMachinesProvider` 全生命周期(create/exec/stop/start/destroy)+ 短期凭证 grant/revoke + **SSH 实连**(经 `fly proxy`/WireGuard:grant→可连、revoke→拒)全部在真实 Fly(app `puppyone-sandboxes`,region `sin`)跑通。镜像 `sandbox/scope-fly/` 远程构建推送(无本地 Docker)。**两个真 bug 修复**:① `hkg` region 已废弃→`sin`;② 镜像 `puppy` 账号被 `useradd` 锁定,OpenSSH 10 在 `UsePAM no` 下拒锁定账号 key 认证 → Dockerfile 加 `usermod -p '*' puppy` 解锁(仅 key 登录)。**公网 raw TCP `:22` 入口也已验证**(临时分配专用 IPv4 `$2/mo`,grant→可连/revoke→拒,测完立即释放);免费的 `fly proxy`/WireGuard 路径同样通过。剩 VSCode IDE 端走一遍。详见 `sandbox-fly-validation-2026-06.md`。
- 另:E2B 全链路 + **SSH(VSCode Remote-SSH)实环境打通**(`ssh_e2b`);多用户协同实测(发现 **PuppyOne 强制线性历史 → rebase 工作流**)

第二梯队完成(#3+#4 外部化+reaper,#5+#7 凭证+per-user 身份;Fly 代码跟进)。下一步(M3):**#6 自定义 E2B 模板**(烤入 sshd+websocat,免每次运行时硬化/安装)→ **#8 可观测+调参** → **#9 API+UI** → **#10 Fly 实连**(待绑支付/IPv4)→ **#11 legacy 退役**。

## (历史)当前动手:#1 + #2(+ #4)
理由:让"用户连进去就有一个能长期用、会自动省成本"的真 sandbox;三件都不大、风险低、可立即验证。
