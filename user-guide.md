# DAMF 元数据工厂 —— 配置与使用手册

适用对象：把这套系统装在生产/测试服务器上的人。每天用它干活的人请先看《使用说明.md》——那份按任务写、只用界面上的名字、不含子系统编号；本手册留作配置、运维与排障的底层参考，两边的界面名以《使用说明.md》为准。
本手册中的事实按 2026-10-25 这台部署（`/home/ubuntu/projects/DAMF`）逐项核对过；标了「未实现」或「已知限制」的地方是代码现状，不是笔误。

> **文档对齐：代码/契约 1.19.0（2026-10-25 校准）；S5 1.13.0 / S5M 1.14.1。**
> 路径数、分组与附录 A 一律以 `S1/docs/openapi.json` 为准：147 条路径、172 个操作。

---

## 0. 30 秒上手

```bash
cd /home/ubuntu/projects/DAMF
./install.sh --ip <公网IP> --port 1583 --mobile-port 1584 --api-port 8090   # 装（可反复跑）
./start.sh                                                                  # 起
./start.sh --status                                                         # 查
```

浏览器打开 **`https://<IP>:1583/`**（桌面）或 **`https://<IP>:1584/`**（手机）。
注意是 **https**。用 http 会得到 `400 Bad Request / The plain HTTP request was sent to HTTPS port`，见 §2.2。

账号口令：

```bash
printf 'admin / %s\n' "$(sed -n 's/^FACTORY_BOOTSTRAP_ADMIN_PASSWORD=//p' .deploy/env | tail -1)"
```

首次登录后 S1 会把该账号标成「需改口令」（`mustChangePassword`），请在「用户」页给自己换掉。
**换过之后这一串就只是历史值**：env 里的引导口令只在建号那一刻被读一次，重启不会把它写回账号，
拿它登录会得 401（§6.6、§10）。

**登录后第一件事：打开「开始使用」**（桌面 `/getting-started`，手机 `/start`）。刚装完的机器是「活着但什么都没配」，
这一页**一步一屏**：选厂商、录 API key、登记模型、登记 DHIS2 实例（含用户名与口令）、建项目 —— 全程在浏览器里做完，
**不必再 SSH 进服务器改 env**（值只发一次，加密后入库；想沿用 `env://` 也照样支持，见 §8.7）。
它同时会把 `.deploy/env` 里写错的变量名原样列出来。详见 §6.0。

---

## 1. 这套系统是什么

### 1.1 进程拓扑

```
                      ┌─ nginx（TLS 终结，自签证书）
  浏览器 ── https ─────┤   :1583 → /var/www/damf/s5   （S5 桌面控制台，静态站）
                      └─┤   :1584 → /var/www/damf/s5m （S5M 移动控制台，静态站）
                        │
                        └─ 同源反代 /api/v1 → 127.0.0.1:8090
                                                    │
                                          factory-api（S1 控制平面，uvicorn）
                                          只监听 127.0.0.1，进程内直接调用 S2/S3/S4
                                                    │
                                          factory-worker（周期任务 / Temporal worker）
                                          不开端口，与 api 共用同一个 EnvironmentFile
                                                    │
                                        PostgreSQL（5 个 schema：core dx bp ai/cp）
                                        本地磁盘 .deploy/state/objects（产物文件）
```

关键点：**没有独立的 S2/S3/S4 服务**；S1 把三个包 `import` 进来当库用（`FACTORY_PROVIDER_MODE` 决定用真实实现还是替身）。另有一个独立的 **`damf-worker`** 进程，只跑周期任务（`inprocess` 引擎）或当 Temporal worker（`temporal` 引擎），不开端口。API 只监听回环地址，公网只能通过 nginx 的 `/api/v1` 进来。

### 1.2 子项目分工

| 目录 | 包名 | 职责 | 技术 |
|---|---|---|---|
| `S0` | `factory_foundation` | 公共基础库：数据库/事务、产物（artifact）注册与对象存储、审计链、幂等、密钥引用、错误目录 | Python |
| `S1` | `factory_control` | 控制平面 = 唯一的后端 API。项目、工作流、阶段、Gate、部署运行、身份与权限、SSE | Python / FastAPI |
| `S2` | `factory_blueprint` | 蓝图（17 个 section 的版本化文档）与确定性编译：元数据包、OpenAPI、UI 规格、UID 分配、三层校验 | Python |
| `S3` | `factory_intelligence` | 调大模型：上传件 → NRR（归一化需求记录）→ 蓝图补丁，受数据外发策略与预算约束 | Python |
| `S4` | `factory_dhis2` | 唯一接触 DHIS2 的模块：环境档案、能力探测、discovery、VALIDATE/COMMIT 执行、快照与对账 | Python |
| `S5` | — | 桌面控制台（React 18 + @dhis2/ui + TanStack Query + Monaco + React Flow） | Vite / TS |
| `S5M` | — | 移动控制台（Vue 3 + Pinia + 自研 `fui` 组件库），与 S5 **不共用代码** | Vite / TS |
| `requirements/` | — | 10 份 SRS 与集成约定文档（需求编号如 `CP-GATE-012`、`UI-APP-008` 都出自这里） | Markdown |

`S1` 的对外契约版本：**1.19.0**，共 **147 条路径 / 172 个操作**（`GET /api/v1/openapi.json`；附录 A 与本行必须一致，改动契约时两处一起改）。请求 schema 里的枚举取值已写进 `enum`，两端下拉由生成的类型派生。

### 1.3 本机的实际状态（已核对）

| 项 | 值 |
|---|---|
| 对外端口 | 1583（S5） / 1584（S5M），均 TLS |
| API | `127.0.0.1:8090`，systemd 单元 `damf-api`，已 enable |
| 周期任务 | systemd 单元 `damf-worker`，与 API 同一个 `EnvironmentFile`，已 enable |
| nginx 站点 | `/etc/nginx/sites-enabled/damf.conf` → 源文件 `.deploy/nginx.conf` |
| 静态根 | `/var/www/damf/s5`、`/var/www/damf/s5m` |
| 数据库 | 库 `damf`，角色 `ubuntu`（unix socket peer 认证，配置里没有 DB 口令） |
| schema | `core` 11 表、`dx` 13、`bp` 15、`ai` 21、`cp` 25 |
| 后端能力 | `FACTORY_PROVIDER_MODE=real` —— S2/S3/S4 **真身**（替身模式下整条流程也能跑通，但产物是空的，见 §8.1） |
| 登录模式 | `FACTORY_AUTH_MODE=local` |
| 证书 | 自签，SAN 含 `IP:43.132.224.225, IP:127.0.0.1, DNS:localhost, DNS:<主机名>` |
| 主机防火墙 | `ufw` inactive —— 能否公网访问只由云安全组决定 |

`/healthz` 实况（本机此刻跑的是 S2/S3/S4 真身；`providers` 那一段就是唯一的真相来源）。界面上的「能力接入」读的就是这一段：`S2`/`S3`/`S4` 在两个控制台里分别显示为「蓝图编译」「需求解析」「环境执行」，`real`/`fake` 显示为「真实接入」「演示替身」——编号本身不上屏（S5-M SRS §2，V1.4.1）：

```json
{"status":"ok",
 "engine":{"name":"inprocess","durable":false,
           "detail":{"pendingStages":0,"note":"state is durable in cp; only the thread hand-off is not"}},
 "providers":{"S2":"real","S3":"real","S4":"real"},
 "startup":{"migrationsMode":"apply","appliedSchemas":["core","dx","bp","ai","cp"],"skippedSchemas":[],
            "providersInitialized":["S4","S2","S3"],"providersMissing":[],
            "registered":{"artifactTypes":3,"auditEventTypes":89,"errorCodes":18,"idPrefixes":24},
            "authMode":"local"}}
```

`registered` 那几个数是 **S1 自己声明的目录条目数**（`bootstrap.py` 取的是 `S1_ARTIFACT_TYPES` / `S1_AUDIT_EVENT_TYPES` /
`s1_error_codes()` / `S1_ID_PREFIXES` 的长度），**不是库里的行数**：真身接进来之后 `core.error_code` 有 47 行、
`core.audit_event_type` 有 146 行，因为 S2/S3/S4 各自也注册一批。把这两个口径混作一比，就会得出"注册少了 100 多条"
的假故障 —— 要查库里的实况，用 `select count(*) from core.error_code`。

**界面看不到 `/healthz`**：它不带 `/api/v1` 前缀，nginx 不代理它（§9.2）。控制台读的是同源的
`GET /api/v1/setup`（S5IF-SET01），它把这些事实加上「还差什么」一起回给你 —— 见 §6.0。

---

## 2. 访问与登录

### 2.1 地址

| 用途 | 地址 |
|---|---|
| 桌面控制台 | `https://<IP>:1583/` |
| 移动控制台 | `https://<IP>:1584/` |
| API 交互式文档 | `https://<IP>:1583/api/v1/docs`（免登录可访问） |
| OpenAPI 契约 | `https://<IP>:1583/api/v1/openapi.json`（免登录） |
| 后端进程 | `127.0.0.1:8090`，只在机器上可达 |

两个站点共用同一个后端和同一套账号；静态资源与 `/api/v1` 同源，因为 S1 没有 CORS 中间件（`UI-APP-008` / `UM-COMP-004`）。

### 2.2 那个 400：为什么必须 https

nginx 的监听是 `listen 1583 ssl`，端口只讲 TLS。用 `http://` 打过去，nginx 回的是它自己的 400 页面：

```
400 Bad Request — The plain HTTP request was sent to HTTPS port
```

这不代表服务坏了。`.deploy/nginx.conf` 里已加了一行兜底，把明文请求 301 到 https（`install.sh` 的 nginx 模板）：

```nginx
error_page 497 =301 https://$host:$server_port$request_uri;
```

所以今天 `http://<IP>:1583/` 会自动跳到 `https://<IP>:1583/`。

**为什么不能干脆用 http 跑**：S1 的会话 Cookie 写死 `Secure`（`S1/src/factory_control/rest/routes/identity.py` 的 `_set_session`）。浏览器只在 HTTPS（或 `http://localhost`）下保存 Secure Cookie，纯 `http://公网IP` 下你能登录但登录态留不住，每次刷新都是未认证。配置里 `FACTORY_HTTPS_ONLY=true` 也是硬校验：`FACTORY_AUTH_MODE` 不是 `off` 时它必须为 `true`，否则进程拒绝启动。

### 2.3 自签证书

`install.sh` 用 openssl 生成 825 天的自签证书，放在 `.deploy/tls/`。浏览器一定告警，点「高级 → 继续访问」即可。想消掉告警：

- 有域名：`certbot certonly --standalone -d your.domain`，然后把 `.deploy/tls/cert.pem`、`key.pem` 换成 certbot 的 fullchain/privkey，`./start.sh --restart`；或改 nginx 站点直接指向 `/etc/letsencrypt/live/...`。换完记得 `./install.sh --hostname your.domain` 把域名加进 `server_name`。
- 只有 IP 且想让内网机器不告警：把自签 CA 装进各机器信任库（证书带 `basicConstraints=CA:TRUE`，可以当 CA 用），`openssl x509 -in .deploy/tls/cert.pem` 就是它。

证书只在「文件缺失」或「SAN 变了」或加了 `--renew-tls` 时才重签。换了公网 IP 请 `./install.sh --ip <新IP>`，SAN 变化会自动重签。

### 2.4 两种登录模式

| 模式 | 配置 | 用户看到什么 | 权限 |
|---|---|---|---|
| `local`（默认） | `FACTORY_AUTH_MODE=local` | 两端都是 `/login` 页（账号 + 口令）：S5 桌面与 S5M 移动各自把无会话的请求带回这一页，页上说明为什么会到这里 | 按角色判定，越权返回 403 |
| `off` | `FACTORY_AUTH_MODE=off` | 不问账号，只问名字（S5「请输入你的名称」/ S5M「我是谁」） | `AllowAllPolicy`，人人全权 |

代码还支持 `oidc` 与 `both`，**两端的界面都已经实现这一半**：`authMode` 为 `oidc`/`both` 时，桌面登录页与手机 `/login` 都出现「使用组织账号登录」，浏览器带 S1 给的 `client_id / redirect_uri / response_type / scope / state / nonce` 出门，回来各自落在 `/auth/callback` 用 `code` + `state` 换会话（`state` 由 S1 与它签发时写下的 HttpOnly cookie 比对，界面无从伪造）。`FACTORY_AUTH_MODE` 的代码取值是 **`off` / `local` / `oidc` / `both`**；但 **`install.sh`/`start.sh` 的 `--auth` 只接受 `local|off`**。开 OIDC 需要手工改 `.deploy/env` 并补 `FACTORY_OIDC_ISSUER`、`FACTORY_OIDC_CLIENT_ID`、`FACTORY_OIDC_CLIENT_SECRET_REF` 三项（`validate_runtime()` 会逐项点名缺哪个），且仓库内从未真跑过一次与真实 IdP 的往返（`S1/README.md` 自己标了未验证）——端到端测的是界面行为，替身回答的是 S1 的形状。

`off` 模式给写操作必须带 `X-Operator` 头，否则 400 `OPERATOR_REQUIRED`；`local` 模式下即使带了 `X-Operator`，也会用会话里的 subject 覆盖它（防伪造审计）。

`X-Operator` 的取值规则（CP-API-002）：HTTP header 只允许 ISO-8859-1，**浏览器发不出中文姓名** —— Chromium 在 `Headers.set` 就抛 `TypeError`，请求根本不到服务器。所以两端对超出 Latin-1 的姓名先做百分号编码再发（`张三` → `%E5%BC%A0%E4%B8%89`），S1 在门口按 UTF-8 解码，审计落的是 `张三`；装得下 Latin-1 的姓名（`Renée`）走原样。**用 `curl` 时两种写法都行**：`-H 'X-Operator: 张三'` 按 UTF-8 字节发出（传输层把它读成 `å¼ ä¸‰`，S1 再按 UTF-8 还原），或自己写成 `-H 'X-Operator: %E5%BC%A0%E4%B8%89'`，两种都会以同一个姓名进审计。

另外 `off` 模式下这几类写入直接 409 `FEATURE_NOT_ENABLED`：项目成员增删、产物读取授权、生产豁免。要放开就设 `FACTORY_M4_FEATURES_ENABLED=true`；`local`/`oidc`/`both` 下身份已被强制，M4 写入按角色表放行，不再需要这个开关。

### 2.5 会话与口令规则

| 规则 | 值 | 来源 |
|---|---|---|
| 空闲滑动超时 | `FACTORY_SESSION_IDLE_MINUTES=30`，每次请求续期 | `.deploy/env` |
| 绝对上限 | `FACTORY_SESSION_ABSOLUTE_HOURS=12`，续期越不过它 | `.deploy/env` |
| 会话 Cookie | `factory_session`，HttpOnly + Secure + SameSite=Strict | `FACTORY_COOKIE_NAME` |
| CSRF Cookie | `factory_csrf`，可读（刷新后仍能取到） | `FACTORY_CSRF_COOKIE_NAME` |
| CSRF 头 | `X-CSRF-Token`，Cookie 认证的 POST/PUT/PATCH/DELETE 必带，否则 403 `CSRF_FAILED` | `FACTORY_CSRF_HEADER` |
| 口令长度 | 最少 8 位，**没有**复杂度要求 | `identity/passwords.MIN_PASSWORD_CHARS`，由 `create_user` 与 `set_password` 共用 |
| 失败锁定 | 连错 5 次锁 15 分钟 | `identity/authn.py` 的 `MAX_FAILED_ATTEMPTS` / `LOCK_MINUTES` |
| 存储 | `scrypt(N=2^14,r=8,p=1)`；会话只存 SHA-256 摘要 | `identity/passwords.py` |

口令长度这条**必须**落在服务层：只写在请求 schema 上的话，`POST /users`（它的 `password` 没有约束）能建出
一个 1 个字符口令的账号，而 bootstrap 管理员是在进程内直接调用 `create_user` 的，schema 从来管不到它。

失败计数走的是**自己的一条事务**，不是认证那一个。这不是洁癖：登录失败要抛异常，而工作单元见到异常就
回滚，计数因此连同这一次尝试一起被抹掉——表现是"每次都是 401，但永远锁不上"，等于没有速率限制。

S5 与 S5M 的过期体验现在是一致的：任一读取或写入回答 401，界面就把会话收成"未登录"并弹回登录入口，
**只报一次**（一个过点的会话是一个事件，不是一屏问题）。顶栏 / 「账号与会话」还会提前显示到期时间。

**改自己的口令**：S5 顶栏「修改口令」，S5M「账号与会话」里同名入口——本人改自己的口令不需要
`user.manage`，两边都一直允许调用，此前只是没地方调。改完**必然要重新登录**：S1 在写入口令时吊销该账号
的全部会话（包括你正在用的这一条），两个界面都把这句话说在按下去之前。被标记
`mustChangePassword` 的账号会在入口上多一个 ⚠ 和一行原因；这一条是**报告位**，服务端不拦任何调用，
要收紧成硬拦是一个明确的后续版本决定。

**被锁了怎么知道**：登录的回答故意不区分"口令错"与"账号被锁"（区分开来就是账号枚举通道）。
管理员在用户列表上看得到——`locked` 那一列/标记会写「已锁定」，重置口令即解锁并顺带吊销该账号所有会话。
停用是 `PATCH /users/{id}`，**账号不能删除，只能停用**。

---

## 3. 安装：`install.sh`

### 3.1 前置条件

- Ubuntu / Debian 系（脚本按 apt/dpkg 写，别的发行版直接 die）。
- root 或可用的 sudo —— 要写 `/etc/nginx`、`/etc/systemd/system`、`var/www`。
- 目录 `S0..S5M` 都在（必须在仓库根目录跑）。
- 能出网装 apt 包、NodeSource、npm 包。
- 内存够跑两次 Vite 构建（S5、S5M 各一次）。

脚本会自己补齐：`git curl ca-certificates openssl python3 python3-venv python3-pip postgresql nginx`，Node 低于 20 或缺失时装 NodeSource 22（Vite 6 要求 `^20.19 || >=22`）。

### 3.2 一次典型安装

```bash
./install.sh --ip 43.132.224.225 --port 1583 --mobile-port 1584 --api-port 8090
```

跑完不会启动服务（脚本最后一行说得很清楚：注册了 systemd 但「尚未启动，交给 start.sh」）。接着：

```bash
./start.sh --seed     # 启动 + 灌一个演示项目
```

### 3.3 它按顺序做的十件事

| # | 步骤 | 产物 | 跳过开关 |
|---|---|---|---|
| 1 | 前置检查 | 校验发行版、目录、sudo、解析 `--ip` | `--render-only` 整段跳 |
| 2 | 系统依赖 | apt 装缺失包 | `--skip-apt` |
| 3 | Node 运行时 | NodeSource 22（若需要） | `--skip-build` |
| 4 | Python 环境 | `S1/.venv`，先装第三方依赖，再 `--no-deps -e` 装 S0..S4 五个本地包，最后导入自检 | `--config-only` |
| 5 | 前端构建 | `S5/dist`、`S5M/dist`（`npm ci` + `npm run build`） | `--skip-build` |
| 6 | 静态落地 | 整目录换掉 `/var/www/damf/{s5,s5m}`（先复制到 `.next`，再把旧的挪走、新的挪进来），`chmod -R a+rX` | `--config-only` |
| 7 | PostgreSQL | `enable --now`，建角色（默认同名 OS 用户，socket peer）、建库 `damf`，并用 systemd 将用的那个身份实测能否连上 | `--config-only` |
| 8 | 运行时配置 | 生成/更新 `.deploy/env`（首次才生成口令），同步端口与模式 | — |
| 9 | TLS 证书 | `.deploy/tls/{cert.pem,key.pem,san.txt}` | — |
| 10 | nginx + systemd | 渲染 `.deploy/nginx.conf`、`.deploy/damf-api.service` 与 `.deploy/damf-worker.service`，写入系统，`nginx -t` 通过才 reload；两个单元都 enable | `--render-only` 只渲染不落系统 |

第 6 步为什么要复制到 `/var/www`：nginx worker 是 `www-data`，而仓库常在 `/home/<user>`（750 进不去）。不复制就得放宽家目录权限。
第 6 步为什么是**整目录换**而不是往旧目录里叠加：chunk 文件名带内容哈希，每次构建都换名字，旧文件不会被新的 `index.html` 引用，看起来无害 —— 但它们会一直堆着（本机量过：一次部署后 `/var/www/damf/s5m/assets` 里躺着 592 个 js，桌面端 29 个），而缓存里还留着旧 `index.html` 的浏览器会继续拉到旧 bundle，症状正是"我看到的界面跟这次部署的不一样"。改成 `.next` → 换名 → 删 `.prev` 之后，同一目录从 592 降到 61（桌面 29 → 5）。
第 4 步为什么 `--no-deps`：五个包互相以 `factory-*` 声明依赖，而 S1 钉死 `factory-foundation==0.1.0`、S0 实际已是 0.1.2，让解析器自己去装必失败。

### 3.4 开关全表

| 开关 | 默认 | 作用 |
|---|---|---|
| `--ip` | `hostname -I` 第一个 | 写进证书 SAN 与 nginx `server_name` |
| `--hostname` | 无 | 追加域名（有域名建议改用 certbot） |
| `--port` | 8443 | S5 对外端口 |
| `--mobile-port` | 8444 | S5M 对外端口 |
| `--api-port` | 8080 | factory-api 本机端口（只 127.0.0.1） |
| `--auth local\|off` | local | 登录模式 |
| `--providers auto\|fakes\|real` | auto | 后端能力，见 §8。给 `real` 时先逐个 import 检查 S2/S3/S4，缺谁就中止并点名（不是"退化成替身继续装"）；`auto` 缺谁只是警告一句 |
| `--db-user` | 当前用户 | PG 角色名；与登录用户不同会导致 peer 认证失败 |
| `--skip-build` | — | 跳过 apt node + 前端构建 |
| `--skip-apt` | — | 跳过系统包安装 |
| `--renew-tls` | — | 强制重签证书 |
| `--render-only` | — | 只渲染 `.deploy/` 下三个文件，不碰系统目录 |
| `--config-only` | — | = `--skip-apt` + `--skip-build`，并跳过建库/venv/静态落地，但**会**落 nginx + systemd |
| `--dry-run` | — | 特权动作只打印 |
| `-h/--help` | | 帮助 |

校验规则：端口必须是 1–65534 的数字；`--port` ≠ `--mobile-port`；`--api-port` 不等于任何一个对外端口。

### 3.5 幂等，以及一条很重要的「配置回读」规则

裸跑 `./install.sh`（不带任何开关）**不会**把端口重置成脚本默认的 8443/8444。未显式给出的开关，脚本从已生效的配置里读回来：

| 值 | 从哪读回 |
|---|---|
| `--port` / `--mobile-port` | `.deploy/nginx.conf` 里第 1、2 个 `listen N ssl` |
| `--api-port` | `.deploy/env` 的 `FACTORY_API_PORT` |
| `--auth` | `FACTORY_AUTH_MODE` |
| `--providers` | `FACTORY_PROVIDER_MODE` |
| `--ip` | `.deploy/tls/san.txt` 里第一个 `IP:` |

`.deploy/env` 的写入是就地替换（`env_set`），**值没变就绝不碰文件** —— 因为 `start.sh` 用这个文件的 mtime 判断「配置是否已生效」。所以别手工 `touch .deploy/env`，那会让下次 `start.sh` 白重启一次。

结论：**`.deploy/nginx.conf` 和 `.deploy/damf-api.service` 是生成物，别手工编辑**；要改就跑 `install.sh` 或 `start.sh` 带开关。

---

## 4. 日常启停：`start.sh`

### 4.1 动作

| 命令 | 做什么 |
|---|---|
| `./start.sh` | 同步配置 → 先起/重启 `damf-api` → 等 `/healthz` → 再起/重启 `damf-worker` → 自检 → 打印访问信息 |
| `./start.sh --restart` | 强制重启两个单元（改了 `.deploy/env` 里的手工项后用） |
| `./start.sh --stop` | 先停 `damf-worker` 再停 `damf-api`。**nginx 不停**，只是没有后端 |
| `./start.sh --status` | `damf-api` / `damf-worker` / `nginx` 三个服务状态、监听端口、`/healthz`、两个站点 https 探活 |
| `./start.sh --logs` | `journalctl` 看两个单元（api + worker），默认各 60 行；`LOGLINES=200` 改行数，`UNIT=damf-worker ./start.sh --logs` 只看一个 |

### 4.2 开关

`--port --mobile-port --api-port --ip --hostname --auth --providers --renew-tls` 含义与 `install.sh` 一致；`--seed` 灌演示数据；`--no-reconfigure` 只启停不重渲染；`--dry-run` 跳过特权动作。

未给的开关同样回读已生效配置，规则见 §3.5。

### 4.3 它自己做的判断（值得知道，避免困惑）

1. **带配置开关跑 = 必然重启**（`CONFIG_DIRTY`）。
2. **`.deploy/env` 比进程新 = 也重启**。它比较 `env 文件 mtime` 与 `/proc/<MainPID>` 的时间戳；systemd 对已运行进程执行 start 是空操作，靠这招让手工改的配置真正生效。
3. 每次启动都以 `--config-only` 回头跑一遍 `install.sh` 做配置同步，所以改端口/模式不需要重新构建前端。
4. 然后：等 `/healthz` 最多 60 秒 → 超时就把日志尾部 30 行打到 stderr 并退出；通过后才启动 `damf-worker`（迁移没有并发锁）。
5. 自检项：`authMode` 是否等于期望值；`/healthz` 里的 S2/S3/S4 提供方；管理员能否登录（local 模式）；两个站点静态 200 且同源 `/api/v1` 可通。

### 4.4 `--seed` 演示项目

它做的事：登录 → 读 `/environments` → 挑目标环境（自动选一个非 PRODUCTION 作设计基准）→ `POST /projects`（模式 A，名字 `demo-outpatient-MMDD-HHMM`）→ 上传一份 markdown 需求 → 跑当前阶段。

两个前提，踩坑前先看清：

- **一个环境都没有时它不灌**，并提示：`--providers auto` 下请先在界面「环境」里登记。界面现在能登记环境（§8.5），照提示做即可；也可以直接 `curl`。
- 需要一个非 PRODUCTION 环境，全是生产环境也不灌。

`fakes` 模式下 `FACTORY_FAKE_ENVIRONMENTS=dev:TEST,prod:PRODUCTION` 会预先注册 `dev`/`prod` 两个替身环境，`--seed` 开箱可用；本机已切到 `real`，`--seed` 用的是已登记的真实环境。

### 4.5 三个常用配方

```bash
# 换对外端口
./start.sh --port 9443 --mobile-port 9444        # 会重渲染 nginx、reload、重启后端

# 换公网 IP（证书 SAN 会变 → 自动重签）
./install.sh --ip 1.2.3.4 && ./start.sh --restart

# 只改 env 里的某项（例如会话时长、模型口令）
vim .deploy/env && ./start.sh --restart
```

---

## 5. 配置详解

### 5.1 `.deploy/env`（systemd EnvironmentFile，0600，含口令）

| 键 | 本机值 | 说明 / 可选值 |
|---|---|---|
| `DATABASE_URL` | `postgresql://ubuntu@/damf?host=/var/run/postgresql` | 必填，空值直接拒启。走 unix socket + peer 认证，所以串里没有口令。也可以写 `postgresql+psycopg://...` |
| `OBJECT_STORE_URL` | `file://<repo>/.deploy/state/objects` | `memory://`（重启即丢）/ `file://` 或 `local://` / `s3://bucket[/prefix][?endpoint=...]`（需 boto3）/ `http(s)://endpoint/bucket/prefix` |
| `SECRET_BACKEND` | `env` | **装饰用**。被解析、不被任何代码使用；实际注册的后端是 `env` + `memory`（S0 的 `secrets` 包），另加一个只读的 `db`——仅当 `FACTORY_CREDENTIAL_MASTER_KEY` 存在时注册 |
| `FACTORY_API_HOST` | `127.0.0.1` | 默认 `0.0.0.0`；别放开，前面有 nginx |
| `FACTORY_API_PORT` | `8090` | 与 nginx `upstream` 同源，由 `--api-port` 维护 |
| `FACTORY_MIGRATIONS_MODE` | `apply` | `apply`（按 `core→dx→bp→ai→cp` 顺序 upgrade 到 head）/ `check`（不执行 DDL，只比对版本，给只读副本用）/ `off` |
| `FACTORY_WORKFLOW_ENGINE` | `inprocess` | `inprocess`（API 进程内 4 线程池；worker 进程同时跑周期任务）/ `temporal`（需 Temporal，配 `FACTORY_TEMPORAL_*`） |
| `FACTORY_PROVIDER_MODE` | `real` | `auto` / `fakes` / `real`，见 §8 |
| `FACTORY_FAKE_ENVIRONMENTS` | `dev:TEST,prod:PRODUCTION` | **只在替身执行器生效**，`real` 下惰性 |
| `FACTORY_AUTH_MODE` | `local` | 代码取值 `off` / `local` / `oidc` / `both`；脚本的 `--auth` 只写 `local` 或 `off` |
| `FACTORY_HTTPS_ONLY` | `true` | `AUTH_MODE≠off` 时必须为 `true`，否则拒启 |
| `FACTORY_SESSION_IDLE_MINUTES` / `_ABSOLUTE_HOURS` | 30 / 12 | ≥1 |
| `FACTORY_BOOTSTRAP_ADMIN_SUBJECT` / `_SECRET_REF` / `_PASSWORD` | `admin` / `env://FACTORY_BOOTSTRAP_ADMIN_PASSWORD` / （口令） | 前两个必须成对。启动时**仅当该账号不存在**才创建，角色 `PLATFORM_ADMINISTRATOR`，`mustChangePassword=true` |
| `FACTORY_CREDENTIAL_MASTER_KEY` | （安装时生成的 64 位十六进制） | **界面录入凭证的主密钥**：HKDF 派生出 AES-256-GCM 的库密钥，密文存 `cp.secrets`（FR-SEC-006、CP-SEC-001）。没有它 = 关掉界面录入（相关端点回 503，服务照常）；改它 = 库里已存凭证全部解不开（`keyMismatch`），要逐个重录。它本身不入库、不回显 |
| `MODEL_API_KEY` 等注释行 | 注释状态 | 走「环境变量」这条路接真实模型/DHIS2 时取消注释填值，界面里用 `env://键名` 引用。另一条路是界面直接录入（`db://键名`，见 §6.0、§8.5），两条可混用 |

其它常用可调项（都有代码默认值，写在 env 里即覆盖）：

```
FACTORY_COOKIE_NAME / FACTORY_CSRF_COOKIE_NAME / FACTORY_CSRF_HEADER
FACTORY_SSE_CHANNEL=factory_cp_events  # NOTIFY 与 LISTEN 两侧同读这一项；必须是 PostgreSQL 标识符
FACTORY_SSE_POLL_SECONDS=15        # 静默多久触发一次兜底 notify_all
FACTORY_SSE_HEARTBEAT_SECONDS=20   # ": keep-alive" 心跳
FACTORY_AUDIT_LINK_INTERVAL_SECONDS=5      # link_audit 的回填间隔
FACTORY_NOTIFICATION_MAX_ATTEMPTS=5        # 邮件/Webhook 投递重试上限
FACTORY_CREDENTIAL_EXPIRY_WARNING_DAYS=14
FACTORY_M4_FEATURES_ENABLED=false  # 免登录模式下放开成员/授权/豁免写入
FACTORY_DEFAULT_MAX_UPLOAD_BYTES=104857600        # 仅在首次播种时生效，见 §5.4
FACTORY_DEFAULT_ALLOWED_UPLOAD_TYPES=<见 S1/src/factory_control/config.py 的 default_allowed_upload_types>
FACTORY_SMTP_SECRET_REF / FACTORY_WEBHOOK_SECRET_REF
```

`FACTORY_SSE_CHANNEL` 的合法性在 `validate_runtime()` 就检查（字母/数字/下划线，且不能以数字开头）：NOTIFY 和 LISTEN
读同一个值，改错不会"只断一半"，而是进程直接拒绝启动。`FACTORY_TEMPORAL_RETENTION_HOURS` **已从配置删除**：
retention 是 Temporal namespace 的属性，而代码库里不存在 namespace 创建逻辑，留一个没人读的键就是假配置。

**`APP_ENV=production` 现在可以设了**（本轮修复）：bootstrap 在生产档只挂 memory 后端，进程照常起。语义要想清楚——`env://` 密钥引用在这条路上按 FD-SEC-001 的设计**解析必失败**，凭证要走 `db://` 主密钥或部署侧注入；S0 的 `install_development_backends()` 本身仍然拒绝在 production 装 env 后端，那是正确行为，改的是 S1 的调用姿势。凭证库与这一条**无关**：`db` 后端只看 `FACTORY_CREDENTIAL_MASTER_KEY` 在不在。

### 5.2 改完怎么生效

| 改了什么 | 生效方式 |
|---|---|
| `.deploy/env` 任意项 | `./start.sh --restart`（直接跑 `./start.sh` 也会因 mtime 比对而重启） |
| 端口 / 模式 / IP | `./start.sh --port ...` 之类，自己会重渲染 + reload nginx |
| `.deploy/nginx.conf` | 别手改；跑 `./install.sh --render-only` 再 `--config-only` 落系统 |
| 前端代码 | `./install.sh`（会构建并复制到 `/var/www`）；只改 S5/S5M 时不必重建后端 |
| Python 代码 | 改了 `S0..S4` 源码即时生效（venv 里是 `-e` 可编辑安装），但需要 `./start.sh --restart` |

### 5.3 生成的文件落在哪

| 源（仓库内） | 系统位置 |
|---|---|
| `.deploy/nginx.conf` | `/etc/nginx/sites-available/damf.conf` + `sites-enabled/` 软链 |
| `.deploy/damf-api.service` | `/etc/systemd/system/damf-api.service` |
| `.deploy/damf-worker.service` | `/etc/systemd/system/damf-worker.service` |
| `.deploy/env` | 不复制，systemd 用 `EnvironmentFile=` 直接读 |
| `.deploy/tls/{cert,key}.pem` | 不复制，nginx 站点直接引用仓库内绝对路径（`key.pem` 0600） |
| `S5/dist` → `/var/www/damf/s5`，`S5M/dist` → `/var/www/damf/s5m` | |

systemd 单元要点：两个单元都是 `User=<安装时的登录用户>`（本机 `ubuntu`）、`WorkingDirectory=<repo>/S1`、`Restart=always`、`TimeoutStartSec=180`、`NoNewPrivileges=true`，日志进 journal；API 退避 3 秒，worker 退避 5 秒。worker 不开端口，只跑周期任务或当 Temporal worker。
`User` 必须与 `DATABASE_URL` 里的 PG 角色同名，peer 认证才成立 —— 换用户跑就重建单元。

### 5.4 运行期设置不在 env 里

这些是数据库表 `cp.system_settings` 的行，首次启动从 `FACTORY_DEFAULT_*` **播种一次**，之后改 env **不会回灌**（只补缺失键）。改它们是走界面「系统设置」或 `PUT /api/v1/settings`（有审计）：

| 键 | 默认 |
|---|---|
| `max_upload_bytes` | 100 MiB |
| `allowed_upload_types` | pdf / csv / txt / docx / xlsx / json / md / doc |
| `idempotency_retention_hours` | 24 |
| `event_retention_days` | 7 |
| `default_gate_reminder_hours` | 24 |
| `archived_project_retention_days` | 0（关闭产物清理） |
| `platform_notification_channels` | 空 |

想改上传上限：改界面里的值，或先把 `cp.system_settings` 里那行删掉再改 env 重启。**别只改 env**，那没作用。

---

## 6. 使用：S5 桌面控制台

### 6.0 从零到第一次提交（冷启动）

`install.sh` + `start.sh` 跑完，机器是「活着但什么都没配」的状态：库建好了、迁移跑过了、系统设置有了默认值、
引导账号在，但**没有环境、没有模型、没有项目**。两端控制台都有同一张清单来收这个尾：

- 桌面：左侧 **开始使用** → `/getting-started`
- 手机：`/start`（≥1024px 在左栏；手机上走「我的 → 开始使用」，工作台顶部那张卡也进得去）

清单的每一项只由两个来源决定，界面自己不推断业务状态（UI-BR-02 / UM-BR-02）：

| 来源 | 提供什么 |
|---|---|
| `GET /api/v1/setup`（S5IF-SET01） | 三方子系统是真实还是替身、`validate_runtime()` 的配置问题、环境的登记数与**是否做过连接测试**、提供方/模型计数、项目数、认证体制、「初始口令还没改」、凭证库是否可用与哪些引用已解不开（`credentials`） |
| `GET /api/v1/allowed-actions`（平台作用域） | 每个按钮能不能点，以及不能点的原因码 |

**一步一屏**（契约 1.7.0 起，1.15.0 重排）：顶部一条步骤条，下面一次只展开当前这一步，需要登记的东西就在这一屏里填完，
填完自动走下一步——**不必再离开浏览器去 SSH 改文件**。清单只有**四步**，顺序按"没有它就什么都跑不动"排：
模型排在第一位（没有可用模型时每个需要模型的阶段都在开始前失败），提供方与模型同在这一节里登记（模型必须挂在
提供方上，拆成两步只会让第二步去猜第一步）。按这个顺序走：

| # | 步骤名 | 做什么 | 还要手工吗 |
|---|---|---|---|
| 1 | 「模型提供方与模型」 | 一屏里两张表单：选厂商（目录带出地址与常用模型名，也能自由输入）→ 就地录 API key（**本机 Ollama/vLLM/LM Studio 这一栏直接留空**，它就是不需要密钥）→ 登记模型并启停。已登记的模型就在治理的 `模型` 一节里改（`/governance/models`，S5IF-GOV06） | 不用。key 走 `PUT /secrets/{name}` 加密入库，界面之后只见 `db://名字`（§8.5） |
| 2 | 「DHIS2 环境」 | 内嵌环境表单：地址、类别（开发 / 测试 / UAT / 生产）、认证方式、各凭证范围，用户名与口令或令牌**就地录入**；登记完就在同一屏点「测试连接」与「能力探测」 | 不用。愿意把值留在环境变量里也可以，填 `env://键名` 一样收 |
| 3 | 「第一个项目」 | 给入口与（被拦时的）不可用原因；向导是页面，仍跳转 | 不用。但项目至少要指向一个**非生产**环境（`CP-PRJ-002`），向导会在环境那一步就说明 |
| 4 | 「跑阶段、审批、正式提交」 | 指向项目工作区，开始第 6 节那条主干 | 不用。生产提交还需要审批人与豁免（§6.6） |

清单顶部如果有一条红框「服务端发现的配置问题」，那是 `validate_runtime()` 的句子**原样**显示：点名要改的是哪个
`FACTORY_*` 变量，不含取值，改完由管理员 `./start.sh --restart`。它是页面上的一条事实，不是一步——没有"做完"
这个动作，所以不占步骤号。

**清单里没有「确认三方子系统接的是真实实现」这一步了**（1.15.0 起）。它是 `GET /setup` 的 `providers`——三项后端能力
（蓝图编译 / 需求解析 / 环境执行）现在是 `真实接入` 还是 `演示替身`——由服务器自己的 `.deploy/env`
里 `FACTORY_PROVIDER_MODE` 决定（§8.1），浏览器里没有任何控件改得动它。把它列成一步，等于给操作者布置一个
他完不成的任务，而一条可以忽略的清单项教会人忽略整张清单。现在这句话写在清单**末尾的一张事实卡**上：
替身在跑就点名是哪一项，并附服务端原样的 `能力: 状态` 一行。

冷启动时最容易误判成「坏了」的三件事：

1. **空表格不是崩溃**。环境页与治理页在无数据时给的是「还没登记」的空状态加一个跳转；连这句话都没有才算 bug。
2. **按钮灰着是有原因的**。桌面把鼠标移到按钮上、手机看按钮下面那行小字，显示的是 S1 原因码翻过来的话。界面不会替 S1 猜「应该可以点」。
3. **「新建项目」点不动**基本只有一种解释：`project.create` 没授予这个账号（`auth=off` 时不会出现，那种模式下人人可用）。清单第 4 行会直接把 S1 的这句话抄给你。

想在 shell 里核对界面看到的事实：

```bash
curl -ks https://127.0.0.1:1583/api/v1/setup -H "Cookie: factory_session=<会话>" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ("providers","configuration","environments","intelligence","projects","credentials")}, ensure_ascii=False, indent=1))'
curl -ks https://127.0.0.1:1583/api/v1/secrets -H "Cookie: factory_session=<会话>"   # 只有名字、引用与时间，没有值
```

`GET /setup` 只回事实：哪一步「该做什么」是界面按上面的顺序呈现的，可用性仍然只来自 `allowed-actions`。
无 `governance.manage` 读取权的账号会看到 `intelligence: null`，界面上写「你无权查看」而不是「0 个模型」；
无 `secret.manage` 时 `credentials` 同样是 `null`。`credentials.unresolvable` 列出的是**已登记但解不开**的引用
（最常见是换了主密钥），它不为空时先别改环境——那些环境一跑就会失败。

### 6.1 页面地图

界面用词已按 `S5M_移动优先前端_SRS.md` §2 的术语表统一为中文：**蓝图 / 登记表 / 审批节点 / 提示词 /
修改提案 / 凭证引用**。本手册正文里仍写 `Blueprint`、`Registry`、`Gate`、`Secret` 的地方，指的是后端与
契约里的同名概念（`registryRefs`、`DEPLOY_GATE`、`env://` 引用那一类），不是屏幕上现在的文字；两端各有一条
i18n 守卫（`S5/src/i18n/messages.test.ts`、`S5M/src/i18n/parity.test.ts`）拦着英文词回到中文界面。

左侧主导航（`S5/src/routes.tsx`、`SidebarNav.tsx`）：

| 路径 | 界面名 | 干什么 |
|---|---|---|
| `/` | 总览 | 项目卡、**待审批节点**、最近执行、通知；没配完时顶部还有一条「开始使用」提示 |
| `/approvals` | **待审批** | 跨项目的审批收件箱：所有停在等人决定的节点，一行一个，写清所属项目、这是哪一段的审批、目标环境、已批／需要几次、还没处理的警告与高优先级问题数；「去决定」进的就是审批页本身。`只看等我批的` 与 `按项目筛选` 都是**服务端筛选**（`GET /gates` 的 `mine`／`projectId`，S5IF-FLD03），分页照本节末尾那条规矩报「已列出 x / 共 N」。这一页不做任何决定——批准仍然只在审批页那一屏发生 |
| `/getting-started` | **开始使用** | 冷启动清单：提供方、环境、模型、首个项目，每项写清事实与跳转（§6.0） |
| `/projects` | 项目 | 列表 + 「显示已归档」 |
| `/projects/new` | 新建项目 | 6 步向导：基本信息 → 负责人 → 交付 → 语言 → 环境 → 汇总确认 |
| `/projects/:id` | 工作区 | 三栏：左阶段导航 · 中主区 · 右 Inspector |
| ├ `overview` | 总览 | 阶段清单与状态、**暂停/恢复/取消整个工作流**、本项目待审批的 Gate（点进去就是审批页） |
| ├ `stages/:stageId` | 各阶段 | 运行/重跑、进度与**取消本次 Agent 运行**、人工核对、**整份问题问卷**、人工编写；视图切换里「图」就地画出对象依赖图，「Registry / Diff」跳到元数据页 |
| ├ `inputs` | 输入 | 上传与解析状态 |
| ├ `inputs/:aid/column-mapping` | 列映射复核 | 校正模型猜的列对应关系后重解析 |
| ├ `artifacts` | 产物 | 预览 / 原始内容 / 差异 / 依赖与来源 + 「复现」。**每个产物有自己的地址** `artifacts/:artifactId`，详情头部把那行 URL 写出来并给「复制该产物链接」——审核意见可以引用地址而不是"从上往下第三个"。字节走**「下载」**：一条 `GET /artifacts/{id}/content?download=true` 的原生链接（CP-UI-016），能不能出现只问 `allowed-actions` 的 `artifact.download`；内容已清除（FR-DATA-008）时这条链接不出现，记录与哈希仍在。「清除内容」只在移动端有（桌面 SRS 没有这一条） |
| ├ `artifacts/:aid` | 单个产物 | 同上，只是被 URL 指着；血缘里的来源也走这个地址 |
| ├ `issues` | 校验问题 | 按等级/阶段/规则/对象过滤，可点「解释」 |
| ├ `metadata` | 元数据 | 按登记表 / 按对象类型 / 按文件 |
| ├ `decisions` | **决策** | 这一版蓝图记下的取舍：`决策号 / 种类 / 理由 / 选定方案 / 还考虑过的方案 / 风险 / 模型置信度 / 影响程度 / 谁确认的 / 影响到的产物`。登记表那一列的 `决策` 就链到这里，带 `?ids=DEC-1` 时只看这一条并给一句「只显示 …」与「查看全部决策」（S5IF-BP13）。审批页的第三问「为什么（决策与理由）」用的是同一张卡片，不再只念警告 |
| ├ `graph` | 对象图 | 领域图 / 映射图（>500 节点按类型聚合） |
| ├ `api` · `api/doc` | 接口审核 · OpenAPI 文档 | 按页面的接口卡片；文档视图是完整浏览器（只读，不许试运行） |
| ├ `handoff` | **开发包** | 模式 B 交给前端团队的那份包：逐文件列 `kind / 大小 / 哈希`，就地读一个文件，下载走浏览器自己的 `?download=true`。"这份包存不存在"问的是 S1 的 allowed-actions（`frontend_package.download`），不是从 404 反推——门关着就不发那个请求；因别的原因关着（项目已归档）时念 S1 给的原因，而不是承诺一个不会再跑的阶段（CP-UI-021/022） |
| ├ `gate/:gateId` | 审批 | 三个入口：桌面左侧 **待审批**（`/approvals`）、总览的项目卡区、项目「总览」页的待审批节点卡。分节条在审批页里把 `总览` 标成当前项（节点是从总览点进来的），否则人一进来整条分节条就都不亮了 |
| ├ `deployments` | 部署 | 按目标环境的运行列表、「创建运行」 |
| ├ `deployments/:runId` | 部署控制台 | 摘要、环境绑定、COMMIT、**取消运行**、结果 |
| ├ `change-requests` | 变更请求 | 影响分析 / 批准 / 驳回 / 开始 / 完成 / 取消 |
| ├ `audit` | 审计 | 过滤 + 哈希链校验 + 提交追溯 |
| ├ `members` | 成员 | 整集合 PUT |
| ├ `grants` | 读取授权 | RESTRICTED / PHI_PII 的读取许可 |
| ├ `exemptions` | 生产豁免 | 带理由和到期日的豁免 |
| └ `settings` | 设置 | 导出 / 归档 / 删除 |
| `/environments` | 环境 | DHIS2 环境登记与编辑，含连接测试、**能力探测**、**停用（可带理由）**与紧急锁定（§8.5） |
| `/governance/<分节>` | 治理 | 一条地址一节：`models`（模型与 Agent 名录）、`providers`、`policy`（含**申请放宽**）、`prompts`、`cost`（用量与预算）、`evaluations`；`/governance` 只跳到 `models`。页面标题跟着分节走（`治理 · 模型`）——从前这一页是八张卡堆着、只能滚动，引导页与搜索都只能把你丢在最上面 |
| `/users` | 用户 | 本地账号、角色授予、**按账号实际持有的角色逐条撤销**、重置口令、停用 |
| `/roles` | 角色 | 只读：6 个内置角色 → 动作清单 |
| `/notifications` | 通知 | 已读/全部已读、按项目过滤 |
| `/settings` | 系统设置 | §5.4 那些运行期参数 |

**产物按媒体类型决定「能读」还是「只能下载」。** 两个控制台共用同一条规则（`S5/src/lib/media.ts`、`S5M/src/lib/media.ts`）：`text/*`、JSON/XML/YAML/SQL 一类文本类型进查看器；`.docx` / `.pdf` / 图片等二进制给出类型、大小与「下载」，**不再把字节当文本读成一屏替换字符**，也不会先下载那些不打算渲染的内容。下载地址始终是 `GET /api/v1/artifacts/{id}/content?download=true`（带 `Content-Disposition: attachment` 与文件名），内联读取则不带这个头；是否显示下载入口只看 `allowed-actions` 的 `artifact.download`。内容已清除（`contentPurged`）时既没有内容也没有下载，记录与哈希还在。

**「还没生成」不是 404 故障。** 依赖图、登记表、校验报告、OpenAPI / UI 规格、开发包这类派生资源在对应阶段跑完前不存在，S1 回 `404` 且 `details.reason=NOT_GENERATED`（可带 `requiredStage`）；两端把它渲染成「完成 {阶段} 后可查看」的引导态，不弹全局错误提示。开发包列表还可能用 `200 + kind:NONE` 表示"还没交付"。只有项目里的首个设计阶段还没跑时的 `blueprint/draft` 也按 `NOT_GENERATED` 解析成空草稿；没有 `reason` 的 `NOT_FOUND` 仍然按真错误上报（见 §6.2、§7.2）。

**提供方行上的「测试连接」**问一次 `POST /governance/providers/{name}/test-connection`：它只打提供方地址的 `GET /models`（5 秒预算），不生成内容、不产生调用费用、不回显密钥；结果分「地址应答正常」与「地址没有给出可用应答 — <传输层原话>」，把"地址写错 / 密钥被拒 / 模型比超时慢"三种问题分开。按钮可用性与编辑提供方同一个动作（`governance.provider.enable`），不是另一个权限。

**工作区的分节条分成四组**：`项目`（总览 / 审计 / 设置）、`审阅`（输入 / 产物 / 校验问题 / 元数据 / 决策 / 对象图 / 接口）、`交付`（开发包 / 部署 / 变更请求）、`谁能看什么`（成员 / 读取授权 / 生产豁免）。从前是十五个页签平铺一行，分类这件事是界面留给操作者的作业。

「总览」页是这个工作区的控制面板：待审批的 Gate 列在这里，暂停 / 恢复 / 取消整条工作流也在这里——它们是整个工作流的动作，不属于某一个阶段，所以别在阶段页里找。

**长列表是分页读的，屏幕会说实话**（S5IF-G04）。审计与产物按游标（`limit` + `cursor`，下面挂「加载更多」），账号按 offset 翻页，通知的「只看未读 / 发给我的」是**服务端筛选**，顶栏的未读角标取 S1 的 `total`。每一处都写成「已列出 x / 共 N」。这不是装饰：把最新 50 条本地过滤，会把"这位操作者上周改了什么"回答成空列表，也会把 200 条未读报成 50。

**首屏包体有门禁**（**UI-NFR-06**：首屏 JS ≤ 200 KB gzip、单功能块 ≤ 300 KB；这一条本轮才写进 `requirements/S5_FactoryUI_SRS.md`，因为原来的 UI-NFR-01「首屏 < 3 秒」在构建机上永远不会失败，等于没人负责）。除外壳与登录那一进一出外，每个界面一块 chunk；`npm run check:budget` 读 `dist/index.html` 真正引用的东西算 gzip，并把结果落到 `S5/docs/reports/bundle-budget.md`。这一条抓到了一个真实的坑：`vite.config.ts` 里把 `@xyflow/react` 写进 `manualChunks`，等于把 57.7 KB 的图库提进了**每一屏**的首屏，而全应用只有依赖图那一屏用它。

**六张管理类表格现在区分"没有东西"和"没读到"**（`AsyncShell`）。成员、读取授权、生产豁免、角色、账号、部署以前只画表头，请求失败和列表为空长得一模一样。现在三态各自成句：加载、被 S1 拒绝（带 `correlationId`；只有 S1 说这条可重试才有重试按钮）、为空（一句解释 + 一个去处，比如部署页为空时指向项目设置——目标环境是在那里选的）。

### 6.2 端到端流程

**第 1 步 · 建项目。** 「新建项目」→ 填名称与负责人 → 选交付模式与项目类型 → 语言 → 勾目标环境。硬性要求（S1 会 422）：

- 至少 1 个目标环境，且**至少一个非 PRODUCTION**；
- **必须且只能一个「★设计基准环境」**，没标的话 DISCOVERY 阶段直接 `INVALID_STATE`；
- 目标环境必须已在 S4 注册过，否则 `INVALID_STATE`。

**第 2 步 · 上传需求材料。** 「输入」页。必须先选密级才能上传（S1 拒绝未声明密级的上传），选到 PHI_PII 还要勾确认框。点行的「解析」触发 `POST /inputs/{aid}/parse`，状态从「未解析」走到「已解析 / 待复核 / 失败 / 被策略拒绝」。扫描版 PDF 没有文本层会被拒，改传表格或文本导出，或者走「人工编写」。

**第 3 步 · 跑设计线阶段。** 顺序是

```
REQUIREMENTS → DOMAIN → DISCOVERY → MAPPING → PAGE_DESIGN → BASELINE_COMPILE →（模式 B）HANDOFF
```

| 阶段 | 界面名 | 谁在做 | 是否要人提交审批 |
|---|---|---|---|
| REQUIREMENTS | 需求归一 | S3 模型 | 要 |
| DOMAIN | 领域建模 | S3 模型 | 要 |
| DISCOVERY | 现状发现 | S4 只读查询 | 自动 |
| MAPPING | 映射 | S3 模型 | 要 |
| PAGE_DESIGN | 页面设计 | S3 模型（**模式 B 才有**） | 要 |
| BASELINE_COMPILE | 基线编译 | S2 编译 | 自动 |

点「运行 Agent」，202 受理，进度通过 SSE 实时推（顶栏出现「Agent 运行中 · 进度: …」）。运行中的进度卡片上有**「取消本次 Agent 运行」**：它只取消这一次 run，不取消整条工作流；run id 来自 SSE 进度帧的 `agentRunId`，按钮出不出现只问 `allowed-actions` 的 `agent.cancel`。模型产出会落成蓝图补丁，阶段转 `WAITING_APPROVAL` 并生成 Gate；如果这一轮还带回了问题，阶段里的整份问卷会就地更新（§6.8）。
阶段按钮的可用性**完全由 `GET /api/v1/allowed-actions` 决定**，本地不猜；不可用时提示「该操作当前不可用。」

「重跑 Agent」会先作废未决问题与在等审批的 Gate。
需要手工兜底时用「人工编写」（结构化表单或 Monaco JSON 编辑器，按 S2 的 section schema 现场校验），仅 REQUIREMENTS/DOMAIN/MAPPING/PAGE_DESIGN 可用。

**第 4 步 · 复核产物。** 「产物 / 校验问题 / 元数据 / 对象图」四个标签页。「复现」会重编译并比对哈希，是验证确定性输出的手段。校验问题可以点「解释」让模型说明这一条为什么报。

**第 5 步 · 过 Gate。** 从「总览」点进审批页。审批按哈希绑定：批准与「你此刻看到的 N 个对象」绑死，内容一变，审批自动失效（`INVALIDATED`），必须重看。
所有警告必须标「接受」或「需修正」才解锁批准；**只要有一条「需修正」，就只能驳回**。
未回答的 HIGH 级问题会阻止批准。
七种 Gate：`REQUIREMENT_GATE`（需求）、`DOMAIN_GATE`（领域）、`ARCHITECTURE_GATE`（映射）、`DESIGN_GATE`（页面设计）、`PACKAGE_GATE`（静态校验后）、`DEPLOY_GATE`（提交前）、`HANDOFF_GATE`（交接）。

**第 6 步 · 部署到 DHIS2。** 「部署」→ 选环境「创建运行」→ 运行线六个阶段：

```
BIND_COMPILE(R1) → STATIC_VALIDATION(R2,出 PACKAGE_GATE) → DHIS2_VALIDATE(R3,出 DEPLOY_GATE)
→ COMMIT(R4) → POST_VERIFY(R5) → FIXTURE_IMPORT(R6)
```

「部署控制台」里过 DEPLOY_GATE 后点「提交（COMMIT）」，会弹二次确认，列出环境、载荷哈希、变更统计。
COMMIT 前后端做的事：审批有效 → **三方载荷哈希必须相等**（validate、gate、当前包）→ 环境未被紧急锁定且在提交时间窗内 → 生产环境还要求同一工作流有个非生产运行已 `VERIFIED` → 漂移检查 → 才发 `POST /api/metadata importMode=COMMIT`。

网络断了会显示「结果待确认」并明确不自动重放 —— 用**同一个幂等键**重试来确认结果。COMMIT 一旦发出就不可取消。

**第 7 步 · 收尾。** 「审计」页查记录、点「校验哈希链」。项目里程碑阶梯：

```
DRAFT → DESIGN_APPROVED → PACKAGE_APPROVED → DHIS2_VALIDATED → DEPLOYED → VERIFIED → HANDED_OFF
```

工作流状态是**推导出来的**，不是存的字段，优先级 `CANCELLED > PAUSED > COMPLETED > FAILED > BLOCKED > WAITING_USER > ACTIVE`。`COMPLETED` 要求：至少一个运行 `VERIFIED` + 没有未关闭的运行 + 设计线走完（模式 B 还要 HANDOFF PASSED）。

**第 8 步（可选）· 退役一个对象。** 总体 SRS 15.6 的三种恢复策略里，"修正性提交"与"回退到上一版本载荷"就是上面的第 3～6 步重来一遍；
**定向删除**另有一条硬路径：项目里要有一条处置为 `DEPRECATED` 的 Registry 条目 → 把它的 businessKey 写进变更申请的
`deletionCandidates` 并**批准**（§6.6）→ 带着这份申请重跑 R1，S2 才会在包的 manifest 里列出候选（`BP-CMP-026` 逐条再核引用与关联数据）→
再用那份包调 `POST /environments/{key}/delete-objects`，S4 在提交前当场再核一次（`DX-QRY-004`）。
这一条**没有界面按钮**（理由见 §11 第 5 条），命令与四道闸在 §8.5 末。

### 6.3 交付模式 A / B

| | A | B |
|---|---|---|
| 界面文案（S5） | 「A —— 工厂交付全部」 | 「B —— 工厂交付给开发人员」 |
| 界面文案（S5M） | 「A · 直接交付」 | 「B · 含页面设计」 |
| `PAGE_DESIGN` | **SKIPPED**（原因 `DELIVERY_MODE_A`） | 正常执行 |
| `HANDOFF` | **SKIPPED** | 正常执行：出前端产物，里程碑走到 `HANDED_OFF`（本机的示例运行就是这一条，见 §8.1 的实况） |
| 变更模式 | `POST /projects/{id}/upgrade-mode`（幂等），S5M 的项目设置里有可用按钮，S5 没有 | |

模式 B 的交付物清单页（S5M 的「交付物」、S5 的「前端产物」）要 HANDOFF 完成才有内容 —— 现在拿得到：
本机的示例运行走完 HANDOFF 后，`GET /projects/{id}/registry/frontend-package/files` 列出的就是那次编译出来的包，
读某个文件的正文用 `…/frontend-package/files/{path}`。**没有"预览"这一档**：S1 没有页面预览端点，
这一屏给的是交付物的文件清单与内容，不是渲染出来的界面（§11 第 10 条）。

### 6.4 上传密级（5 档，两个界面一致）

`PUBLIC 公开` · `INTERNAL 内部` · `CONFIDENTIAL 机密` · `RESTRICTED 受限` · `PHI_PII 个人/健康数据`

密级不是标注而已，它决定数据能不能进模型：策略是「组织级 ∩ 每个目标环境」的交集。默认矩阵允许 `LOCAL_ONLY` 与 `PRIVATE_NETWORK` 处理常规级别，`RESTRICTED` 只允许 `LOCAL_ONLY`，**`PHI_PII` 不允许外发且无法放宽**（申请放宽对 PHI_PII 无效，`policy/model.py:106`）。
读 RESTRICTED / PHI_PII 产物还要求按项目授予 `artifact.read.restricted` / `artifact.read.phi`，即「读取授权」页。

### 6.5 阶段状态机

`LOCKED → READY → RUNNING →（WAITING_INPUT | WAITING_APPROVAL）→ PASSED`，异常分支 `FAILED`、`BLOCKED`、`INVALIDATED`、`SKIPPED`、`MANUAL_CHECK`。
非法跳转返回 409 `INVALID_STATE`，`details.allowed` 会列出此刻允许的目标状态 —— 看那个字段就知道下一步该点什么。

`MANUAL_CHECK` 的含义是「系统结果不确定，需要人判定」，在阶段页填核实说明后点「标记为通过 / 标记为失败」。

### 6.6 角色与权限

6 个内置角色（代码定义，没有自定义角色）：

| 角色 | 定位 |
|---|---|
| `SOLUTION_ARCHITECT` | 设计线主力 |
| `DHIS2_IMPLEMENTER` | 元数据落地，**有** `execution.commit.PRODUCTION`，**没有** `gate.approve.*` |
| `FRONTEND_DEVELOPER` | 页面/前端产物 |
| `BUSINESS_ANALYST` | 需求与业务校验 |
| `DEPLOYMENT_APPROVER` | **有** `gate.approve.DEPLOY_GATE.PRODUCTION`，**没有** commit |
| `PLATFORM_ADMINISTRATOR` | 平台管理（引导账号默认拿这个） |

`DHIS2_IMPLEMENTER` 与 `DEPLOYMENT_APPROVER` 是刻意分开的：执行者不是审批人（`CP-GATE-012`）。生产审批还会检查：**同一个人不能审两次**、**该运行的执行人不能批自己的 DEPLOY_GATE**；若 S4 环境档案给出了 `protection.operator_role`，审批人还须持有该角色，但该字段当前无法设置（见 §11 第 9 条），所以这条检查目前不触发。`required_approvals` 每次从 S4 现读，当前恒为默认值，Gate 因此按 1 人创建。

项目级动作还要求「是该项目成员」。角色可以全局授予，也可以限定到某项目（限定即隐含成员身份）。

**几个具体动作名，排障时按它查**：整份问卷的每一题仍走 `question.answer`（端点是 `POST /questions/answers`，服务端逐题检查是否开放、是否属于项目）；取消单次 Agent 运行是 `agent.cancel`；环境「测试连接 / 能力探测」是 `environment.test_connection` / `environment.probe_capabilities`；提供方「测试连接」复用的是编辑提供方那个 `governance.provider.enable`；模型登记与改行分别是 `governance.model.register` / `governance.model.enable`。角色表本身仍是 6 个内置角色，没有自定义角色；`FRONTEND_DEVELOPER` 只有 `project.view` + `artifact.read`，不持有 `question.answer`，所以问卷对它只读、提交按钮不可用。

**跨项目的两个列表按成员关系过滤**（契约 1.14.0，`FR-SEC-004`）。`GET /projects` 与 `GET /deployment-runs` 的 URL 里没有项目，
所以它们只能问组织级那一句 —— 过去问完就把**全部**项目交出去，隔离靠的是"没人持有组织级非管理员角色"这个巧合。
现在分两种答复：**组织级**持有 `project.view` 的人（平台管理员，或被组织级授予该角色的实施者）仍看到整个组织，那正是管理界面该有的视野；
只有**项目级**授权的人只看到自己所属的项目，`total` 与行数一致。对操作者的可见变化是：一个只有项目级授权的人
（本机就是 `implementer` / `approver`）过去打开项目列表会吃 **403**，连自己刚建的项目都列不出来，现在列得出自己的、也只看得到自己的。

**同一个角色名可以在两个范围各存一份**，这不是冗余而是有意：把 `DHIS2_IMPLEMENTER` 授在组织级，说的是"这个人替整个 Factory 登记环境、录凭据"；
授在项目里，说的是"这个人在该项目里执行 Run"。撤销只撤你选中的那一条（`§6.6` 末的角色分配那一段）。
因此 `environment.*` / `governance.*` / `secret.manage` 这些**动作在 URL 里没有项目可问**的，仍然按组织级答复 —— 它们管理的对象（环境、提供方、模型、凭据）
本来就是全厂共有的；给它们另造一套"组织级专用动作名"会让附录 B 的词表与两个控制台的 ACTION 表、`allowed-actions` 的回答集合各说一套，
把它们从项目角色表里删掉又会取消掉上面那种合法授权。裁决与理由写在 `S1/docs/CHANGELOG.md` 1.14.0。

**退役一个对象要走变更申请**（`CP-CR-005`，总体 SRS 15.6 的"定向删除"）。新建变更申请时可以填 `deletionCandidates`
（S5「变更申请」对话框里的「本次请求要作废的对象」，S5M 面板里同名的「要作废的对象」）——**只能填处置为 `DEPRECATED` 的条目**，
填别的会当场 422 并在 `details.notDeprecated` 里列出对不上的键名。为什么拦在这里：非 DEPRECATED 的名字到编译期才会被
`BP-CMP-026` 静默剔除，那时候界面已经显示"删除执行过了"，而对象还好好待在实例上。剩下的三步（批准 → start → 重跑 BIND_COMPILE
→ 调删除端点）没有界面入口，见 §11 第 5 条与 §8.5 末。

删除一条链上是两步权限：`project.export` 与 `project.delete` 一起给 `PLATFORM_ADMINISTRATOR` —— `CP-PRJ-007` 规定**归档之后必须先产出 PROJECT_EXPORT 才能删**，只给删除不给导出会让这一步对系统里每个身份都走不通（删除回 409 `requires: PROJECT_EXPORT`、导出回 403）。这两个动作也都是项目级的，所以**清掉一个自己不所在的项目仍然会是 403**：先把自己加进成员里。

**怎么加一个账号**：S5 用侧栏的「用户」页（`/users`），S5M 用「权限管理 → 用户」；两处都受 `user.manage` 控制（没有它按钮就是灰的，且灰的原因来自 S1）。填的是**登录名**（`subject`，认证时用的那串）与**显示名**（`displayName`，界面上给人看的那串，中文正常）——两者不是一回事，登录名进审计与审批记录，显示名只用于展示。新账号的口令至少 8 位（服务层强制，见 §2.5）。
**角色是单独一步**：建完账号后在同一页「分配角色」，一次授一个；**两端的"项目范围"都是从项目列表里选，不是填 id**——选「组织级（不限定项目）」即全组织生效，选某个项目即只在该项目的范围内生效（CP-IAM-002，并且隐含该项目成员身份）。撤销也只撤一条，撤的是列表里那一条（同一角色可以在组织级和一个项目里各有一份）。
**账号删不掉**：S1 只有 `PATCH /users/{id}`，能停用（`status: DISABLED`）不能删除——停用会立刻吊销该账号的所有会话。要"消失"的账号请停用，不要指望有删除按钮。两个控制台都有这个开关：S5 在账号行上（「停用」/「启用」），S5M 在卡片的操作区里同一对按钮；桌面端这一对按钮此前**从未发出过请求**（它把 id 绑在了 hook 创建的那一刻，拿到的是空串，见 S5 CHANGELOG 1.4.0）。
**两处建号都可以选"首次登录要求改口令"**：桌面端默认勾上（管理员发的是初始口令），移动端默认不勾（自己给自己建号时通常不需要）——两边都是一个可见的开关，不再由界面写死。`mustChangePassword` 仍然是报告位（CP-IAM-005 ③）：服务端不因此拦任何调用，界面提示并给出自助改密入口。
**登录名不能重复，也不能"重开"**：同一个 `subject` 再建一次会得到 422（`details.fields[0].path = subject`）。这条检查**跨来源、跨状态**——已经停用的账号仍然占着它的登录名，所以停用的人不会在你新建同名账号时把旧授权继承过去。要恢复那个人，用「启用」，不是重建。
**建好之后还能改什么**：显示名与邮箱在两个控制台都能改（S5 账号行的「编辑」，S5M 卡片上的「编辑账号」）；**登录名不能改**——它是审计与审批记录里的那串，改它等于改写历史，S1 也没有开这个口。
一个能干活的管理员账号最小路径就是：新建（登录名 + 显示名 + 口令）→ 授 `PLATFORM_ADMINISTRATOR` → 用新口令登录一次。

**本机现在有哪些账号**（口令不写进任何文件，包括这一份）：

- `admin`：部署时建号的引导管理员（`FACTORY_BOOTSTRAP_ADMIN_SUBJECT`），组织级 `PLATFORM_ADMINISTRATOR`。
  **口令以库里那份为准**：`.deploy/env` 的 `FACTORY_BOOTSTRAP_ADMIN_PASSWORD` 只在**建号那一刻**被读一次，
  重启不会把它写回账号 —— 所以在界面上换过口令之后，env 里那串就只是历史值，拿它登录会得 401（`§10` 的
  「口令看起来对了却登不进去」）。要恢复有三条路：用现在那份口令登录；由**另一个**管理员在「用户」页重置；
  或者——只剩这一个管理员、口令又忘了——在服务器上跑
  `S1/.venv/bin/python S1/scripts/reset_admin_password.py`（口令只从 stdin 进，不落 argv / shell history；
  有终端时隐藏输入并要求连打两遍，脚本按 `--dry-run` → 目标确认 → 写入口令的顺序走，所以先看一眼会动谁
  再决定）。它调的就是页面那条 `identity.service.set_password`：换掉整条 digest、清 `failed_attempts` 与
  `locked_until`、**吊销该账号已发的会话**、写审计事件 `cp.user.password_set`。加 `--sync-env-file`
  会把新值一并写回 `.deploy/env`（文件不是 0600 就拒绝写），否则 env 里那串又变成历史值，而
  `./start.sh` 的登录自检与 `S5M/scripts/verify-deployed-pages.mjs` 读的正是它。
- `implementer`：`DHIS2_IMPLEMENTER`，**项目级**（授在本机的两个示例项目上）。负责阶段执行、PACKAGE_GATE、COMMIT。
- `approver`：`DEPLOYMENT_APPROVER`，**项目级**。只批 `DEPLOY_GATE` —— 让执行的人批自己执行的 Gate 会被
  `AC-GATE-04` 直接拒掉，所以演示完整流程时必须换这个身份。

口令由操作者自行设定，记在服务器之外；`implementer` / `approver` 的口令同样不进仓库、不进日志。
`probe-lock-<日期>@example.org` 这类活体探测留下的账号（5 次失败 → 锁定 → 重置 → 停用）本机现在没有；
如果哪天出现，它一定是 `DISABLED` 状态 —— 停用是唯一可逆的收敛方式，S1 没有删除端点。

**连续 5 次口令错误会把账号锁一段时间**（`CP-IAM-005` ④），而登录端点故意把"锁了"和"口令错"说成同一句 401，
只有「用户」页的列表会显示 `locked`。用脚本试口令之前先想清楚这一点：本机的 `admin` 就曾被一轮拿着旧口令的
自动重试打到只剩一次余量（`failed_attempts = 4`）。计数在库里：`select subject, failed_attempts from cp.users`。

### 6.7 屏幕上的取值词汇表（记号 → 词）

后端把状态、角色、类别作为**记号**发出来（`WAITING`、`PHI_PII`、`SOLUTION_ARCHITECT`）。界面一律换成词，
规则只有一条：**会出现在屏幕上的记号，必须在两端两种语言里各有一个词**；查不到的**原样显示**，
宁可让人看到一个奇怪的大写词，也不要给一个空白或猜出来的词。完整表在
`requirements/S5M_移动优先前端_SRS.md` §2.1（两端共用），词本身存在 `src/i18n`（桌面 `messages.ts`、
手机 `locales/{en,zh-CN}.ts`），读它的只有 `src/lib/labels.ts` 和状态标签组件；
`src/lib/vocabulary.test.ts` 逐个记号查两种语言，缺词、或者词就是记号本身，都判红。

常用几组（中文列就是屏幕上那几行字）：

| 来自 | 记号 → 中文 |
|---|---|
| 审批节点 `GateStatus` | `WAITING` 等待审批 · `APPROVED` 已批准 · `REJECTED` 已驳回 · `INVALIDATED` 已失效 |
| 部署任务 `RunStatus` | `CREATED` 已创建 · `IN_PROGRESS` 进行中 · `WAITING_APPROVAL` 等待审批 · `COMMITTED` 已提交 · `VERIFIED` 已核验 · `FAILED` 失败 · `CANCELLED` 已取消 · `MANUAL_CHECK` 待人工核实 |
| 项目工作流 `WorkflowStatus` | `ACTIVE` 进行中 · `WAITING_USER` 等人处理 · `BLOCKED` 已阻断 · `COMPLETED` 已完成 · `PAUSED` 已暂停 · `CANCELLED` 已取消 |
| 变更申请 `ChangeRequestStatus` | `DRAFT` 草拟 · `ANALYZED` 已做影响分析 · `APPROVED` / `REJECTED` / `COMPLETED` / `CANCELLED` 同上 |
| 项目进度 `MilestoneStatus` | `DRAFT` 还没出草案 · `DESIGN_APPROVED` 设计已批准 · `PACKAGE_APPROVED` 元数据包已批准 · `DHIS2_VALIDATED` 预检校验已过 · `DEPLOYED` 已正式提交 · `VERIFIED` 已核验 · `HANDED_OFF` 已交开发包 |
| 待确认问题 `QuestionStatus` | `OPEN` 待回答 · `ANSWERED` 已回答 · `WITHDRAWN` 已撤回 · `SUPERSEDED` 已被后续取代 |
| 一次执行 `ExecutionStatus` | `COMPLETED` 已完成 · `FAILED` 失败 · `NEEDS_MANUAL_CHECK` 待人工核实 |
| 内置角色 | `SOLUTION_ARCHITECT` 方案架构师 · `DHIS2_IMPLEMENTER` DHIS2 实施人员 · `FRONTEND_DEVELOPER` 前端开发 · `BUSINESS_ANALYST` 业务分析员 · `DEPLOYMENT_APPROVER` 部署审批人 · `PLATFORM_ADMINISTRATOR` 平台管理员 |
| 登记表处置 `Disposition` | `CREATE` 新建 · `UPDATE` 更新 · `REUSE` 复用 · `REFERENCE` 引用（不托管）· `EXTERNAL` 外部对象 · `DEPRECATED` 停用 |
| 审批节点绑定的对象 `RefType` | `ARTIFACT` 产物 · `BLUEPRINT_DRAFT` 蓝图草稿 · `BLUEPRINT_VERSION` 蓝图版本 · `ENVIRONMENT` 目标环境 · `EXECUTION` 一次执行 |
| 预算状态 | `UNSET` 未设上限 · `OK` 未超 · `WARNING` 接近上限 · `EXCEEDED` 已超限额 |
| 问题严重度 | `BLOCKING` 阻断问题 · `ERROR` 错误 · `WARNING` 警告 · `INFO` 提示；待确认问题 `HIGH` 高 / `MEDIUM` 中 / `LOW` 低 |
| 通知渠道 | `IN_APP` 站内消息 · `EMAIL` 邮件 · `WEBHOOK` Webhook · `WEBHOOK_SECRET` Webhook 的密钥引用 |
| 能力档案新鲜度 | `VALID` 仍然有效 · `EXPIRED` 已过期 · `VERSION_CHANGED` 目标环境换过版本 |
| 密级 | 见 §6.4（公开 / 内部 / 机密 / 受限 / 个人健康数据） |

产物类型（`METADATA_PACKAGE` 元数据包、`FRONTEND_PACKAGE` 开发包…共 25 个）、两版蓝图差异里的对象状态
（新增 / 已更新 / 无变化 / 移除 / 仅引用）、需求条目的优先级与状态（必须 / 应该 / 可以 / 不做，已确认 / 待复核）、
环境的删除策略、豁免名、账号来源、用量归组轴，同样各自有词，逐项列在 SRS §2.1。

**四样故意不换**：④ 通知里服务端自己写的那句 `message`（`REQUIREMENT_GATE is waiting for approval`
是 S1 落库的原文）——界面上的那一行标题换成词（「有一个节点等待你的审批」），原文留在下面一行，
排查时要的就是那句原话。剩下的三样：① 审计事件类型（`cp.stage.started` 那一串）——审计页是给人核对链路的，
翻成中文等于制造一个服务端查不到的名字；② DHIS2 对象类型（`organisationUnit`）与 HTTP 方法——
那是 DHIS2 与 OpenAPI 的词，不是本产品的；③ 业务键、UID、哈希、模型名、端点地址，以及连接检查名
（`addressReachable`）、IAM 动作串（`gate.approve.DEPLOY_GATE`，它同时也是一条权限名）、
登记表文件 `role`——数据与标识符，逐字对照才有用。

**两处一个记号两句话**：`ACTIVE` 既说环境（启用中）也说项目工作流（进行中），所以项目卡单独取"进行中"那个词；
预算的 `WARNING` 与问题的 `WARNING` 不同义，各查各的表。

这一节是本轮补出来的，两处具体的错也是本轮修掉的：`notifications.types` 的键当年写成小写而服务端发的是
大写记号，于是那十七个通知类型在屏幕上一直是英文原文（两种语言的成对检查看不见这种洞——两边同时缺一样算配平）；
手机系统设置里的通知渠道那一行写成"是 email 就叫邮件，否则叫 Webhook 引用"，而服务端发的是大写记号
（`EMAIL`、`WEBHOOK`、`IN_APP`），于是每一条渠道都被念成了 Webhook 引用；桌面那一栏的候选值也写成小写，
与 S1 自己发出的那份对不上。现在两端的词表按服务端的拼法建，查的时候先做大写归一（历史上存进去过小写值的
部署不至于读出空白）。

### 6.8 整份问卷：一次答完、一次提交（桌面）

从契约 1.19.0 起，设计阶段的问题不再"一题一答"。桌面在**项目阶段页**（`/projects/:id/stages/:stageId`）内嵌整份问卷：
只要该阶段还有 `OPEN` 问题就出现，标题是「问题 · N」；它列出**这个阶段的全部开放问题**，不再需要点进单题。

- **每题都带三个标记**：严重度（高/中/低）、`阻断` 或 `选填`、`单选` / `可多选`。单选来自 `answerMode=SINGLE`，
  多选来自 `MULTIPLE`；历史问题没有这个字段时按 `SINGLE` 处理。
- **建议答案**由模型给出，推荐项旁边有绿色「推荐」标签；每条还可以带理由（`rationale`）、会重新生成什么
  （`regenerates`）、是否改 UID（`changesUid`）。这些只是帮你判断，不会自动替你选。
- **「其他（自行填写）」**是每题最后一个选项：单选时它替换建议答案，多选时它可以和建议一起勾；选中后出现
  「其他回答」文本框，文本必须非空才算作答。
- **阻断规则**：还有阻断题没答时，提交按钮保持禁用，页面上同时写出「还有 N 个阻断问题未回答」。至少要有一题
  作答；一题都没答也不会提交。未回答的 HIGH 问题还会在后续审批时阻止批准（§6.2 第 5 步）。
- **一次提交**：按「提交全部回答并重新生成」，整个页面的答案装进一个 `POST /api/v1/questions/answers` 请求。
  服务端在一个事务里记录全部答案，**最多 resume 一次**；不会出现"第一题答完模型就跑了、第二题作废"。
- **下一轮就地出现**：响应里的 `questions` 是 resume 之后仍然开放的问题。有新一轮就替换当前卡片（同一页继续答）；
  没有就提示「已全部回答，系统将继续运行」。如果只答了选填题、阻断题还没答完，服务端只落答案、不 resume
  （`resumed=false`）——这正是界面禁用提交所防的状态。
- **幂等**：一次提交用一个 `Idempotency-Key`；同一个键重发只会回放第一次的结果，不会写第二份答案。换一轮问卷
  就换一个新键。

在 shell 里走同一条路（登录取 `JAR` / `CSRF` 的十行见 §8.5 开头）：

```bash
BASE=https://127.0.0.1:1583/api/v1

# 先看这个工作流有哪些开放问题；questionId、suggestion id、answerMode 都在里面
curl -ks -b "$JAR" "$BASE/workflows/<workflowId>/questions" | python3 -m json.tool

# 整份提交：单选给一个 suggestionIds；多选给多个；「其他」给空数组 + customText
curl -ks -b "$JAR" -X POST "$BASE/questions/answers" \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -H 'Idempotency-Key: questionnaire-20261025-1' \
  -d '{
    "answers": [
      {"questionId": "Q-1", "suggestionIds": ["S-1"]},
      {"questionId": "Q-2", "suggestionIds": ["S-2", "S-3"]},
      {"questionId": "Q-3", "suggestionIds": [], "customText": "按 15 岁以上口径统计"}
    ]
  }'
```

响应形状（`questions` 为空表示这一轮已答完并继续运行）：

```json
{"answers":[{"questionId":"Q-1","status":"ANSWERED"}],
 "resumed":true,
 "runId":"RUN-…",
 "status":"RUNNING",
 "questions":[{"id":"Q2-1","answerMode":"SINGLE","blocking":false,"suggestions":[]}]}
```

服务端的硬规则：`answers` 至少 1 条、最多 200 条；每题必须选建议或给 `customText`；一次提交只能落在**同一个阶段、
同一个工作流、同一个项目**；同一题不能出现两次。违反任一条是 422/409，不是"部分成功"。

---

## 7. 使用：S5M 移动控制台

### 7.1 结构

手机上是 5 个底部标签，≥1024px 变 10 项左侧栏：

| 标签 | 名称 | 内容 |
|---|---|---|
| home | 工作台 | 快捷操作、我关注的项目、正在运行的任务、最近部署 |
| todo | 待办中心 | **跨项目**汇总：待审批 / 阻断中的阶段 / 待人工核实，按紧急度排，左滑「稍后处理」24 小时。筛选是**地址的一部分**：`/todo?kind=gate` 打开就只看待审批（侧栏那条「待审批」用的就是它），换筛选会写回地址，返回键回到你原来那一句；`kind` 是不认识的值就当没筛 |
| messages | 消息 | 分组通知、全部标为已读 |
| projects | 项目 | 列表 + 搜索；「仅显示进行中 / 显示已归档」是一组两态选择，新建项目是 **3 步**向导 |
| me | 我的 | 操作人姓名、界面语言、字号、深色模式、管理入口、清除本地缓存 |

侧栏（非底部标签）才有的页面：`/start` 开始使用（与桌面 `/getting-started` 同一张清单、同一批事实）、**`/todo?kind=gate` 待审批**（就是待办中心的审批筛选，只是有一个能被收藏、能发给同事的地址）、`/environments` 环境管理、`/governance/<分节>` 治理（手机上顶部一条分节条，标题写 `治理 · 模型`）、`/access` 权限管理（账号与会话 / 用户 / 角色 三个 tab）、`/settings` 系统设置。项目内还有 `/projects/:id/requirements` 需求条目页（§7.2 第 21 条）与 `/questions/:id` 整份问卷页（§7.5）。`/start` 不占底部标签：手机上从「我的 → 开始使用」或工作台顶部那张卡进。

**登录是它自己的页面** `/login`（不是弹窗）：没有会话时外壳把你带到这儿，页上写明为什么到这里（"你的会话已经结束（超时，或口令被修改）"），登录后回到你原本要去那一屏（`?next=` 只认站内路径）。任一读取或写入回答 401，同一个观察把你带回 `/login`，并且只报一次。部署开着组织账号（`authMode` 为 `oidc`/`both`）时，登录页在口令框下面多一个「使用组织账号登录」，浏览器出门去 IdP，回来落在 `/auth/callback` 换会话——与桌面端同一套（§2.4）。

### 7.2 手机端独有能力

1. **待办中心**（跨项目聚合 + 稍后处理），桌面只有总览卡片。
2. **API 审核** `/projects/:id/api` 与 **OpenAPI 文档** `/projects/:id/api/doc` —— 自研的只读 OpenAPI 浏览器，可按 operationId/路径/tag/schema 搜（桌面端有同一对页面）。
3. **交付物** `/projects/:id/package`：构建包的哈希与文件清单；**开发包** `/projects/:id/handoff`：模式 B 交给前端团队的那份包（CP-UI-021），逐文件 `kind / 大小 / 哈希`，就地读一个文件，下载是浏览器自己的 `?download=true` 链接。两者不是一回事，`kind` 与 `role` 也不是同一个词。这一屏先问 allowed-actions 里的 `frontend_package.download` 再决定要不要去读清单（UM-BR-01）：S1 说没有，就不发那个请求，直接告诉你缺哪个阶段。
4. **滑动提交**：「向右滑动以提交到 {env}」，生产环境红字并带 `(PROD)` 后缀。
5. **重新检查漂移**、**查看对账结果**（reconcile），运行控制台上比桌面多这动作；提交卡片里还能**取消这次运行**。
6. **项目设置真正可用**：目标环境 ↑↓ 排序、更换设计基准、提醒时长 + 邮件 + Webhook 引用、UID 池余量、升级交付方式、输入项目名确认删除，**以及导入**（UI-PRJ-004 把导出与导入配成一对，此前手机上只有前一半——导出去的东西只能回电脑前恢复）。填一个导出产物 ID 即发 `POST /projects/import`，可用性来自平台作用域的 `project.import`；受理后新项目真的出现在列表里。桌面的同名页面没有导入以外的更多东西。
7. **问题做成整页问卷**：同一阶段全部开放问题同屏，按 `answerMode` 单选/多选、「推荐」标记、「其他」自由填写；阻断题未答时提交按钮按住，整页一次提交，服务端只 resume 一次，下一轮问题原地出现。**提交后不可撤销**，旧的逐题 5 秒撤销条已撤下；未点提交就离开不发送任何回答。
8. **人工编写本地草稿自动保存**（5 秒），回来问「继续上次未提交的内容？」。
9. **SSE 断了自动转轮询**（重连 3 次失败后 5 秒一次 `invalidateQueries`；页面切后台暂停）。降级时顶栏会挂出「已切换为轮询刷新」——数据还在更新，但不再自己动了。
10. 紧急锁定：**加锁**两端都必须填理由（S1 拒绝无理由的锁定），**解锁都不要求**理由 —— 它是恢复访问的那一步，拦它等于把人锁在门外。锁定与否从 `allowed-actions` 的 `details.locked` 读，不是环境的 `status`（§8.5）。
11. 自助改口令，首次登录会提示。
12. **工作流动作**：阶段页底部动作条的「更多」里可以暂停 / 恢复整条工作流，「取消工作流」会先弹确认。
13. **环境**：与桌面一致的登记 / 编辑 / 连接测试 / **能力探测** / **停用**（可填理由）/ 紧急锁定。
14. **治理**：提供方登记与编辑、**逐行「测试连接」**、预算，以及**申请放宽数据策略**（选项只给「上限减去当前生效」的那部分，见 §8.5）。测试连接只问地址的 `GET /models`，结果贴在那一行下面（「地址应答正常」/「地址没有给出可用应答 — 原话」），不产生模型调用费用。
15. **权限管理**：账号列表里显示该账号**实际持有的角色**，每条可以直接撤销；账号可停用 / 启用（S1 没有删除端点）。
16. **项目级的成员、读取授权、生产豁免三页**都在 `/projects/{id}/…` 下（`UM-AC-21` 的这条特例已撤销，`docs/adr/004-m4-carve-out.md` 只作历史；测试现在断言的是"这三页必须存在"）。
17. **组织账号登录（OIDC）**与**会话过点自动回登录页**：这两条曾是桌面独有，现在两端同一套（§2.4、§7.1）。

18. **顶部搜索跳到四类东西，最近访问真的读回来了**（UM-NAV-004）：一个词给出**项目、阶段、环境、对象**四组结果，各自跳到 `/projects/{id}`、`/stages/{id}`、`/environments/{key}`、`/projects/{id}/graph?businessKey=…`（依赖图会把这个键选上，而不是把你丢在一张还要自己搜的画布前）。阶段与对象在 S1 没有跨项目的列表端点，所以这两组搜的是"你最近打开的那个项目"，面板在结果下面把这句说明写出来——搜不到不等于世界上没有。什么都没输入时，列出的是最近 10 个访问过的对象（项目行显示名字而不是 id）。

19. **产物详情把三件事分成三个控件**（UM-REV-001、UM-P-04、FR-DATA-008）：「下载」是 `GET /artifacts/{id}/content?download=true` 的真地址（能不能出现只问 `allowed-actions` 的 `artifact.download`），「复制链接」复制的是可引用的页面地址——此前这两件事挤在一个写着"下载 / 复制链接"、实际只复制链接的按钮上。**正文能不能就地读由媒体类型决定**：文本/JSON/XML/YAML/SQL 进查看器，`.docx` / `.pdf` 等二进制只显示类型、大小与「下载」，不把字节当文本读成乱码，也不先下载不打算渲染的内容。「清除内容」按第 6.2 节矩阵要求**输入产物 ID** 才按得下去，清除后记录与哈希还在、字节取不回来，下载链接也随之消失。

20. **取消 Agent 运行**（S5M 1.13.0）：桌面早有的入口，现在运行中的阶段进度卡片上也有——红色「取消 Agent 运行」按下先弹确认，运行的编号来自 SSE 进度帧的 `agentRunId`（后到的帧缺这个字段也不会把它抹掉），按钮出不出现仍然只问 `allowed-actions` 的 `agent.cancel`，界面不替 S1 猜。

21. **需求条目可以在手机上逐条写**（`/projects/:id/requirements`，S5M 1.14.0/1.14.1）：项目详情的「审阅」组里有入口，REQUIREMENTS 阶段页也有「需求条目」按钮。它不是只读的 NRR 摘要，而是**和解析产出的条目同一份数据**（蓝图草稿的 `requirements` 段）：
    - 顶部可在「草稿 / 已批准」两个投影间切换；默认打开实际存在的那个（阶段已 `PASSED` 就落在「已批准」，不会给你一屏空草稿）。
    - 可按 `全部 / 待确认 / 已确认 / 已驳回` 过滤；条目卡上写业务键、优先级、置信度、来源（解析 / 决策 / 手写）、未答问题数。
    - 「新增」只发一个 `add /requirements/items/-` 追加；「编辑 / 删除」先在提交前重读一次，再整段替换数组（`POST /stages/{stageId}/patches`，RFC 6902），这样解析管线在这期间加进来的条目不会被覆盖。保存成功后列表会立即刷新（1.14.1 修的就是这条失效键）。
    - 单条可以「确认 / 驳回」，也可以「确认全部待确认」；表单取值上限读 `GET /projects/{id}/blueprint/section-schemas/requirements`。
    - **能写的前提是 S1 仍在回答 `patch.submit`**：只有阶段处于 `READY` / `RUNNING` / `WAITING_APPROVAL` 时才可以；阶段已关闭就收起全部写入口，并给一个去「变更申请」的跳转。没有权限或被拒时，页面显示 S1 的原因码，不自己猜。
    - 桌面没有这张独立页；桌面的逐条编辑仍在阶段页的「人工编写」里（结构化表单或 Monaco JSON）。

### 7.3 只能在桌面端做的事

- 产物「复现」（`POST /artifacts/{id}/reproduce`）与哈希对照：这条判断要在宽表格里读，手机上不放。
- Registry 视图、**决策视图**（`/projects/:id/decisions`，§6.1）、Inspector 常驻面板、大图视图：这几条判断要在宽表格与画布里读，手机上不放。
- 需求条目的**结构化表单 / JSON 编辑**仍以桌面「人工编写」为主；手机有更顺手的逐条增删改页（§7.2 第 21 条），两边写的是同一份 `requirements` 段。

### 7.4 两端不一致的地方（用的时候别混）

| 事项 | 桌面 | 移动 |
|---|---|---|
| 交付模式叫法 | 「交付模式」 | 「交付方式」 |
| 拒绝审批叫法 | 「拒绝」 | 「驳回」 |
| 新建项目 | 6 步 | 3 步 |
| 密级默认 | 无默认，不选不让传 | 记住上次选择，否则 `INTERNAL`（绝不默认 PHI_PII） |
| PHI 确认 | 勾确认框后「上传」才亮 | 点「上传」弹确认页 |
| 上传入口 | 文件选择器 + 每行「解析」 | 拍照 / 相册 / 文件，传完自动解析 |
| 大文件 | 全量 | 只显示前 500 行 |
| 阶段主操作 | 「运行 Agent」/「重跑 Agent」 | 按优先级只出一个主按钮 |
| 问题问卷 | 阶段页内嵌整份问卷，一屏答完一次提交 | 阶段页的「回答问题」进入 `/questions/:id`，同一屏列出该阶段全部开放问题 |
| 需求条目编写 | 阶段页「人工编写」的结构化表单 / JSON | 独立的 `/projects/:id/requirements`，逐条新增 / 编辑 / 删除 / 确认 / 驳回 |
| 「产物未生成」提示 | 也给引导态：依赖图与开发包都写「由 {阶段} 生成」并跳过去 | 同一套引导态（含 Registry、校验问题、接口文档），并把没有 `details.reason` 的裸 404 也映射成引导 |
| 目标版本约束 | 空 | 默认 `2.41.x` |
| 默认语言 | `en` | `zh`（与桌面的 `zh-CN` 拼写不同） |

### 7.5 整份问卷：一页答完、下一轮就地出现（手机）

手机把问卷做成一个**独立页面** `/questions/:id`，但它显示的不是这一题，而是**该问题所属阶段的全部开放问题**——
从阶段页的问题卡片、待办中心、通知或输入页的问题入口点进来，看到的都是同一份整卷。页面按「高严重度优先」排序，
同一个阶段答完之前不用来回跳。

- **单选 / 多选由服务端说**：`answerMode=SINGLE` 渲染成单选组，`MULTIPLE` 渲染成复选组；没带这个字段的历史问题按单选。
  每题都有严重度、`阻断` / `选填` 标签。
- **推荐答案**带绿色「推荐」；理由与「会重新生成 N 项」写在选项下面。`changesUid` 的选项另行标出，提醒你会动 UID。
- **「其他（自行填写）」**在单选里是一个互斥选项，在多选里可以和其它建议一起勾；勾了才出现「其他回答」输入框，
  空文本不算作答。
- **阻断题不答完，提交按钮是禁用的**：页面顶部和底部各有一条「还有 N 个阻断问题未回答」。至少要有一题作答；
  一题都没答也不能提交。服务端的硬规则与桌面完全相同：同一阶段 / 同一工作流 / 同一项目、每题选建议或给自定义文本、
  同题不重复、一次最多 200 条。
- **一次提交**：底部吸底的「提交全部回答」把整页装进一个 `POST /api/v1/questions/answers`。服务端先在一个事务里
  记录全部答案，再**最多 resume 一次**；只答了选填题、阻断题仍未答完时只落答案、不 resume。
- **下一轮就地出现**：响应里的 `questions` 直接覆盖当前列表。非空就提示「已提交。模型根据回答生成了 N 个新问题」，
  页面滚回顶部继续答；为空就提示「已全部回答，系统将继续运行」并回到阶段页（没有阶段 id 时回待办中心）。
  页面停留期间以服务端刚回的这份列表为准，不会被稍慢的缓存读覆盖；SSE 事件仍会推进阶段状态。
- **幂等**：一次提交换一个 `Idempotency-Key`；同一个键重发回放第一次结果，不会重复写答案。

```bash
BASE=https://127.0.0.1:1583/api/v1     # JAR / CSRF 的取法见 §8.5 开头

# 与桌面同一个端点；手机只是把整卷放在一页里
curl -ks -b "$JAR" -X POST "$BASE/questions/answers" \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -H 'Idempotency-Key: questionnaire-mobile-20261025-1' \
  -d '{"answers":[{"questionId":"Q-1","suggestionIds":["S-1"]},
                   {"questionId":"Q-2","suggestionIds":[],"customText":"按 15 岁以上口径"}]}'
```

响应里的 `questions` 就是下一轮要显示的整卷；`resumed` 告诉你这次是否真的把 Agent 继续跑起来了。桌面端同一套
字段与规则见 §6.8。

---

## 8. 接入真实能力（离开 fakes）

### 8.1 三个模式

| `FACTORY_PROVIDER_MODE` | 行为 | 用在哪 |
|---|---|---|
| `fakes` | **无论真实包是否可导入，一律用 S1 自带的替身** `FakeBlueprint`/`FakeIntelligence`/`FakeExecution` | 演示、前端开发、走通流程。本机已切到 `real`，演示替身只用于临时排查 |
| `auto`（脚本默认） | 真实包能 `import` 就用真的，否则退回替身 | 渐进接入 |
| `real` | 必须真的装上 `factory_blueprint`/`factory_intelligence`/`factory_dhis2`，否则拒启 | 生产 |

`/healthz` 的 `providers.S2/S3/S4` 就是回答这一点的（`real`/`fake`），别信界面，信这个字段。

**`fakes` 下的「成功」是空的**：不产生真实设计内容，不会有任何东西到 DHIS2，但整条工作流会走通。`FakeIntelligence.parse_input` 返回 `used_model:false`；`FakeExecution` 造出来的环境是 `https://<key>.invalid`。

混合模式有个坑：`auto` 下 S2 真 + S4 替身时，S2 会装自己的 `StubDhis2`，发的是 `stub...` 开头的计数式 UID，而 `/healthz` 仍显示 `S2=real`。要接 DHIS2 就把 S4 也弄真。

### 8.2 切到 real

`install.sh --providers` 与 `start.sh --providers` 都接受 **`auto|fakes|real`**。给 `real` 时脚本会逐个 `import` 检查
`factory_blueprint` / `factory_intelligence` / `factory_dhis2`，缺谁就中止并点名哪个包没装；`auto` 缺谁只是警告，
运行时退回替身。切到 `real` 最省事的一条：

```bash
./start.sh --providers real     # 同步 .deploy/env、重渲染并重启两个单元
curl -sS http://127.0.0.1:8090/healthz | python3 -m json.tool   # 三个都该是 real
```

本仓库用 `-e` 装了 S2/S3/S4，所以 `auto` 现在等价于真实实现可用；`auto` 与 `real` 的差别只在「导入失败时是否静默退回替身」。

### 8.3 接真实模型：三件事缺一不可

1. **登记提供方**（`POST /api/v1/governance/providers`）：`name`、`providerClass`（`LOCAL_ONLY`/`PRIVATE_NETWORK`/`APPROVED_EXTERNAL`/`UNRESTRICTED`）、`endpoint`、`apiKeySecretRef`、`adapter`、`enabled`。
   `apiKeySecretRef` **可以整体不填**（契约 1.7.1）：本机起的 Ollama / LM Studio / llama.cpp 默认不鉴权，留空就是"运行时不发 `Authorization` 头"。别为了填满表单编一个不存在的 `env://名字`——登记会通过，第一次调用必失败，`GET /setup` 的 `credentials.unresolvable` 还会替这条假引用报一次警。填了则必须是引用串（含 `://`），写字面值 422。
   调用方式是 OpenAI 兼容的 `POST {endpoint}/chat/completions`，健康检查打 `GET /models`。**endpoint 为空时调用直接报错**「model X has no endpoint configured」。
   `adapter` **目前什么都不控制**：S3 只有一个线协议实现，`provider_for()` 不看这个字段（AI-LLM-007），填 `anthropic` 也照样按 OpenAI 兼容协议发请求。界面上它是一个只读的 `openai_compat` 常量，不是下拉框——接 Anthropic/Gemini 原生协议要等新增适配器。
   登记完（或改完地址/密钥后）点该行的「测试连接」：它调 `POST /governance/providers/{name}/test-connection`，只打 `GET {endpoint}/models`，5 秒预算、不产生模型调用；返回 `healthy` + 传输层原话，能把"地址写错 / 密钥被拒 / 超时"分开。
2. **登记模型**（`POST /api/v1/governance/models`）：`provider` + `model` 必填，另有 `capabilities`、`applicableAgents`（按 Agent 指定优先级）、`unitPriceInputPer1k`/`unitPriceOutputPer1k`、`enabled`。单价不进预算统计就无法做成本闸。
   **改已登记的模型要用 `PATCH /governance/models/{ref}`，不要重新 `POST`**。PATCH 是差量：只有点名的列会被写，未出现的字段保持原值；一个字段都不点名（或拼错字段名）回 422；`capabilities` 按键合并，所以只改 `vision` 不会把 `maxContextTokens` 打回默认。响应是整行 `ModelView`，审计里带 `before`/`after`。两端模型表单都只送"真的动过"的字段，并且**不发送** `endpoint` / `apiKeySecretRef`——这两个模型级覆盖目前只能在 API 上设；重新 `POST` 会用缺省值覆盖整行，少填哪个字段哪个字段就可能变空。
3. **激活 Prompt**（`POST /api/v1/governance/prompts/activations`）：激活会被校验卡住 —— 需要一次 `POST /governance/evaluations` 评测**得分 ≥ 0.95** 加一条 `POST /governance/regression-records` 回归记录。
   ~~**没激活不会报错**：会静默退化成一个空占位 Prompt，产出莫名其妙。~~ —— **已改为显式拒绝**：未激活的 Agent 启动运行会返回 `FAILED` 结果（不建 run 行、不花模型的钱），错误码 `AI_PROMPT_NOT_ACTIVATED`、问题里写明是哪个 agent 缺激活；`resume`/聊天/问题解释这三条旁路直接抛同一错误。补激活有现成脚本：`S1/scripts/activate_prompts.py`（幂等，评测→回归→激活三步都走公共治理端点，v2 模板发布后重跑一次即可）。
   **本机现状**：`review.explain` 的 v1 与 v2 都已注册，**v2 已在激活位**（2026-10-10 激活）；系统当前完全可用。以后换模板或新机器接真实 S3，跑一次
   `S1/.venv/bin/python S1/scripts/activate_prompts.py --base-url http://127.0.0.1:8090 --provider stub --model stub/stub-model --agents REVIEW --token <会话token>`，或在「治理 → 提示词」里激活。

还有几点容易忽略：

- 没有任何「允许且启用」的模型候选时，运行返回 `POLICY_DENIED`（策略判定为不可外发），不是异常。
- `MODEL_BASE_URL` 这个键**没有任何代码读它**。`MODEL_API_KEY` 之所以有效，纯粹是因为你把 `apiKeySecretRef` 写成 `env://MODEL_API_KEY`；而接口地址必须敲进提供方登记的 `endpoint` 字段里。
- 里程碑现状：S3 的 `CURRENT_MILESTONE=M2` 仍是排期刻度，但 `API` / `UI` / `FIXTURE` / `REVIEW` 四个 Agent 已在 `DELIVERED_AGENTS` 里逐项交付（`GET /governance/agents` 对它们回 `enabled=true`），所以 **PAGE_DESIGN 与模式 B 的 HANDOFF 会真正产出**，不再返回里程碑失败。仍被这道闸挡着的是 SRS 12 里更晚的源码提取、脱敏等能力；判断一个 Agent 能不能跑，看 `enabled`，不要只看 `milestone` 字段。桌面端阶段视图的四个切换段都有真实内容：「表格」是需求条目，「图」就地画 S2 产出的依赖图，「Registry」「Diff」跳到元数据页（那边已经有过滤与版本选择）。**没有「预览」这一档** —— S1 没有页面预览端点，留一个只会说「未实现」的标签等于把缺陷做成按钮。

### 8.4 接真实 DHIS2 环境

`POST /api/v1/environments`。字段与取值：

| 字段 | 必填 | 允许值 / 说明 |
|---|---|---|
| `environmentKey` | 是 | 项目里引用它 |
| `baseUrl` | 是 | 默认 `tlsRequired=true`，`http://` 会在保存时被拒 |
| `environmentClass` | | `DEV` / `TEST` / `UAT` / `PRODUCTION` |
| `authMode` | | `PAT`（→ `Authorization: Bearer <值>`）或 `BASIC`（→ 值必须是 `user:password`） |
| `versionConstraint` | | **执行侧要求**（`EnvironmentSpec` 里是必填项），例 `2.41.x` |
| `credentials[]` | | 每项 `{scope, secretRef, label?}`；scope ∈ `discovery import tracker datastore apps maintenance`。**每个环境 `discovery` + `import` 两个都必须有** |
| `secretRef` | | 必须是引用串且含 `://`，接受 `db://` `env://` `vault://` `secret://` `kms://`。**真能解析的只有前两个**：`db://`（界面录入、加密入库）与 `env://`（值留在 `.deploy/env`）。后三种是保留语法，没有对应后端，填了必失败（§5.1 的 `APP_ENV` 限制） |
| `llmDataPolicy` | | `LOCAL_ONLY` / `PRIVATE_NETWORK` / `APPROVED_EXTERNAL` / `UNRESTRICTED` |
| `timeoutSeconds` | | **超时就调这个字段**，别去设 `FACTORY_DX_*` |
| `protection` | | 自由对象（生产环境的提交时间窗放这里）。**只在创建时接受**，`PATCH` 的体里没有这个键 |

生产环境（`PRODUCTION`）的硬约束在**保存时**就判：

- `discovery` 与 `import` 的 `secretRef` **必须不同**（DX-AC-02）；
- 提交时间窗必填（ISO 星期 + HH:MM，按环境自己的时区）；
- `deletionPolicy` 默认 `FORBID`（非生产默认 `SAFE_ONLY`）；
- 生产不接受 fixture 导入。

**注册完立刻测连接**：`POST /api/v1/environments/{key}/test-connection`，body `{"requiredScopes":["discovery","import"]}`。没有留下连接测试记录时，R3 校验阶段会直接 BLOCKING 失败 —— 因为 import 权限这一事实只有 test-connection 能产生（DX-EXE-012）。

运行期还有两个必须知道的闸：

- **环境提交锁是全局的、跨项目、永不过期**。一次 COMMIT 落到 `NEEDS_MANUAL_CHECK` 会一直占着锁，直到你对该执行调 `POST /executions/{id}/reconcile` 再 `POST /stages/{id}/resolve-manual-check`。**不要重新发一次部署**，那只会多一次冲突。
- **紧急锁定**用 `POST /environments/{key}/emergency-lock`，body 是 `{locked, reason}`，锁定时理由必填、解锁时不必 —— 它是审计动作，只有加锁那一支需要留下为什么。

数据外发策略与环境的联动有一条**已查实并已修好**的接缝（本轮之前它是"读码推断"）：S4 交出的字段名是 `llmDataPolicy`，
而 S3 的策略归一化器只认 `dataExportPolicy` / `policy` / `exportPolicy` 那几个包装名与 `allowedClasses` 一类列表 —— 于是 S4 那份文档
整体落进兜底分支，**任何环境无论配得多宽，到 S3 都是 `LOCAL_ONLY (fail-safe)`**：登记一个 `PRIVATE_NETWORK` 的 Poe/Ollama 提供方，
设计阶段的每一次调用都会被策略拒掉，而界面上只看得见一条 fail-safe 警告。修法有两半，缺一不可：
① S3 认 `llmDataPolicy` / `maxProviderClass` 这一类**上限**写法（`_CEILING_KEYS`）；② 上限按"该类别**及一切更严格者**"展开
（`ProviderClass` 的语义就是"数据最远能到哪"，把上限读成恰好一个类别会让更宽的天花板**拒掉**本地的模型）。
`PHI_PII` 仍然无论如何都为空（`AI-POL-005`），未知取值仍然落兜底并给出 `unknown provider class`。
钉住它的是三方的那条测试 `S1/tests/service/test_real_s4_deletion.py::test_the_ceiling_s4_states_is_understood_by_s3`
—— S3 不得 import S4、S4 不解析策略，只有把三个包装在同一个 venv 里的 S1 能同时看到两端。
仍然建议接完真实 S3+S4 后读一次 `GET /projects/{id}/effective-policy` 对一眼：`environmentPolicies` 给出每个目标环境自己的类别，
`effective.warnings` 里那句 `narrowed by environment policy: X` 就是交集的出处（本机 `sbtest2` 配的确实是 `LOCAL_ONLY`，所以它显示 `LOCAL_ONLY` 是**正确**结果，不是这条缺陷的残留）。

### 8.5 登记环境与模型提供方

两件事都能在界面上做了，路径如下。

**桌面（`https://<IP>:1583/`）**

1. 「环境」→ 右上「添加环境」；改已有的就点该行的「编辑」（环境标识只读）。
2. 「治理」→ 底部「提供方」卡片 →「登记提供方」；改已有的点「编辑提供方」，停用/启用是一个按钮，行上另有「测试连接」。

**移动（`https://<IP>:1584/`）**：左侧栏「环境管理」→「新建环境」/ 卡片上的「编辑」；「治理」→「提供方」卡片 →「登记提供方」/「编辑提供方」，行上同样有「测试连接」。

表单在发出请求前就挡住 S1/S4 一定会拒的输入，所以点下去不会只拿到一个 422，更不会拿到一个说不清哪儿错的 500：

| 规则 | 为什么 |
|---|---|
| 密钥引用必须以 `db://` `env://` `vault://` `secret://` `kms://` 开头 | 业务请求从不接收明文值（`S5IF-G09`）；值只能通过 `PUT /api/v1/secrets/{name}` 单向录入一次。**真能解析的只有 `db://`（界面录入）与 `env://`（环境变量）**，后三种是保留语法，没有后端 |
| 地址必须以 `http://` 或 `https://` 开头；勾了「要求 TLS」则必须是 https | S4 在保存时拒任何其他写法（未勾 TLS 时给 `http://` 是允许的，但表单会警告） |
| 每个 scope 只能有一条引用 | S4 的凭据行按 scope 建唯一键；给同一 scope 再填一行，真实模式是 `IntegrityError`（一个 500），替身模式则悄悄覆盖前一行的引用 |
| 类别选 `PRODUCTION` 时必须同时有 `discovery` 与 `import` 两条引用，且两者不能相同 | `DX-AC-02` |
| 类别选 `PRODUCTION` 时必须至少给一个提交时间窗（选星期 + 起止时刻） | `DX-ENV-002`：S4 认为没有时间窗的生产环境不是一个生产环境 |
| 版本约束必填 | `DX-ENV-001`：没有它，"这个环境是不是 2.41"只能靠猜 |
| 编辑时显示名不能清空 | S4 会对合并后的档案重跑校验，空名字在那边报错 |
| 超时必须是正整数 | `timeoutSeconds` 决定该环境所有调用的默认超时 |

类别与认证方式的取值现在和 S1 一致：`DEV/TEST/UAT/PRODUCTION`、`PAT/BASIC`（旧表单给的 `SANDBOX/STAGING/OAUTH2/API_TOKEN` 一律会被 422，那些下拉项已从两端移除）。这些取值同时写进了契约的 `enum`，两端下拉是从生成类型派生的 —— 契约改了，`npm run typecheck` 就会在 `src/api/enums.ts` 报错，不会等到提交才发现。

**类别在登记时就定死了。** `PATCH /environments/{key}` 带 `environmentClass` 会被 422（`details.rule = DX-ENV-001`），两端的编辑表单也因此把它渲染成只读并写明原因。要换个类别只能另登记一个环境 —— 因为类别决定了这个环境已经满足哪些规则（删除策略默认值、时间窗、引用分离），改类别等于假装那些规则不必存在。

保存成功后**务必立刻点「测试连接」**：没有连接测试记录的话，R3 `DHIS2_VALIDATE` 会直接 BLOCKING（`DX-EXE-012`），表单成功不等于环境可用。桌面端连接结果会列出检查（S1 用 `state: PASSED/FAILED/SKIPPED` 三种状态，跳过不是失败）、按 scope 归组的缺失权限、以及 S1 给出的 `testedAt` 时刻 —— 这个接口是幂等的，同一个 `Idempotency-Key` 会**回放上一次的结果**而不是重新探测，所以两端每次点击都换一个新键。

**提供方的「测试连接」是另一件事**：它只问提供方地址的 `GET /models`（`POST /governance/providers/{name}/test-connection`，5 秒预算，不产生模型调用、不回显密钥），回答「地址应答正常」或「地址没有给出可用应答 — <原话>」。它不写连接测试记录，也不影响 R3；环境能否提交仍由环境自己的 test-connection 决定。两端每次点击同样换一个新的 `Idempotency-Key`。

`protection`（提交时间窗、时区、删除策略）现在两端都能编辑：创建时随表单一起提交，编辑时只在你真的动过它之后才进 body。这个"只在动过时才送"是必须的 —— S4 是整表替换时间窗列表，不是往里合并，一个没读旧值就写新值的表单会把已有窗口清空。所以移动端的表打开生产环境时会先读 `GET /environments/{key}/protection`，读到之前不给你编辑窗口。

**目前无法设置的一项**：`requiredApprovals` 与 `operator_role`（`DX-ENV-002`【调整】）。S4 0.1.0 的 `_validate_m4_fields` 仍以 “take effect with M4 permissions” 在保存时拒绝任何非空值（`DX_ENVIRONMENT_INVALID`），尽管 S1 的 M4 认证已交付；读取回默认值，两端也都不提供编辑入口。因此 DEPLOY_GATE 的所需审批人数实际回退为 1，多人审批由角色与职责分离规则保证，但“提高某环境所需审批人数”这一配置目前不可用。

**紧急锁定不是环境的「状态」。** S1 的 `EnvironmentStatus` 只有 `ACTIVE` / `DISABLED`；锁定与否是保护行的一列，界面从 `GET /allowed-actions` 里那条 `environment.emergency_lock` 的 `details.locked` 读（桌面端还会在行上多打一个「已锁定」标签，并把按钮从「紧急锁定」翻成「解除锁定」）。解除锁定不要求填理由，S1 也的确不要求 —— 只有**加锁**必须有理由，因为它要进审计链（`DX-ENV-007`）。


**停用与能力探测。** 环境行上除了「测试连接」「紧急锁定」，还有「能力探测」和「停用」。探测是问实例"你到底是什么版本、开了什么"，答案会存成一份能力档案（返回里带 `version` 与档案版本），下次测试连接会拿它比对；停用是把这台实例移出服务，可选填一句理由 —— 它是这条环境安静下来的唯一记录。

**停用是单向的。** S4 只有 `disable_environment`，`PATCH /environments/{key}` 也不带 `status` —— 一旦停用，除了直接改库或另登记一个新标识，没有把它请回来的接口。两个界面的确认文案都写明了这一点：如果只是想临时不上线，别用它。

停用的后果要连着看：针对该实例的**任何**操作都会被拒 → 409 `INVALID_STATE`（真实 S4 抛 `DX_ENVIRONMENT_INVALID`，S1 在门口对两种提供方给同一个回答），所以 `GET /allowed-actions` 会把「停用 / 测试连接 / 能力探测」三条翻成不可用并给 `reasonCode = ENVIRONMENT_DISABLED`；但**编辑档案与加解锁保持可用** —— 否则一台停用的环境就再也救不回来了。再点一次停用拿到的是 409 而不是 500：那是 S4 的拒绝，不是控制面崩了。

**等价的 curl 路径**（本机自签证书加 `-k`）：

```bash
BASE=https://127.0.0.1:1583/api/v1
JAR=$(mktemp)
PW=$(sed -n 's/^FACTORY_BOOTSTRAP_ADMIN_PASSWORD=//p' .deploy/env | tail -1)

CSRF=$(curl -ks -c "$JAR" -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"subject\":\"admin\",\"password\":\"$PW\"}" \
  | sed -n 's/.*"csrfToken":"\([^"]*\)".*/\1/p')

post() { curl -ks -b "$JAR" -c "$JAR" -X "$1" "$BASE$2" \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -H "Idempotency-Key: $3" -d "$4"; }

# 1) 登记一个开发 DHIS2 环境
post POST /environments env-dev-1 '{
    "environmentKey": "dhis2-dev",
    "name": "开发实例",
    "baseUrl": "https://dev.example.org",
    "environmentClass": "DEV",
    "versionConstraint": "2.41.x",
    "authMode": "PAT",
    "tlsRequired": true,
    "llmDataPolicy": "PRIVATE_NETWORK",
    "timeoutSeconds": 60,
    "credentials": [
      {"scope": "discovery", "secretRef": "env://DHIS2_DEV_DISCOVERY"},
      {"scope": "import",    "secretRef": "env://DHIS2_DEV_IMPORT"}
    ]
  }'

# 2) 测连接（不留记录的话 R3 会 BLOCKING）
post POST /environments/dhis2-dev/test-connection test-dev-1 \
  '{"requiredScopes":["discovery","import"]}'

# 3) 登记模型提供方
post POST /governance/providers prov-1 '{
    "name": "inhouse-llm", "providerClass": "PRIVATE_NETWORK",
    "endpoint": "https://llm.internal/v1",
    "apiKeySecretRef": "env://MODEL_API_KEY",
    "adapter": "openai_compat", "enabled": true
  }'

# 4) 只改地址：其余字段保持原值（S1 1.4.0 起）
post PATCH /governance/providers/inhouse-llm prov-2 '{"endpoint":"https://llm.internal/v2"}'

# 4b) 问一次提供方地址是否应答：只打 GET /models，不生成内容、不产生调用费用
curl -ks -b "$JAR" -X POST "$BASE/governance/providers/inhouse-llm/test-connection" \
  -H "X-CSRF-Token: $CSRF" -H 'Idempotency-Key: probe-prov-1'
#    → {"healthy":true,"detail":"…传输层原话…","ref":"inhouse-llm"}
#    地址错/密钥被拒/超时会给出不同的 detail，而不是同一句 unreachable

# 4c) 模型编辑是差量 PATCH：只写点名的 capabilities 键，其余列原样不动
post PATCH /governance/models/inhouse-llm/gpt-4o model-patch-1 '{"capabilities":{"vision":true}}'
#    空 body 或拼错字段名 → 422；不要用 POST 重新登记来"改一行"（整行覆盖会清掉没填的列）

# 5) 建生产环境时一并给提交时间窗（protection 只能在这条路上设）
post POST /environments env-prod-1 '{
    "environmentKey": "dhis2-prod",
    "baseUrl": "https://prod.example.org",
    "environmentClass": "PRODUCTION",
    "versionConstraint": "2.41.x",
    "authMode": "PAT",
    "tlsRequired": true,
    "credentials": [
      {"scope": "discovery", "secretRef": "env://DHIS2_PROD_DISCOVERY"},
      {"scope": "import",    "secretRef": "env://DHIS2_PROD_IMPORT"}
    ],
    "protection": {"commitWindows": [{"days": [1,2,3,4,5], "start": "20:00", "end": "23:30"}]}
  }'
```

`protection` 在**创建与编辑两条路上都有**（`S1` 1.4.0 起）：`PATCH /environments/{key}` 接受同一个 `ProtectionSpec`，S4 用它整表替换该环境的提交时间窗。也正因为是整表替换，两端的表单在没读到现有窗口之前不让你改它 —— 读一次再写，才不会把别人配好的窗口抹掉。
`days` 用 ISO 星期（1=周一），时间按该环境自己的时区（`protection.timezone`）解释；`deletionPolicy` 不给时按类别取默认（非生产 `SAFE_ONLY`、生产 `FORBID`，`DX-ENV-002`）；`emergencyLock` 出现在这里会被 422（`DX-ENV-007`），锁定只有 `POST /environments/{key}/emergency-lock` 这一条路，因为只有它会记下是谁、什么时候、为什么。

```bash
# 6) 只改生产环境的提交窗口，其余字段不动
post PATCH /environments/dhis2-prod env-prod-2 '{
    "protection": {
      "timezone": "Africa/Addis_Ababa",
      "commitWindows": [{"days": [6], "start": "22:00", "end": "23:30"}]
    }
  }'

# 7) 加锁必须有理由；解锁不需要
post POST /environments/dhis2-prod/emergency-lock lock-1 '{"locked":true,"reason":"核对生产元数据期间冻结提交"}'
post POST /environments/dhis2-prod/emergency-lock lock-2 '{"locked":false}'

# 8) 探一次能力档案。这个头重复会**回放上一次的结果**，所以每次点击都要换一个新键
post POST /environments/dhis2-dev/probe-capabilities probe-1 '{}'

# 9) 停用一台环境（理由可选，但它是唯一那条"为什么安静了"的记录）
post POST /environments/dhis2-dev/disable disable-1 '{"reason":"实例迁移，暂停使用"}'

# 10) 这两条会 422，是表单提前挡住的那两类输入
post POST /environments env-bad-1 '{"environmentKey":"bad one","baseUrl":"https://x.invalid","versionConstraint":"2.41.x","credentials":[{"scope":"discovery","secretRef":"env://D"},{"scope":"import","secretRef":"env://E"}]}'
post PATCH /environments/dhis2-prod env-bad-2 '{"environmentClass":"UAT"}'
```

第 4 条是提供方编辑的验收点：`PATCH /governance/providers/{name}` 的语义是**未出现的字段保持原值**，所以修一个拼错的地址不必重填密钥引用；只送 `{"enabled":false}` 也不会顺带抹掉地址（早期这个操作只接受 `enabled`，要改地址只能重新 `POST` 整条登记，而登记是整行覆盖 —— 少填哪个字段哪个字段就变空）。第 4c 条把同一语义用在模型上：只送一个 `capabilities.vision`，`maxContextTokens` 与单价保持原值；空 PATCH 被 422 拒绝，不会变成一次"什么都没改但显示保存成功"。

**退役一个对象（总体 SRS 15.6 的"定向删除"，契约 1.14.0 起才真的走得通）**

删除是整个链条里唯一不可逆、且实例不提供回滚的动作，所以它被四道闸夹着：授权写在**变更申请**上，
名单要过 **S2 的编译期核验**，提交前还要 **S4 当场再核一次**关联数据。少任何一环，你拿到的都是拒绝而不是"试试看"。

```bash
# 11) 声明要退役哪些对象（只能填处置为 DEPRECATED 的 Registry 条目，否则 422）
post POST /projects/PRJ/change-requests cr-del-1 '{"reason":"退役不再记录的体重数据元","affectedBusinessKeys":["dataElement.weight"],"deletionCandidates":["dataElement.weight"]}'
#   填错会得到：422 VALIDATION_ERROR，details.notDeprecated 列出对不上的键名（§6.6）

# 12) 影响分析 → 批准 → start（start 会按影响范围重开设计线，CP-CR-004/006）
post POST /change-requests/CR/impact cr-impact-1 '{}'
post POST /change-requests/CR/approve cr-appr-1 '{}'
post POST /change-requests/CR/start cr-start-1 '{}'

# 13) 重跑设计线到 BASELINE_COMPILE，再跑一次部署运行；BIND_COMPILE 会把这份申请与候选清单一起交给 S2 编译
post POST /stages/ST-req/run stage-run-1 '{}'

# 14) 取那份包的产物 id 与哈希。**摘要里没有包的产物 id**（§11 第 7 条），走阶段详情：
get GET /workflows/WF          #   在 stages 里找这条运行的 BIND_COMPILE 那一行的 id
get GET /stages/ST-bind        #   → outputArtifactIds 四项：METADATA_PACKAGE / MANIFEST / DIFF_REPORT / VALIDATION_REPORT
get GET /artifacts/ART-package #   → type 确认是 METADATA_PACKAGE，contentHash 就是下面要填的 expectedHash

# 15) 提交删除：Idempotency-Key 必填，重复同一把键只会回第一次的结论，绝不会删第二次
post POST /environments/dhis2-dev/delete-objects del-1 '{"projectId":"PRJ","deletionArtifactId":"art-…","expectedHash":"sha256:…"}'
```

`expectedHash` 用那份包自己的哈希 —— 规则与 COMMIT 同一条（`DX-BR-05`：验过的就是提交的）。
候选清单为空时这一步会被拒，那是"这次运行没有授权退役任何东西"的正确答复（§11 第 6 条）；
PRODUCTION 类环境按 `deletionPolicy` 决定，默认 `FORBID`（`DX-ENV-002`），紧急锁定中的环境一律拒绝。


先在 `.deploy/env` 里把 `DHIS2_DEV_DISCOVERY` / `DHIS2_DEV_IMPORT` / `MODEL_API_KEY` 三个键取消注释填值，再 `./start.sh --restart`，否则表单存得下来、用时解析不出密钥。
字段全貌看免登录的 `https://<IP>:1583/api/v1/docs` 或 `/api/v1/openapi.json`。

所有 POST/PUT/PATCH 的重复提交都靠 `Idempotency-Key`；同键不同 body → 422 `IDEMPOTENCY_CONFLICT`，同键但上一次还在跑 → 409 `REQUEST_IN_PROGRESS`（可重试）。表单为「编辑」类提交把请求体摘要算进幂等意图，所以改一笔再提交不会被判定成重放。`POST /deployment-runs/{id}/commit` 是唯一**强制**要求这个头的。

### 8.6 申请放宽数据策略

数据外发策略是**两张矩阵**，界面一次读得到（`GET /governance/policy` 同时返回）：

- **上限 `ceiling`** —— S3 的 `POLICY_MATRIX`，产品允许的最宽档位；改它要动代码。
- **当前生效 `effective`** —— 从系统默认起步，每批一次放宽就永久变宽一格。

「治理 → 数据策略 → 申请放宽」的可勾项永远是 `ceiling[level] − effective[level]`：某密级已经顶到上限时，表单直接写「该密级已到上限，无可放宽」，而不是给一个注定被拒的按钮；`PHI_PII` 那一档永远是空的 —— 病人数据不允许送往任何模型。越过上限的申请回答 403 `AI_POLICY_RELAXATION_DENIED`（`details.ceiling` 会给出该密级允许到哪），不给 `change` 或理由留空都是 422。

点之前要看清这四条，它们都是核实过的行为：

| 事实 | 意味着 |
|---|---|
| **全组织** | 生效策略是组织级的，一次批准影响此后**每一个**项目能往哪儿发什么 |
| **永久** | 没有到期时间，界面上也没有「撤销放宽」。要收窄只能回到代码里的默认矩阵重新部署 |
| **进审计** | 理由原样写进 `ai.policy.relaxed` 事件与放宽记录，响应里能读回自己刚批的 `reason` / `change` / `createdAt` |
| **角色门还没生效** | 响应里的 `roleChecked` 目前只是操作员姓名，`roleGate` 会写明当前门是「MVP 操作员姓名」；真正的角色校验是 M4 |

成功后界面立刻重读策略矩阵，所以「批了但看不见」这种情况当场就会暴露。`fakes` 模式也一样真改：早期版本把上限当成生效策略返回，于是每个密级看起来都已到顶、申请也只回一个假 id —— 那正是这个入口当初没做成按钮的原因。

移动端是同一套数据：「治理 → 放宽策略」的抽屉里用分段按钮选密级，可勾项同样来自两份矩阵的差集。

```bash
# 读两张矩阵：effective 是现在能做什么，ceiling 是最多能放到什么
curl -ks -b "$JAR" "$BASE/governance/policy"

# 批准一次：让 INTERNAL 多允许一类外发目标
post POST /governance/policy/relaxations relax-1 '{
    "change": {"level": "INTERNAL", "addClasses": ["APPROVED_EXTERNAL"]},
    "reason": "跨语言术语比对需要经审批的外部模型",
    "scope": "organization"
  }'

# 验收点是"再读一次看得见"：上一条 201 之后，effective.allowed.INTERNAL 应该多一项
curl -ks -b "$JAR" "$BASE/governance/policy"

# 这三条都会被拒：PHI_PII 不可外发；越过上限；没有 change
post POST /governance/policy/relaxations relax-2 '{"change":{"level":"PHI_PII","addClasses":["LOCAL_ONLY"]},"reason":"仍会被拒","scope":"organization"}'
post POST /governance/policy/relaxations relax-3 '{"change":{"level":"CONFIDENTIAL","addClasses":["UNRESTRICTED"]},"reason":"越过上限","scope":"organization"}'
post POST /governance/policy/relaxations relax-4 '{"reason":"缺 change"}'
```


### 8.7 凭证的两条录入路（以及厂商目录）

从契约 1.7.0 起，**配好一个环境或一个模型不再需要 SSH**。 DHIS2 的用户名/口令、API token、模型 API key 都可以
直接在界面里录进去；值只在按下去的那一瞬间经 HTTPS 发一次，服务端用主密钥加密后存进 `cp.secrets`，
之后任何接口都只回一个引用。

| 路径 | 引用写法 | 值住在哪 | 什么时候用 |
|---|---|---|---|
| **界面录入**（新） | `db://名字` | `cp.secrets` 里的 AES-256-GCM 密文，密钥由 `FACTORY_CREDENTIAL_MASTER_KEY` 派生 | 日常配环境、换 key、给某个环境单发一个令牌 —— 不用碰服务器 |
| **环境变量** | `env://键名` | `.deploy/env`（0600） | 想让值由配置管理工具/CI 统一下发；或这台机器故意没配主密钥 |

两条可以混用：同一个环境的 `discovery` 用 `env://`、`import` 用 `db://` 也收。`vault://`、`secret://`、`kms://`
仍在引用白名单里（历史兼容），但**没有对应后端**，填了只会在真正调用时失败 —— 所以两端的下拉与提示里都不列它们。

**界面上在哪**：凡是有 secretRef 输入框的地方，旁边都有「录入新凭证」。点开是一小块面板：引用名 + 令牌
（或用户名 + 口令，Basic 认证会按 `user:password` 组合成一个值，这是 S4 要求的形状）。面板上的输入框是
`type="password"` + `autocomplete="new-password"`，请求发出后无论成功失败值都立刻从组件里清掉；
旁边还有一个下拉，列出已经录过的引用，同一个 token 不必输第二遍。

**三个端点**：

```
PUT    /api/v1/secrets/{name}     body {"material": "<值>"}   → 创建或轮换，响应只有名字、引用与时间
GET    /api/v1/secrets            → 只列名字/引用/时间戳与总数
DELETE /api/v1/secrets/{name}     → 还被引用时 409 SECRET_IN_USE，details.referencedBy 列出是谁在用
```

- **没有 `GET /secrets/{name}`**。那是一个能拿 403/404 猜名字探针，值也本来就不可回读。
- `PUT` 是 upsert：同一个名字再写一次就是轮换，`rotatedAt` 记下换值时刻。这一条让「重试」是安全的 ——
  一次网络抖动之后重发同一个请求，得到的是同一个结果，不是第二条记录。
- 名字规则 `^[a-z0-9][a-z0-9._-]{1,63}$`。**不要把用户名放进名字**：名字会被 `GET /secrets` 列出来，
  而列表是任何有 `secret.manage` 的人都读得到的。
- 三条端点都挂 `secret.manage`，默认授予 `PLATFORM_ADMINISTRATOR` 与 `DHIS2_IMPLEMENTER`。
  `/secrets` 被排除在幂等回放表之外 —— 那张表逐字存响应体，引用不该进回放。
- 值不会出现在这些地方：响应体、审计的前后摘要（只手写名字与引用）、日志，以及 **422 的校验错误体**
  （pydantic 会把提交的输入原样抄进 `ctx.input`，所以对名为 `material` 的字段服务端在渲染前先抹掉）。

**主密钥**（`FACTORY_CREDENTIAL_MASTER_KEY`，`install.sh` 自动生成，`start.sh` 会给老机器补一行）：

| 情况 | 表现 |
|---|---|
| 没配 | 三个端点回 503 `SECRET_UNAVAILABLE`（`details.reason = storeDisabled`），界面提示「改用 `env://` 引用」；**服务照常启动**，其余功能不受影响 —— 把可选功能缺失变成起不来，会让一台本来能跑的机器因噎废食 |
| 换了值 | 旧密文解不开，解析时报 `SECRET_UNAVAILABLE` + `details.reason = keyMismatch`。`GET /setup` 的 `credentials.unresolvable` 会列出受影响的环境与提供方。修法是把每一个凭证重新录一遍（多版本保留与全库重加密推到下一轮） |
| 太短 | `validate_runtime()` 直接在引导页顶部那句话里点名这个变量，并说「至少 32 个字符」 |

主密钥只写在这一行里，不入库、不回显、不写日志。它保护的是「库被拖走但文件没被读到」这一层；
**部署主机被攻陷不在保护范围内**（总体 SRS FR-SEC-006 明写了这条边界），那时拿到 env 文件就等于拿到一切。

**等价的 curl 路径**（登录取 CSRF 的写法同 §8.5；`X-Operator` 在 `auth=off` 下必带）：

```bash
BASE=https://127.0.0.1:1583/api/v1
# 录一个值 → 只回引用。名字用 ASCII：它会被 GET /secrets 列出来。
post PUT /secrets/dhis2-dev-import '{"material":"admin:s3cr3t"}'
post PUT /secrets/model-key          '{"material":"sk-abcdef"}'
curl -ks -b "$JAR" "$BASE/secrets"                       # 名字、db://引用、时间；没有任何值
curl -ks -b "$JAR" -X DELETE "$BASE/secrets/dhis2-dev-import"   # 被引用 → 409 + details.referencedBy
curl -ks -b "$JAR" "$BASE/governance/provider-catalogue" | python3 -m json.tool
```

同名再 `PUT` 一次就是轮换：`rotatedAt` 会多出值，`createdAt` 不变。带不带 `Idempotency-Key` 都一样 ——
`/secrets` 被排除在回放表之外，重发的语义由 `PUT` 本身保证。

**厂商目录**：`GET /api/v1/governance/provider-catalogue`（读权与 `GET /governance/providers`、
`/governance/models` 同一条，都是 `governance.manage`）回
`{key, displayName, endpoint, wireProtocol, models[], keyRefSuggestion, docsUrl, local, note}`。
`local` 那几条（Ollama、vLLM、LM Studio）的 `keyRefSuggestion` 是**空的**：它们默认不鉴权，目录不给一个
"建议的引用名"就是因为那个名字没有对应的密钥——表单里那一栏直接留空即可（§8.3）。
目录里只有 OpenAI 兼容的厂商（OpenAI、DeepSeek、阿里云百炼、Moonshot、智谱、硅基流动、Groq、Together、
OpenRouter、Ollama、vLLM、LM Studio），因为**本版本只实现了这一个线协议**：S3 的 `provider_for()` 连
`adapter` 字段都不看，Anthropic/Gemini 原生接口既没有 `/chat/completions` 也要别的鉴权头 ——
把它们列进候选，就是给用户一个存进去必失败的地址。

地址与模型名**仍然可以自由输入**（私有网关、微调、改名的部署是常态），目录只是起点；
`note` 字段会把「这一版只说 OpenAI 兼容」这句话原样带给界面。目录条目**不代表已验证连通**（AI-CAT-001）：
能不能通，只有连接测试和真实调用知道。

**这台机器上量过的**（2026-10-04，`https://127.0.0.1:1583`，替身提供方）：`PUT /secrets/measure-ascii` 回 200，
响应只有 `{name, ref, algo, createdAt, updatedAt, rotatedAt, createdBy}`；同名再写一次 → 仍然只有一行，
`rotatedAt` 多出值；中文名字 `测量` 被 422 拒掉，且**整个响应体里找不到提交的那个串**（ASCII 与 UTF-8 两种查法都干净）；
建一台 `authMode=BASIC`、两条凭据都写 `db://measure-ascii` 的环境 → 201，读回来只有引用；
`test-connection` 八项检查全 `PASSED`（含 `authentication`）—— 这一条才是"引用 → S0 解析 → `db` 后端 → 解密 →
`user:password`"整条链子在真进程里跑通的证据；`GET /setup` 的 `credentials` 给
`{storeAvailable:true, masterKeyConfigured:true, scheme:"db", named:1, unresolvable:[]}`；
删还被引用的凭证 → 409 `SECRET_IN_USE` + `details.referencedBy` 点名两个 scope，把引用挪到另一个名字之后再删 → 200；
`/setup`、`/environments`、`/governance/providers`、`/governance/provider-catalogue`、`/secrets`、`/audit`
六条读路径里都搜不到那个值；目录回 12 家，`wireProtocol` 只有一种 `openai_compat`，地址都以 `/v1` 或 `/v4` 结尾。
两端引导页也在浏览器里真走了一遍：步骤条 4 步、当前一步 `aria-current="step"`，环境那一步就地开表单，
凭证录进去之后字段里剩下 `db://dhis2-env-discovery`，而 `localStorage`/`sessionStorage` 与页面上都找不到那个值。


---

## 9. 运维

### 9.1 服务与日志

```bash
systemctl status damf-api damf-worker nginx --no-pager
journalctl -u damf-api -f                  # 后端实时日志
journalctl -u damf-worker -f               # 周期任务 / Temporal worker
UNIT=damf-worker ./start.sh --logs         # 只过滤一个单元
LOGLINES=300 ./start.sh --logs             # 两个单元各 300 行
tail -f /var/log/nginx/access.log          # 需 sudo；www-data:adm
tail -f /var/log/nginx/error.log
sudo systemctl restart nginx               # 只动 nginx
```

后端与 worker 日志都走 journald（`StandardOutput=journal`），文件里没有单独的应用日志。
`NoNewPrivileges=true` + `Restart=always`（api 3 秒、worker 5 秒退避），崩溃会自己拉起来 —— 反复重启先查
`journalctl -u damf-api -n 100` 或 `journalctl -u damf-worker -n 100`。

### 9.2 健康检查的正确姿势

```bash
curl -sS http://127.0.0.1:8090/healthz | python3 -m json.tool
curl -sS http://127.0.0.1:8090/readyz                            # 就绪探针：真问数据库，503 = 库不通
```

两个必须知道的点：

- **`/healthz` 没有被 nginx 反代**。站点只代理 `/api/v1/`，`location / { try_files ... /index.html; }` 会把 `/healthz` 兜到前端首页 —— 从外面打 `https://<IP>:1583/healthz` 会拿到 **200 + 一段 HTML**，看着像健康、其实不是。
- 外部探针改打免登录的 API 路径，实测行为如下（`local` 模式）：

  | 探针 | 结果 | 含义 |
  |---|---|---|
  | `GET /api/v1/openapi.json` | 200 + JSON | 反代与后端都通，最省事的存活探针 |
  | `GET /api/v1/auth/session` | 200 + `{"authenticated":false,"authMode":"local"}` | 它在认证白名单里，**200 不代表已登录** |
  | `GET /api/v1/projects` | 401 + `{"errorCode":"UNAUTHENTICATED"}` | 用来确认认证中间件确实在拦 |
  | `GET /api/v1/setup` | 登录后 200；未登录 401 | **界面读的正是它**（§6.0）：替身模式、配置问题、登记了什么都在里面 |
  | `GET /readyz`（回环） | 200 / 503 | 真探数据库；503 = 库不通，不能进负载 |

- ~~**没有 `/readyz`**~~ —— 已有：`GET /readyz`（只监听回环，不经 nginx）拿运行时的 unit of work 真执行一条语句，数据库不可达回 **503**。它 `include_in_schema=False`，不进 OpenAPI 契约（基础设施探针不是前端操作，147 条路径的钉数不变）。就绪探针打它，存活探针可以继续打 `/healthz`。
- `/healthz` 的 `status:"ok"` 仍是常量（**不探数据库**——这正是 readyz 补上的那半句）。真正有意义的是 `startup.appliedSchemas`、`providers.*`、`engine.detail.pendingStages`。

`start.sh --status` 已经把服务、监听端口、`/healthz` 与两个站点串起来做了，日常用它；`/readyz` 目前要手打（它才是"库通不通"的那一句）。

### 9.3 数据库

```bash
psql damf                              # ubuntu 用户免口令进（socket peer）
psql damf -c '\dt cp.*'
psql damf -tAc "select count(*) from cp.projects"
```

| schema | 归谁 | 表数（本机） | 版本表 |
|---|---|---|---|
| `core` | S0 | 11 | `core.core_alembic_version` |
| `dx` | S4 | 13 | `dx_alembic_version` |
| `bp` | S2 | 15 | 同规则 |
| `ai` | S3 | 21 | 同规则 |
| `cp` | S1 | 25 | `cp.cp_alembic_version` |

迁移由 **`factory-api` 启动时**按固定顺序 `core → dx → bp → ai → cp` 跑 alembic，schema 本身也是迁移建的（`install.sh` 不预建）。`fakes` 模式下 `bp/ai/dx` 照样会建。
升级前手工备份：

```bash
sudo -u postgres pg_dump -Fc damf > /backup/damf-$(date +%F).dump
```

想让升级不自动改结构（比如灰度第一台）：`FACTORY_MIGRATIONS_MODE=check`，它只比对最后一版迁移文件声明的 revision 和库里当前 revision，不匹配就拒启。
回退结构需要显式目标而且非生产 `APP_ENV`，实际上没这条路 —— 结构变更只前进。

审计表 `core.audit_event` 有 PL/pgSQL 触发器强制**只追加**（禁改禁截断），别指望手工清历史。

### 9.4 产物与对象存储

```
.deploy/state/objects/<projectId>/<ARTIFACT_TYPE>/<artifactId>
```

原子写（`.tmp` + `os.replace`），带路径穿越防护。本机当前约 1.5 MB、66 个文件（7 个项目目录，其中 5 个在册项目）。
这目录是**业务数据**，与数据库同等重要：库里存元数据与哈希，内容在这里。两者都要备。
被清理（retention purge）的产物在界面显示「内容已清除」，数据库里的注册记录还在。

换成 S3（MinIO 也可以）：

```
OBJECT_STORE_URL=s3://damf/state?endpoint=http://minio.internal:9000
```

并给 venv 装 boto3：`S1/.venv/bin/pip install -e 'S0[s3]'`，然后 `./start.sh --restart`。
`memory://` 只适合临时试跑，重启就丢。

### 9.5 事件流与 SSE

- 事件**不靠内存**：每条都落 `cp.event_stream_records`，回放从库里读，每批 500 条。
- 推送：写事件的同一事务里发 `pg_notify('<FACTORY_SSE_CHANNEL>', '<workflowId>:<seq>')`（默认频道 `factory_cp_events`），API 进程启动时开**一条** `LISTEN` 连接（`temporal` 引擎除外）。
- **频道名两侧同源**：NOTIFY 与 LISTEN 都读 `FACTORY_SSE_CHANNEL`（默认 `factory_cp_events`），不是一边写死、一边读配置。名字必须是 PostgreSQL 标识符（字母/数字/下划线，不以数字开头），否则 `validate_runtime()` 直接拒启；改它不会静默断掉实时推送。
- 帧格式 `id: <序号>\nevent: <类型>\ndata: {...}\n\n`；首个注释帧 `: connected`，每 20 秒 `: keep-alive`。
- 断线重连用 `Last-Event-ID` 或 `?after=`；如果请求的序号比库里最早一条还旧（或已被清理），服务端发一帧 `stream.reset`（`reason: REQUESTED_SEQUENCE_EXPIRED`）然后结束流，前端收到就整项目重取。**看到 `stream.reset` 是正常恢复，不是错误。**
- 保留期由 `cp.system_settings.event_retention_days`（默认 7 天）控制，清理任务是 `purge_events` —— 但见 §9.6。
- **进度帧的字段名以服务端为准**：`agent.progress` / `execution.progress` 的 payload 是
  `{step, completedFraction, message, stageId, agentRunId, executionId, data}`（`S1/progress.py` 的 reporter
  写这一份，S0 的协议里根本没有 `completed` / `total` 这种步数计数）。两个界面都按这个名字读：进度条读
  `completedFraction`，「取消本次 Agent 运行」用的 run id 读 `agentRunId`。**读错名字的症状是"进度条一直是空的"**，
  而不是报错 —— 所以改这一帧时请连前端一起改。
- nginx 对 SSE 单独做了 `proxy_buffering off; proxy_cache off; gzip off; chunked_transfer_encoding off; proxy_read_timeout 1h; X-Accel-Buffering: no`。若你自己改 nginx，这一整块必须保留，否则实时进度就没了。

排查 SSE：`curl -ksN https://127.0.0.1:1583/api/v1/workflows/<workflowId>/events`，能看到 `: connected` 说明链路通。

### 9.6 定时任务：`damf-worker` 单元（随 install.sh 安装）

`install.sh` 渲染两个 systemd 单元：`damf-api` 与 `damf-worker`（`ExecStart=S1/.venv/bin/factory-worker`，`Restart=always`、`RestartSec=5`，同一个 `EnvironmentFile`）。`start.sh` 对两个单元一起启停/查状态/看日志（`LOGLINES=300 ./start.sh --logs` 默认两个都打，`UNIT=damf-worker ./start.sh --logs` 只看一个）。**注意启动顺序**：`./start.sh` 会先等 api 的 `/healthz` 通过再起 worker——迁移没有并发锁，两个进程同跑 `upgrade` 只在"库已是 head"时才是无害空操作。worker 跑十个周期任务：

| 任务 | 周期 | 职责（不跑时的代价） |
|---|---|---|
| `auto_continue` | 5s | 按系统设置启动被标记为自动的阶段（只推进自动阶段，不绕过任何 Gate） |
| `link_audit` | 5s（`FACTORY_AUDIT_LINK_INTERVAL_SECONDS` 可调） | 审计哈希链回填（`POST /api/v1/audit/verify` 仍可内联补一次） |
| `housekeeping` | 1h | 幂等记录等杂项清理 |
| `verify_chain` | 24h | 审计链完整性自动校验 |
| `purge_events` | 24h | SSE 事件表按 `event_retention_days` 清理（不跑就无限增长） |
| `purge_llm_exchanges` | 24h | S3 模型调用记录清理（记录里引用了输入原文，不能永久保留） |
| `retention` | 24h | 归档项目产物清理（默认 `archived_project_retention_days=0` 本来也关着） |
| `check_credentials` | 24h | 凭据到期提醒 |
| `deliver_notifications` | 5min | 邮件/Webhook 投递重试（上限 `FACTORY_NOTIFICATION_MAX_ATTEMPTS`；站内通知的行本来就是业务代码内联写的，不依赖它） |
| `gate_reminders` | 15min | Gate 催办 |

手工跑一次全部任务仍然可用（调试、或在没装 worker 的旧机器上临时补一轮）：

```bash
S1/.venv/bin/factory-worker --once
```

引擎语义提醒：`FACTORY_WORKFLOW_ENGINE=inprocess` 时 `worker_main` 起一个 asyncio 循环常驻跑上面这些任务；换成 `temporal` 时同一入口变成 Temporal worker（周期任务改由 Temporal schedule 驱动），单元照常装着即可，不需要为它改配置。首次装好 worker 的旧部署要注意**积压**：历史 pending 的通知投递与未链接的审计会在这一次集中补做——上线前可先 `psql damf -tAc "select count(*) from cp.notification_deliveries where status='PENDING'"` 看一眼量级。

### 9.7 备份与恢复

要备的三样：

```bash
sudo -u postgres pg_dump -Fc damf > damf.dump            # 1. 库
tar czf state.tgz -C /home/ubuntu/projects/DAMF .deploy/state .deploy/tls .deploy/env   # 2. 产物+配置+证书
# 3. 代码本身在版本库里，记下 install.sh 的命令行参数即可
```

恢复到新机器：

```bash
git clone <repo> && cd DAMF
./install.sh --ip <IP> --port 1583 --mobile-port 1584 --api-port 8090
#   这一步建好角色、空库 damf、venv、前端、证书，但**没有启动服务**，
#   所以数据库里还没有任何表 —— 迁移是 factory-api 启动时才跑的。正好用来还原。
tar xzf state.tgz                                 # 用备份覆盖 .deploy/{state,tls,env}
chmod 600 .deploy/env .deploy/tls/key.pem
sed -i "s|file:///旧机器/|file://$PWD/|" .deploy/env   # OBJECT_STORE_URL 是绝对路径，必须改
pg_restore -d damf --clean --if-exists damf.dump  # 还原进那个空库
./install.sh --render-only && ./install.sh --config-only   # 用还原回来的 env 重渲染端口/上游
./start.sh
```

`--clean --if-exists` 会先 DROP 再建，所以目标库必须是刚建好的空库；否则先 `psql -c 'DROP DATABASE damf'` 再 `createdb damf`。
备份里的 `.deploy/env` 若带着旧机器的 `DATABASE_URL`（角色名不同）或绝对路径，按上面 `sed` 的思路一并核对。
引导口令沿用备份里的值。想重置：改掉 `.deploy/env` 的 `FACTORY_BOOTSTRAP_ADMIN_PASSWORD`，并删掉 `cp.users` 里那个账号（因为「仅当账号不存在才创建」），否则改动不生效。

### 9.8 证书

```bash
openssl x509 -in .deploy/tls/cert.pem -noout -subject -dates -ext subjectAltName
./install.sh --renew-tls && ./start.sh --restart      # 强制重签
cat .deploy/tls/san.txt                               # 当前 SAN；变了才重签
```

`key.pem` 必须 0600（脚本会 `chmod 600`）。nginx 直接引用仓库内路径，所以 `.deploy` 目录不能删。

### 9.9 端口与放行

| 端口 | 谁听 | 是否要公网 |
|---|---|---|
| 1583 / 1584 | nginx（TLS） | **要**，云安全组 + 任何上层防火墙都要放行 |
| 8090 | factory-api（仅 127.0.0.1） | 不要 |
| 5432 | PostgreSQL | 不要 |

本机 `ufw` 是 inactive 的，所以能不能从公网进来完全取决于云厂商安全组。`./start.sh` 最后会提醒放行了哪两个端口。

```bash
ss -ltn | grep -E ':158[0-9]|:8090'
```

### 9.10 更新代码后

```bash
git pull
./install.sh --config-only            # 只改了配置/端口/模式时
./install.sh                          # 改了前端或依赖（重新 npm ci + build + 落 /var/www）
./start.sh --restart                  # 改了 Python（venv 里是可编辑安装，重启即生效）
```

**改了 REST 契约（`S1/src/factory_control/rest/**` 里的模型或路由）之后，顺序是固定的四步**，少一步就是一次"改了但界面上看不见"的故障：

```bash
cd S1 && ./.venv/bin/python scripts/export_openapi.py --out docs/openapi.json   # 1) 重新导出契约（并把 openapi.py 里的 VERSION 提一级）
cp S1/docs/openapi.json S5/contract/openapi.json                  # 2) 两份 vendored 副本都要换
cp S1/docs/openapi.json S5M/contract/openapi.json
cd S5  && npm run codegen                                        # 3) 两端重新生成生成文件
cd ../S5M && npm run codegen
./install.sh                                                     # 4) 构建——守卫就在这里等着
```

第 3 步生成的文件要**连同契约一起提交到开发机上的仓库**（本机 `/home/ubuntu/projects/DAMF` 不是 git 工作副本，
`git pull` / `git add` 在这台机器上都不成立，别照着上面的注释在本机执行）。

不这么做也不会静默出错，因为 `install.sh` 在构建每个控制台前会备份 `src/api/generated`、重跑一次 codegen、
再逐文件比对，然后**无条件把原样还原**；只要有一处不一致就中止构建并点名是哪个控制台、哪些文件不同。
这一条是补出来的：构建编译的是**签入**的生成文件，所以契约改了而生成文件没跟上时 `tsc`/`vue-tsc` 依然是绿的
—— 本轮就是这样把 1.10.0 新加的 `codePrefix` 漏在了 S5M 之外，直到重新生成才当场暴露。
（比对与还原之间用 `|| true` 兜住 `pipefail`：`diff | head` 会以 SIGPIPE 的非零状态退出，命令替换把那个状态
原样传给赋值，在 `set -e` 下子 shell 会静默死掉 —— 既不报信息也不还原工作区，症状是"构建莫名其妙少了一半"。）

**版本号本身现在也有地方住了**：两端各有一条 `src/test/contractVersion.test.ts`，断言自己 vendored 副本声明的
`info.version` 与路径数就是签入生成物所依据的那一版（现在 1.19.0 / 147）；S1 侧同名职责在
`tests/api/test_openapi_contract.py::test_document_version_and_servers`。提了契约而不改这两处，红的是提版的人自己。

结构变更由 `FACTORY_MIGRATIONS_MODE=apply` 在启动时自动跑，先看一眼 §9.7 的备份。

---

## 10. 故障排查

| 症状 | 原因 | 处置 |
|---|---|---|
| `400 Bad Request / The plain HTTP request was sent to HTTPS port` | 用了 `http://` | 改 `https://`。已配 `error_page 497` 自动 301，若仍出现说明 nginx 配置被手工改过：`./install.sh --render-only && ./install.sh --config-only` |
| 浏览器证书告警 | 自签证书 | 「继续访问」；或换 certbot（§2.3） |
| 页面能开、一刷新就掉登录 | 走了 http（Secure Cookie 不保存） | 必须 https（§2.2） |
| 公网完全连不上 | 安全组没放行 | 先在本机 `curl -ks https://127.0.0.1:1583/` 确认服务活着，再去开安全组 |
| `./start.sh` 卡在「等待 /healthz」后报未起来 | 后端启动失败 | 看它自己打出的日志尾 30 行；常见是 `DATABASE_URL` 连不上、`FACTORY_HTTPS_ONLY` 不为 true、迁移失败 |
| ~~启动即崩 `SecretEnvBackendForbiddenInProductionError`~~ | 已修：`APP_ENV=production` 下 bootstrap 不再装 env 后端而是干净降级 | 现在这条崩溃不会再出现；生产档 `env://` 引用按设计解析失败，凭证改走 `db://`（§5.1） |
| `nginx -t` 没过、install 脚本说已撤销本站点 | 手改过 `.deploy/nginx.conf` 或端口冲突 | 看它打出的 `nginx -t` 输出；`ss -ltnp \| grep <端口>` 找占用者；然后 `--render-only` 重渲染 |
| 登录后 401 一片 / 「会话已失效」 | 空闲 30 分钟或绝对 12 小时到期 | 重新登录；要更久就改 env 两项后 `--restart` |
| 403 `CSRF_FAILED` | Cookie 认证的写操作没带 `X-CSRF-Token` | 界面自动带；手工 curl 要照 §8.5 取 `csrfToken` |
| 400 `OPERATOR_REQUIRED` | `auth=off` 时写操作缺 `X-Operator` | 界面上填名字；脚本加 `-H 'X-Operator: 张三'` |
| 409 `INVALID_STATE` | 阶段状态不允许这个动作 | 读响应里的 `details.allowed`，那才是此刻能做的事 |
| 422 `IDEMPOTENCY_CONFLICT` | 同一 `Idempotency-Key` 换了 body | 换新键。**两个控制台自己会换**：一次被答复的拒绝已经把那把键用掉了，界面在收到答复后就释放它（S5 1.9.0 / S5M 1.10.0）—— 只有没人给出答复的那一次才留着键（网络失败、`REQUEST_IN_PROGRESS`），因为 COMIT 那种不可重复的动作绝不能凭一把新键再发一遍 |
| 登录返回 401 `UNAUTHENTICATED`，口令明明取自 `.deploy/env` | 引导口令只在建号那一刻被读一次，之后以库里那份为准；在界面上改过之后 env 里那串就是历史值 | 用改后的口令登录；忘了就由另一个管理员在「用户」页重置（§6.6），**只剩一个管理员时**用 `S1/.venv/bin/python S1/scripts/reset_admin_password.py`（`--sync-env-file` 顺手把 env 里那行同步掉，脚本自测见 `S1/tests/unit/test_reset_admin_password_script.py`）。另外注意连续 5 次错会锁一段时间，而登录端点故意不区分"锁了"和"口令错" |
| 项目列表空着或 403，但账号确实有项目级角色 | 1.14.0 之前：`GET /projects` 只问组织级那一句，项目级成员一律 403；1.14.0 之后：列表按成员关系过滤，看不见别人的项目是**应有行为** | 想看到全部项目就授组织级 `project.view`（`§6.6`）；只想多管一个项目就把自己加进那个项目的成员 |
| 409 `REQUEST_IN_PROGRESS` | 同一键上一次还在跑 | 等它结束再重试 |
| 409 `FEATURE_NOT_ENABLED` | `off` 模式下动成员/授权/豁免 | 改 `local` 模式，或设 `FACTORY_M4_FEATURES_ENABLED=true` |
| 409 删除项目，`details.requires = "PROJECT_EXPORT"` | `CP-PRJ-007`：归档之后要先产出导出产物才让删 | 先 `GET /projects/{id}/export`（回 202 与那个 PROJECT_EXPORT 产物 id），再删 |
| 403 `PERMISSION_DENIED`，但人是平台管理员 | 项目级动作还要「是该项目的成员」（FR-SEC-004）——全局角色不越过成员关系 | 先把操作人加进「成员」，再执行导出/删除 |
| 某阶段一直 `FAILED`「模型输出被拒绝」 | 数据策略或预算闸 | 提示会写明「超出成本预算」或「数据外发策略拒绝」；改用「人工编写」，或去治理页调策略/预算 |
| DISCOVERY 阶段 `INVALID_STATE` | 项目没标（或标了多个）设计基准环境 | 编辑项目目标环境，只留一个 ★ |
| `UID_POOL_EXHAUSTED`（可重试） | 设计基准环境的 UID 池空了 | 从 S4 补 `system/id`；重试即可 |
| 「还没有任何环境」`--seed` 不灌 | `auto/real` 下确实没有环境 | 在「环境」页登记（§8.5），或临时 `./start.sh --providers fakes --seed` |
| 环境表单点「保存环境」是灰的 | 本地校验未过：无密钥引用、引用不是白名单前缀、勾了 TLS 却不是 https、生产缺 discovery/import 或两者相同、缺版本约束、生产缺提交时间窗…… | 按表单下方红字逐条改；文案与 S1 的 422 规则同源 |
| 点「加密保存并填入引用」回 **503 `SECRET_UNAVAILABLE`**，`details.reason = storeDisabled` | 这台部署没有 `FACTORY_CREDENTIAL_MASTER_KEY`，凭证库根本没启用（不是保存失败，也不是权限问题） | 按 §8.7 补这一行后 `./start.sh --restart`；或者这一次就改用 `env://键名` 引用。界面给的中文提示写的就是这条 |
| 环境/提供方忽然跑不动，`GET /setup` 的 `credentials.unresolvable` 里出现 `keyMismatch` | 换了主密钥去解旧库 —— 旧密文与新密钥派生出的库密钥不匹配，解不开 | 把受影响的凭证逐个重新录入（同名 `PUT` 即轮换）。全库重加密与多版本保留这一轮没做，见 §11 |
| `DELETE /secrets/{name}` 回 **409 `SECRET_IN_USE`** | 还有环境或提供方在引用它，`details.referencedBy[]` 列出是谁 | 先把那些持有者换到别的引用（或改回 `env://`），再删。故意不做「连带清空引用」——那会让一个环境在下一步突然没有凭据可用 |
| 提供方改了地址，密钥引用却没了 | 走的是 `POST` 重新登记（整行覆盖），不是 `PATCH` | 用「编辑提供方」，它发 `PATCH /governance/providers/{name}`，未提交的字段保持原值 |
| 保存完环境，R3 却 BLOCKING | 环境没做过连接测试 | 「环境」行点「测试连接」，看八项检查都过（`DX-EXE-012`） |
| R3 `DHIS2_VALIDATE` 一上来就 BLOCKING | 没做过连接测试 | `POST /environments/{key}/test-connection`（§8.4） |
| 提交后卡在「需人工核对」，且环境一直锁着 | COMMIT 结果不确定会占住跨项目全局锁 | 先 `POST /executions/{id}/reconcile`，再 `resolve-manual_check`；**别重发部署** |
| 实时的 Agent 进度不动了 | SSE 断了 | 桌面 >10 秒断线会显示「与服务器的连接已断开…」横幅；S5M 自动转轮询并在顶栏挂出「已切换为轮询刷新」。检查 nginx 的 SSE location 是否被改坏（§9.5） |
| 停用过的环境点什么都回 409 | S4 拒绝针对停用实例的任何操作（`DX_ENVIRONMENT_INVALID`） | 不是坏了。界面上这三条按钮本该是灰的（`allowed-actions` 给 `ENVIRONMENT_DISABLED`），绕过界面用 curl 直接打会得到同样的 409 —— 两种提供方一致。**停用是单向的**：档案仍可编辑、锁仍可解除，但没有任何接口把它重新启用（§8.5） |
| 放宽策略被 403 `AI_POLICY_RELAXATION_DENIED` | 该密级已到上限，或选了 PHI_PII | 读 `details.ceiling` 看允许到哪；PHI_PII 不可放宽（§8.6） |
| 事件回放整屏重取 | 收到 `stream.reset` | 正常恢复（序号早于保留窗口），§9.5 |
| `/healthz` 200 但内容像网页 | 你打的是 `https://…/healthz`，被 `try_files` 兜到前端首页 | 打 `http://127.0.0.1:8090/healthz`（§9.2） |
| 整份问卷点「提交全部回答」没反应/灰着 | 还有阻断题没答，或一题都没答；按钮的可用性还受 `question.answer` 约束 | 看页面上的「还有 N 个阻断问题未回答」；每道阻断题选一个建议或填「其他回答」，再提交。仍灰就看按钮旁 S1 给的原因 |
| `POST /questions/answers` 回 409/422 | 答案跨了阶段/工作流/项目、同一题出现两次、某题既没选建议也没填文本，或超过 200 条 | 一次只提交同一阶段的一整卷；每题二选一；换一轮问卷换一个新的 `Idempotency-Key` |
| 提交后提示已保存，但 Agent 没继续 | 还有阻断题未答（服务端只落答案、不 resume），或这次只答了选填题 | 把剩余阻断题答完再提交；响应里的 `resumed` 与 `questions` 会说明是否真的续跑、下一轮还剩什么 |
| 提供方「测试连接」显示「地址没有给出可用应答」，但环境测试是好的 | 两者探的不是一件事：提供方探的是模型地址的 `GET /models`，环境探的是 DHIS2 的八项检查 | 看 detail 原话：地址错、密钥被拒、超时是三句不同的话；先修提供方 `endpoint` / `apiKeySecretRef` |
| 改模型后另一个能力被打回默认 | 用了 `POST /governance/models` 重新登记（整行覆盖），不是 `PATCH` | 用 `PATCH /governance/models/{ref}` 只送改动字段；空 PATCH 本来就会被 422 拒掉 |
| 需求条目页保存后列表没变 | 1.14.1 之前的失效键 bug；或这条保存被 S2 判成 `SCHEMA_ERROR` | 升级到 S5M 1.14.1；`SCHEMA_ERROR` 时按页面提示刷新后重做（写前会重读一次整段） |
| 通知里的邮件从不发 | `deliver_notifications` 没跑（worker 未装） | §9.6 |
| 改了 `FACTORY_DEFAULT_MAX_UPLOAD_BYTES` 没反应 | 该值只在首次播种时写库 | 改「系统设置」页或 `PUT /api/v1/settings`（§5.4） |
| 改了 `.deploy/env` 重启也没生效 | 只有 systemd 重启才重读 EnvironmentFile；`systemctl start` 对已运行进程是空操作 | `./start.sh --restart`（它自己会做 mtime 比对） |
| 404 且提示「不属于当前里程碑」 | 功能在 M3/M4 才交付 | 见 §11 |
| 刚装完，界面每个页面都是空的 | 什么都没配置是**事实**，不是故障：环境、模型、项目都要人登记 | 打开 **开始使用**（桌面 `/getting-started`、手机 `/start`），它按顺序列出还差哪几步（§6.0） |
| 「开始使用」顶部出现红框句子 | `.deploy/env` 自相矛盾（如 `FACTORY_AUTH_MODE=local` 但 `FACTORY_HTTPS_ONLY=false`、OIDC 参数不齐、引导账号两个变量只写了一个） | 照句子改那几个变量，再 `./start.sh --restart`。句子只写变量名，不含取值 |
| 「开始使用」末尾的事实卡显示「需求解析：演示替身」（或蓝图编译／环境执行） | `FACTORY_PROVIDER_MODE` 还是 `fakes`，或真实包导入失败被回落 | 阶段会正常出结果，但编译/提交都不可交付；见 §8.1 接真实能力 || 新建项目向导停在环境一步 | 还没有任何环境，或所选环境全是生产（`CP-PRJ-002`） | 向导会写出原因并给「去登记环境」的跳转；照 §8.2 先登记一台非生产环境 |
| 「新建项目」按钮一直是灰的 | `auth=off` 之外的体制下角色没有 `project.create`；S1 从 1.6.0 起才在 `allowed-actions` 里回答它 | 看按钮上的原因码；需要授权就走 §6.6 的角色表 |
| 阶段一开始就失败，说没有可用模型 | 提供方或模型一个都没登记 | 治理页登记模型，清单第 1 行会一直标着未完成 |
| Agent 运行返回 `FAILED` 且错误码是 `AI_PROMPT_NOT_ACTIVATED` | 该 agent 类型的 Prompt 从未走过「评测 ≥0.95 + 回归记录 + 激活」这条链——现在缺激活不再静默降级为空 Prompt，而是一开始就拒绝（不建运行行、不调模型） | 跑 `S1/.venv/bin/python S1/scripts/activate_prompts.py`（§8.3；幂等，可整链重跑）；界面上「治理 → 提示词」也能逐条激活 |
| 中文浏览器打开桌面控制台，第一屏却是英文 | 1.1.0 之前：语言检测交给 i18next 的 `nonExplicitSupportedLngs`，它让精确标签 `zh-CN` 的查找链塌成英文，于是界面全英文而 `<html lang>` 与 `Accept-Language` 都写 `zh-CN` | 升级到本轮的 S5 产物；当下可点右上角语言切换（选择会存进 `damf:language`，之后一直生效）。移动端没有这个问题 |
| 名称填了中文之后，界面上**每一次**保存/提交都报「无法连接服务器，请检查网络」 | 不是网络：HTTP header 只允许 ISO-8859-1，浏览器对 `X-Operator: 张三` 直接在发出前抛 `TypeError`，请求根本没到服务器 | 两端 1.1.0 起自动百分号编码、S1 1.6.0 起自动解码（§2.4）。旧组合的绕法是把操作人姓名改成 ASCII。自己写的脚本两种写法都行 |
| 移动端一登录就弹「当前操作人无权执行此操作」 | 曾经不是 bug 的形状：壳层的顶部搜索上下文用 localStorage 里的「上次项目」无条件发 `workflow`+`registry` 两个读，那个项目被删后 S1 按"对非成员隐藏存在性"回 403，每次 refetch 播一次 toast（S5M 1.13.1 已修：面板打开才问、id 必须在本人项目列表里；权限模型本身没坏，平台管理员照常看得到全部在册项目） | 升级到本轮 S5M 产物即可；临时绕法是在「我的 → 清除本地缓存」里清掉旧的项目引用 |
| 前端构建 OOM / `npm ci` 卡住 | Node 版本低于 20 或内存不足 | `node -v` 应 ≥20.19（Vite 6 要求），必要时 `./install.sh` 让它装 NodeSource 22 |

---

## 11. 已知限制（诚实清单）

只列**现在仍然成立**的事；已经修好的（生产档启动、`/readyz`、SSE 频道两侧、worker 安装、会话 401、取消 Agent、
模型界面、数据策略接缝、契约文档落后等）不再占条目。每条都按 2026-10-25 的代码核过。

**代码 / 契约 / 文档**

1. **`APP_ENV=production` 能启动，但 `env://` 引用按设计解析失败**。bootstrap 在生产档只挂 memory 后端，凭证要走
   `db://`（界面录入 + 主密钥）或部署侧注入；这不是故障，是 FD-SEC-001 的边界。S0 的
   `install_development_backends()` 仍拒绝在生产装 env 后端，那是正确行为。
2. `SECRET_BACKEND` 仍是空配置：解析但不使用；实际只有 `env` + `memory`（外加有主密钥时的只读 `db`）三个后端。
   `vault://` / `secret://` / `kms://` 是保留语法，没有后端，填了只会在真正调用时失败。
3. **R6 的 fixture 导入代码在，但从没在真实 DHIS2 上跑过**（代码先 VALIDATE 再 COMMIT，PRODUCTION 一律拒绝；
   本机示例运行没有 fixture 包，该阶段是 `SKIPPED`）。这是"没试过"，不是"试过失败"。
   **Tracker / DataStore / App 上传的执行器仍未交付**，对应的提交路径只能走到 fixture 为止。
4. **`/healthz` 是常量且不被 nginx 反代**：外部打 `https://<IP>:1583/healthz` 会拿到前端 HTML 的 200。
   存活探针仍可用它（回环），"库通不通"要用 `GET /readyz`（503 = 库不通）。
5. **定向删除没有界面按钮**。授权写在变更申请上（`CP-CR-005`），S5 / S5M / API 补充需求都没有点名一个"删除"控件；
   删除是链条里唯一不可逆、实例不提供回滚的动作（`DX-EXE-038`、总体 SRS 15.6），演示实例又是共享的，所以不在
   没有规格时凭空造入口。走 API 的完整步骤见 §8.5 末。
6. **`POST /environments/{key}/delete-objects` 的载荷只可能是 S2 编译出来的包**（`METADATA_PACKAGE` 归 S2 创建，
   `FD-ART-002`）。候选清单为空 → 包里没有可删对象 → S4 拒绝，这是"本次运行没有授权退役任何东西"的正确答复。
   要让它非空，必须同时有：一条 `DEPRECATED` 的 Registry 条目 + 一份把它列入 `deletionCandidates` 并**已批准**的
   变更申请 + 一次带着该申请重跑的 BIND_COMPILE。S2 编译期再核引用（`BP-CMP-026`），S4 提交前当场再核一次
   （`DX-QRY-004`），两道都不能绕。
7. **Run 摘要不回那份包的产物 id**（`summary` 只有 `snapshot`、`execution` 两个产物槽位）。删除要的
   `deletionArtifactId` 只能从 `GET /stages/{BIND_COMPILE 的 id}` 的 `outputArtifactIds` 里取（四项：包 / manifest /
   diff / 校验报告），再 `GET /artifacts/{id}` 认类型；`GET /workflows/{id}` 的**紧凑**阶段视图不带
   `outputArtifactIds`，照摘要猜包 id 会拿到空列表。要做界面一键删除，S1 得先把包的 id 放进摘要（契约小改动，未做）。
8. **契约与文档现已对齐到 1.19.0 / 147 条路径**（172 个操作）：`S1/docs/openapi.json` 与 `S1/docs/CHANGELOG.md` 都到
   1.19.0；四份需求文档——`requirements/S1_Factory控制平面_SRS.md`、`requirements/S1_add_api_for_S5.md`、
   `requirements/S5_FactoryUI_SRS.md`、`requirements/S5M_移动优先前端_SRS.md`——以及两个控制台的
   `contract/openapi.json` 与签入的生成类型，均已对到同一版。两道守卫钉住这个数字：两端
   `src/test/contractVersion.test.ts`（1.19.0 / 147）与 S1 的
   `tests/api/test_openapi_contract.py::test_document_version_and_servers`。核对只需一条命令：
   `curl -ks https://127.0.0.1:1583/api/v1/openapi.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["info"]["version"], len(d["paths"]))'`。
   **实现有疑问时仍以 openapi 与 CHANGELOG 为准**，不要拿任何叙述性文档反推字段。
9. **`requiredApprovals` 与 `operator_role` 目前无法设置**（`DX-ENV-002`【调整】）：S4 0.1.0 保存时拒绝任何非空值（`DX_ENVIRONMENT_INVALID`，理由是 “take effect with M4 permissions”），尽管 S1 的 M4 认证已交付；读取回默认值，两端都没有编辑入口，DEPLOY_GATE 的所需审批人数因此回退为 1。
10. 阶段视图里「Registry / Diff」是**跳到元数据页的链接**，不是就地渲染（那块屏幕已经有过滤与版本选择，抄一份只会
    多出第二个真相来源）；**没有「预览」这一档**，因为 S1 没有页面预览端点——最接近的
    `/projects/{id}/frontend-package/files` 是交付物的文件清单。
11. **OIDC 只到"界面能走完、S1 形状正确"这一层**：`install.sh`/`start.sh` 的 `--auth` 仍只接受 `local|off`；开
    `oidc`/`both` 要手工改 env 并补 `FACTORY_OIDC_ISSUER` / `_CLIENT_ID` / `_CLIENT_SECRET_REF`。仓库内没有与真实
    IdP 跑过一次完整往返（`S1/README.md` 标了未验证），端到端测的是界面行为。
12. **主密钥轮换不会带着全库走**。换掉 `FACTORY_CREDENTIAL_MASTER_KEY` 后旧密文一律 `keyMismatch`，必须逐个重新
    录入；没有"旧密钥解密、新密钥重加密"的迁移（那需要同时持有两个密钥并落中间状态）。老机器升级会**自动补一行**
    主密钥（`start.sh`），补过之后绝不再改。
13. **一个引用只有一个当前值**：`PUT` 同名即覆盖，`rotatedAt` 记下换值时刻；没有多版本保留、没有到期策略、也没有
    「回滚到上一个值」。`FACTORY_CREDENTIAL_EXPIRY_WARNING_DAYS` 只对**环境档案里登记的到期日**说话，不针对 `db://` 的值。
14. **厂商目录是静态条目，不探测连通**（AI-CAT-001）；`adapter` 被运行时忽略（AI-LLM-007），本版本只有 OpenAI 兼容
    一个线协议。目录也不会加 Anthropic / Gemini 原生条目——列出来就是一个存进去必失败的地址。OIDC 的 client secret、
    SMTP 与 webhook 的密钥也**还没有**界面录入入口，仍只能 `env://`。
15. **模型自身的 `endpoint` / `apiKeySecretRef` 覆盖只能在 API 上设**：两端模型表单只编辑能力、单价、Agent 优先级与
    启停，并且只发送改动的字段；这两个字段不会从表单发出。用它们请走 `PATCH /governance/models/{ref}`，不要用
    `POST` 重新登记（整行覆盖会清掉没填的列）。
16. **需求条目的独立编写页只有移动端有**（`/projects/:id/requirements`）；桌面仍是阶段页「人工编写」的结构化表单 /
    Monaco JSON。两边写的是同一份蓝图草稿 `requirements` 段，但桌面没有"逐条卡片 + 确认/驳回"的这套操作。
17. **没有自定义角色**：只有 6 个内置角色，`GET /roles` 只读，不能新建角色或改写某个角色的动作集；要调整权限只能
    换角色授予（组织级/项目级），不能拼一个新角色。

**界面 / 构建**

18. **S5 的 `VITE_ENABLE_MOCK` 是构建期开关，运行期切不掉**：`dist/config.js` 只能改运行期配置，改不动已经烘进
    bundle 的值。发布构建别带这个变量；e2e 走 `npm run build:e2e`，产物在 `dist-e2e/`，`dist/` 只归生产构建。
    `install.sh` 落地静态产物时只读 `dist/`，并在拷之前检查入口 chunk 的测试标记（S5 查 `VITE_ENABLE_MOCK:"1"`，
    S5M 查 `dev/json-editor`），有就中止并说该跑哪条命令。核对生产包：`grep -c 'VITE_ENABLE_MOCK:"1"' dist/assets/index-*.js`
    应为 0；`dist-e2e/` 从不出现在部署路径上。
    另两个 e2e 坑仍在：端口 `4173` / `8787` 被上次中断的运行占着会被 `reuseExistingServer` 复用（跑前
    `ss -ltnp | grep -E '4173|8787'`，按 PID 停）；两端构建不要同时跑（S5 的 webServer 只有 120 秒预算）。
19. **S5M 的应用本体没有浏览器内替身层**（不像 S5 的 MSW），手工浏览任何页面都要一个活的 S1。它的 e2e 另有一套：
    `scripts/mock-s1.mjs` 在 `127.0.0.1:8787` 起一个替身 S1，只服务于测试，且端口固定（见上一条末段）。

---

## 12. 附录

### A. REST API 摘要

契约版本 **1.19.0**，共 **147 条路径 / 172 个操作**。免登录可取的有 `/healthz`（仅回环）、`/api/v1/openapi.json`、
`/api/v1/docs`。下表按 `openapi.json` 的 `tags` 分组；除 `/healthz` 外全部前缀 `/api/v1`。**路径数相加 = 147，
操作数相加 = 172**（同一条路径可以有多个方法；一条路径只归一个 tag）。

| 分组（tag） | 路径 | 操作 | 代表端点（省略公共前缀） |
|---|---:|---:|---|
| 蓝图与登记表 `blueprint` | 21 | 21 | `GET /projects/{id}/blueprint/{draft,entries,versions,diff,section-schemas}[/{section}]`；`GET /projects/{id}/registry[/managed-scope]`、`/uid-pool`、`/decisions`、`/package-files`、`/frontend-package/files[/{path}]`；`POST /projects/{id}/uid-pool/replenish`；`GET /workflows/{id}/{object-graph,nrr,validation-issues}`；`GET/PUT /deployment-runs/{id}/environment-bindings[/{businessKey}]` + `.../candidates`（`/projects/{id}/object-graph` 是 deprecated 别名） |
| 治理 `governance` | 18 | 24 | `GET/POST /governance/providers`、`PATCH /governance/providers/{name}`、`POST .../{name}/test-connection`；`GET/POST /governance/models`、`PATCH /governance/models/{ref}`；`GET /governance/{prompts,prompts/activations,agents,policy,usage,evaluations,regression-records,provider-catalogue}`；`POST /governance/{policy/relaxations,prompts/activations,evaluations,regression-records}`；`GET/PUT /governance/budget`；`GET /projects/{id}/effective-policy` |
| 目录与产物 `catalog` | 16 | 17 | `GET /allowed-actions`；`GET /artifacts/{id}[/content|/diff|/lineage]`、`POST /artifacts/{id}/{reproduce,purge}`、`GET /workflows/{id}/artifacts`；`GET /audit`、`GET /projects/{id}/audit`、`POST /audit/verify`、`GET /audit/commit-trace/{executionId}`；`GET /notifications`、`POST /notifications/{id}/read`；`GET/PUT /settings`；`GET /upload-limits` |
| 身份与权限 `identity` | 16 | 21 | `POST /auth/login|logout`、`GET /auth/session`、`GET /auth/oidc/request`、`POST /auth/oidc/exchange`；`GET /roles`；`GET/POST /users`、`PATCH /users/{id}`、`POST /users/{id}/password`、`PUT/DELETE /users/{id}/roles`；`GET/PUT/DELETE /projects/{id}/members`；`GET/POST/DELETE /projects/{id}/artifact-grants`；`GET/POST /projects/{id}/production-exemptions`、`DELETE /production-exemptions/{id}` |
| 工作流与阶段 `workflow` | 14 | 14 | `GET /projects/{id}/workflow`；`GET /workflows/{id}`、`/stages`、`/allowed-actions`；`POST /workflows/{id}/{pause,resume,cancel}`；`GET /stages/{id}`；`POST /stages/{id}/{run,rerun,submit-for-approval,resolve-manual-check,patches,questions}` |
| 环境（转发 S4）`environments` | 11 | 13 | `GET/POST /environments`；`GET /environments/expiring-credentials`；`GET/PATCH /environments/{key}`；`GET .../{protection,capability-profile,discovery-results}`；`POST .../{test-connection,probe-capabilities,disable,emergency-lock,delete-objects}`（`delete-objects` 必须带 `Idempotency-Key`，见 §8.5 末） |
| 审批与问题 `gates` | 11 | 11 | `GET /gates`、`GET /gates/{id}`、`GET /gates/{id}/explanation`、`POST /gates/{id}/{approve,reject}`；`GET /workflows/{id}/gates`；`GET /questions`、`GET /questions/{id}`、`GET /workflows/{id}/questions`；`POST /questions/answers`（**整份问卷一次提交，§6.8**）、`POST /questions/{id}/answer` |
| 部署运行 `runs` | 10 | 11 | `POST/GET /workflows/{id}/deployment-runs`；`GET /deployment-runs[/{id}[/summary]]`；`POST /deployment-runs/{id}/{commit,drift-check,cancel}`（`commit` 必须带 `Idempotency-Key`）；`GET /executions/{id}[/report]`、`POST /executions/{id}/reconcile` |
| 项目 `projects` | 9 | 15 | `GET/POST /projects`、`POST /projects/import`；`GET/PATCH/DELETE /projects/{id}`、`POST /projects/{id}/archive`、`GET /projects/{id}/export`、`POST /projects/{id}/upgrade-mode`；`GET/POST /projects/{id}/inputs`（multipart 上传在这里）；`GET/PUT /projects/{id}/{settings,target-environments}` |
| 变更请求 `change-requests` | 8 | 9 | `GET/POST /projects/{id}/change-requests`；`GET /change-requests/{id}`；`POST /change-requests/{id}/{impact,approve,reject,start,complete,cancel}` |
| 输入与 Agent 运行 `inputs` | 7 | 7 | `GET /projects/{id}/inputs/{aid}/column-mapping`、`GET .../llm-log[/{exchangeId}]`、`POST .../parse`；`POST /artifacts/{id}/explain`；`GET /workflows/{wf}/agent-runs/{runId}`、`POST .../agent-runs/{runId}/cancel` |
| 凭证 `secrets` | 2 | 3 | `PUT /secrets/{name}`（**upsert**，body `{"material": "<值>"}`，全套 API 里唯一携带凭证值的请求）、`GET /secrets`（只列名字/引用/时间）、`DELETE /secrets/{name}`（被引用时 409 `SECRET_IN_USE`）。**没有 `GET /secrets/{name}`**；`/secrets` 不进幂等回放表 |
| 阶段对话 `dialogue` | 1 | 3 | `GET/POST/DELETE /stages/{id}/chat`（读历史 / 问一轮 / 重开；历史仍可审计） |
| 装机事实 `setup` | 1 | 1 | `GET /setup`（S5IF-SET01；`providers`、`configuration`、`environments`、`intelligence`、`credentials`、`projects`） |
| 事件流 `events` | 1 | 1 | `GET /workflows/{id}/events`（SSE，`?after=` 或 `Last-Event-ID`） |
| 探针（无 tag） | 1 | 1 | `GET /healthz`（**不带 `/api/v1` 前缀**，仅回环；就绪探针是 `GET /readyz`，不进契约） |
| **合计** | **147** | **172** | |

请求头：`X-Operator`（`off` 模式下写操作必需）、`Idempotency-Key`、`X-Correlation-Id`、`traceparent`、`Accept-Language`（`zh-CN` / `en`）；`local|oidc|both` 模式下写请求另需 `X-CSRF-Token`。
`POST /governance/policy/relaxations` 的请求体（§8.6）：`{"change": {"level": "<密级>", "addClasses": ["<外发类别>"]}, "reason": "<必填>", "scope": "organization"}`。`change` 里的别名（`classification` / `allowedClasses` / `classes`）S1 会一并接受并归一，但界面上发的就是上面这一种；越上限或 `PHI_PII` → 403 `AI_POLICY_RELAXATION_DENIED`。

错误统一信封：`{errorCode, message, stage, artifactId, retryable, correlationId, details}`。

### B. 目录结构

```
DAMF/
├── install.sh  start.sh                # 装 / 启停
├── user-guide.md                       # 本手册
├── requirements/                       # 10 份 SRS（需求编号出处）
├── S0..S4/                             # Python 包，src/<package>/，pyproject.toml，tests/
│   └── S1/.venv/                       # 五个包以 -e --no-deps 装在这里
├── S5/ S5M/                            # 前端；dist/ 是构建产物
├── .dev-tls/                           # 开发期旧证书，未被部署引用
└── .deploy/                            # ★ 全部运行时状态
    ├── env                             #   0600，含口令；改后 --restart
    ├── nginx.conf                      #   生成物，勿手改
    ├── damf-api.service                #   生成物
    ├── damf-worker.service             #   生成物（周期任务 / Temporal worker）
    ├── tls/{cert.pem,key.pem,san.txt}  #   证书（key 0600）
    └── state/objects/<prj>/<TYPE>/<id> #   产物文件，必须备份
```

### C. 命令速查

```bash
./start.sh                      # 起（自动重渲染 + 健康检查 + 自检 + 起 worker）
./start.sh --status             # api / worker / nginx + 端口 + 站点
./start.sh --logs               # 两个单元的日志（LOGLINES=300 可加长）
UNIT=damf-worker ./start.sh --logs                 # 只看 worker
./start.sh --stop               # 停 worker + api（nginx 不停）
./start.sh --restart            # 改了 .deploy/env 之后
./start.sh --seed               # 灌演示项目
./start.sh --port 9443 --mobile-port 9444          # 换端口
./start.sh --providers real                        # 换后端能力（auto|fakes|real）
./install.sh --dry-run --ip <IP>                   # 看看会动哪些系统目录
./install.sh --render-only                         # 只重渲染 .deploy/ 下的配置
./install.sh --config-only                         # 重渲染并落 nginx + 两个 systemd 单元
S1/.venv/bin/factory-worker --once                 # 手工跑一次周期任务
curl -ks https://127.0.0.1:1583/api/v1/openapi.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["info"]["version"], len(d["paths"]))'   # 应为 1.19.0 147
node .deploy/verify-round.mjs                       # 部署后探针（22 项）：下载两端真实下发的分块并核对内容 + 登录后读 allowed-actions + 产物内容的 `?download=true` 真的带文件名
node S5M/scripts/verify-deployed-pages.mjs           # 部署后浏览器探针（42 项）：开发包页每屏给出可读状态、门控没放行就不发请求、产物页三个控件各是各的、顶部搜索命中即跳转
# 前端门禁（一次只跑一端的浏览器套件：并发会让上一份遗留的 vite preview 被复用）
cd S5  && npm run verify                        # 代码生成 → 类型 → lint → 单测+覆盖 → 构建 → 体积门禁
cd S5  && npm run test:e2e                      # 端到端 + 逐屏 axe：42 项 = 12 条主流程 + 30 屏 axe，1.5 分钟（自带 dist-e2e 构建，不动 dist/）
cd S5  && npx playwright test --project=a11y    # 只跑那 30 屏 axe（chromium project 只跑主流程，两个 project 不再跑同一份）
cd S5M && npm run verify                        # 同一条链，移动端
cd S5M && npm run build:harness && npx playwright test   # 移动端全量（产物在 dist-e2e/，不动 dist/）
cd S5M && npx playwright test --project=tap-target       # 18 屏 44 × 44 触控量测（UM-UX-001）
cd S5M && npx playwright test --project=a11y              # 18 屏 axe WCAG 2.1 AA（UM-NFR-05）
curl -sS http://127.0.0.1:8090/healthz | python3 -m json.tool   # 存活检查（不探库）
curl -sS http://127.0.0.1:8090/readyz                            # 就绪检查（真探库，不通回 503）
systemctl status damf-worker --no-pager                          # 周期任务进程
psql damf -tAc "select count(*) from cp.projects"               # 直接看库
```

### D. 更多资料

- **面向使用者**：`使用说明.md`（按任务组织的操作说明；界面里出现的名字都以它为准）
- 交互式 API：`https://<IP>:1583/api/v1/docs`
- 机器可读契约：`https://<IP>:1583/api/v1/openapi.json`
- 需求与集成约定：`requirements/00_总体架构与集成约定.md`（进程边界与 schema 归属）、`requirements/S1_add_api_for_S5.md`（前端契约）
- 各包自述：`S0/README.md` … `S5M/README.md`；S5M 的设计取舍见 `S5M/docs/adr/`
- **变更史**（每一次改了什么、为什么、门禁测到哪）：`S1/docs/CHANGELOG.md`（契约版本即此处版本）、
  `S5/docs/CHANGELOG.md`、`S5M/docs/CHANGELOG.md`、`S0/CHANGELOG.md`、`S2/CHANGELOG.md`
- 界面里的引导：装机后先开「开始使用」（§6.0）。它不是静态说明，而是把服务端事实逐条摆出来的清单
- 脚本内注释本身就是文档：`install.sh` 开头的"为什么必须 TLS"、Python 环境一节的"为什么 `--no-deps`"、`start.sh` 开头的配置回读规则。
