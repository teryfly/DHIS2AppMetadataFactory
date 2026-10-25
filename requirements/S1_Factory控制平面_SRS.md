# DHIS2 App Studio / Metadata Factory
# S1 Factory 控制平面（Factory Control Plane）子系统需求规格说明书

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 子系统 | S1 Factory 控制平面 |
| 文档版本 | V1.4.0（对照代码与 REST 契约 **1.19.0** 反向校准：§8.1 按契约重列为 **147 条路径 / 172 个操作**（146 条在 `/api/v1` 下，另含 `GET /healthz`）；新增 CP-PRJ-010 项目 `codePrefix`、CP-Q-008 整页问卷、CP-CR-008 定向删除执行入口、CP-API-017 提供方连通性探测、CP-UI-024 Run 摘要产物槽位、CP-UI-025 模型调用日志、CP-UI-026 装机诊断、CP-RT-006 `/readyz`、CP-RT-007 `damf-worker` 周期任务；改写 CP-API-009 上传内容扫描、CP-CR-005 删除候选校验、CP-GATE-012 多人审批与职责分离、CP-UI-006 模型差量 PATCH 与提供方探测、CP-UI-007 解析重试、CP-UI-017 第八个设置键 `stage_auto_continue`、CP-UI-018 手工 Patch 幂等键与 UID 池补池、CP-UI-022 草稿 `NOT_GENERATED(requiredStage=REQUIREMENTS)`、CP-IAM-001 认证 `off` 的恒允许行为。上一版 V1.3.0 的条款落账见 `S1/docs/CHANGELOG.md` 1.8.0～1.19.0。V1.2.2 曾新增 CP-IAM-005（口令长度由服务层执行、失败计数在认证事务外提交、`mustChangePassword` 为报告位、锁定与口令错回答逐字相同）。） |
| 上位文档 | 总体 SRS V1.4.0；《00 总体架构与集成约定》V1.3.0（下称“00 文档”） |
| 交付形式 | Python 3.12+ 包（建议包名 `factory_control`）+ 后端运行时组装（`factory-api`、`factory-worker` 镜像） |
| 依赖 | S0（全部基础能力）、S2、S3、S4 的公共 API；Temporal；PostgreSQL（schema `cp`） |
| 需求编号前缀 | CP- |
| 来源标记 | 【直引】【裁剪】【调整】【新增】，定义见 00 文档 0.3 |

---

## 1. 目的与范围

### 1.1 目的

S1 是 Factory 的控制平面：唯一对外的 REST / SSE 入口；持有全部流程状态与审批状态；通过调用 S2、S3、S4 的公共 API 编排各阶段；并负责把 S0～S4 组装为可部署的后端运行时。本文档是 S1 开发组的唯一需求输入。

### 1.2 范围内

1. 公共 REST API 与 SSE 实时推送；
2. 项目管理（目标环境登记、交付模式升级、归档、删除、导出与导入编排）；
3. 工作流、阶段与 Deployment Run 的编排与状态机（Temporal）；
4. Question 生命周期；
5. Human Gate 与审批；
6. Change Request 生命周期；
7. 通知；
8. 系统设置；
9. 定时任务调度（审计链接与校验、产物暂存与保留期清理、事件与模型调用日志清理、投递重试、Gate 催办、阶段自动推进）；
10. 后端运行时组装与部署描述（含 `factory-api` 与 `damf-worker` 两个进程、健康与就绪探针）；
11. 面向界面的转发、聚合与定型（第 3.12 节）；
12. 身份认证、角色与权限、项目成员隔离、受限产物读取授权与生产豁免（第 3.10 节）；
13. 凭证保管与定向删除执行（第 3.7、3.13 节）。

### 1.3 范围外

| 不负责 | 所属 |
| --- | --- |
| 产物存储、审计哈希链、幂等存储、事务、错误模型等基础能力 | S0 |
| Blueprint 内容、Patch 校验与应用、版本号、变更级别判定、影响分析、Registry、UID、编译、静态与跨产物校验、对象级 Diff、开发包完整性检查 | S2 |
| 输入解析、Agent 运行、LLM 调用、数据外发策略、脱敏、Prompt / Model Registry、成本预算 | S3 |
| 环境档案与保护设置、连接测试、Capability Profile、Discovery、执行计划、漂移检查、Snapshot、VALIDATE / COMMIT、部署后验证、环境提交锁、DHIS2 凭证的解析与使用 | S4（S1 只保管加密后的凭证记录，见 3.13） |
| 用户界面 | S5 / S5-M |

身份认证、角色与权限、生产环境职责分离**不属于范围外**，已在本期交付：本地账号与 OIDC、6 个内置角色、组织级与项目级两级授权、项目成员隔离、受限产物读取授权、生产豁免、个人通知（第 3.10 节）；`authMode=off` 时保持声明式身份与恒允许策略（CP-IAM-001/002），`FACTORY_M4_FEATURES_ENABLED` 可单独放开成员/授权/豁免写操作。

S1 禁止：生成业务内容；直接访问其他 schema 的表；访问 DHIS2 或模型提供方；解析 DHIS2 凭证或模型密钥。

---

## 2. 领域概念与定义

| 概念 | 定义 |
| --- | --- |
| 项目（Project） | 一个 DHIS2 应用交付的管理单元；S1 只保存管理与流程属性，交付模式等语义属于 Blueprint（D-15） |
| 项目代号前缀（codePrefix） | 项目创建时交给 S2 的受管对象 code 前缀（`{prefix}_{TYPE}_{businessKey}`）；创建后不可修改，S1 不落库 |
| 项目目标环境 | 项目登记的 S4 environmentKey、晋级顺序与是否为设计基准环境 |
| 工作流（Workflow） | 每个项目恰好一条的长期流程实例，包含设计主线、Deployment Run 与 HANDOFF |
| 阶段（Stage） | 工作流中的一个步骤实例；R1～R6 属于某个 Deployment Run |
| Deployment Run | 把一个 Blueprint 版本部署到一个目标环境的过程 |
| 操作者（Operator） | 未启用认证（`authMode=off`）时由请求头 `X-Operator` 声明的操作人名称（D-16）；启用认证后为会话主体，声明头不被采信（CP-IAM-003） |
| Question | 需要人工回答的结构化问题；`answerMode` 为 `SINGLE`（单选）或 `MULTIPLE`（多选） |
| Gate | 持久化的人工审批节点，绑定对象引用与内容哈希 |
| 绑定对象 | `{refType, refId, contentHash}`；refType 为 ARTIFACT、BLUEPRINT_DRAFT、BLUEPRINT_VERSION、ENVIRONMENT、EXECUTION |
| Change Request | 对已批准或已部署项目的受控变更请求 |
| 允许动作 | 当前状态下可执行的动作；UI 按钮可用性的唯一依据 |

---

## 3. 功能需求

### 3.1 公共 REST API（CP-API）

- **CP-API-001**【裁剪】（19.2）S1 必须以 `/api/v1` 暴露全部公共 API（清单见第 8 章），以 OpenAPI 3.1 描述，随 S1 发布供 S5 使用。【调整（契约 1.19.0）】当前契约含 **147 条路径 / 172 个操作**：`/api/v1` 下 146 条路径、171 个操作，另有不属任何控制台的 `GET /healthz`；`/readyz` 以 `include_in_schema=False` 注册，故不计入。
- **CP-API-002**【调整】（FR-SEC-005、D-16）只通过 HTTPS 提供服务。未启用认证（`authMode=off`）时，全部改变状态的请求必须携带非空的 `X-Operator` 请求头，缺失时返回 400 `OPERATOR_REQUIRED`；该值写入 S0 操作上下文，进入审计、审批与 Question 回答记录。启用认证后该头不再是必填项，身份只来自会话（CP-IAM-003）。【调整（契约 1.6.0）】该头的取值只允许 ISO-8859-1（Fetch 标准对 header 值的限制），因此姓名超出这一范围的浏览器**发不出该请求**，表现为"无法连接服务器"：客户端此时发送 `encodeURIComponent(name)`，S1 在入口按 UTF-8 解码还原；本就装得下的值原样采信，含 `%` 但不是完整转义序列的（如 `100%`）不做解码——解码一个客户端没编码的东西等于凭空造身份。字节透明的客户端（`curl -H`）直接发 UTF-8 时，传输层把它读成 Latin-1 乱码，S1 重读这些字节为 UTF-8（仅在能干净还原时，故真正的 Latin-1 姓名不会被改写）。两种写法落进审计的是同一个名字。
- **CP-API-003**【调整】（FR-IF-001）创建类与执行类请求必须支持 `Idempotency-Key`，使用 S0 幂等存储，作用域为“端点”，保留期默认 24 小时可配置；同键同体返回首次响应，同键异体返回 422 `IDEMPOTENCY_CONFLICT`；【新增】同键请求仍在处理中时返回 409 `REQUEST_IN_PROGRESS`（`retryable = true`，`details = {scope, idempotencyKey}`）。幂等键只从请求头读取（CP-API-014）。调用方给出该头时，服务端必须以调用方带来的键为准，不得改写成"对象 + 尝试次数"——后者会把"删掉一条再加回同一条"读成重放（CP-UI-018）。
- **CP-API-004**【直引】（FR-IF-002）执行类请求必须携带 `environmentKey`；非法状态下的请求返回 409 与原因。
- **CP-API-005**【直引】（FR-IF-004）列表类接口支持分页与排序；时间使用 ISO 8601 UTC。
- **CP-API-006**【新增】每个请求生成或沿用 `X-Correlation-Id` 与 `traceparent`，写入 S0 操作上下文。理由：总体 SRS NFR-OBS-001 的追溯字段需要在入口建立。
- **CP-API-007**【新增】归属 S2、S3、S4 的资源（Blueprint、Registry、环境、Governance 等）由 S1 调用对应公共 API 后返回，S1 不持久化其结果。理由：D-14。
- **CP-API-008**【调整】（19.2.1）S1 把 S0～S4 抛出的 `FactoryError` 映射为统一错误响应（第 8.3 节）；未知异常映射为 500 `INTERNAL_ERROR`，不泄露内部细节。

- **CP-API-009**【调整】（FR-REQ-004、NFR-CAP-003）上传入口必须执行文件类型校验与大小上限检查（默认 100 MB，可配置），并按结构做内容扫描；不通过时返回 422 `UPLOAD_REJECTED`，`details` 至少含 `filename`、`violation`、`mediaType`、`sizeBytes`、`maxBytes`、`allowedTypes[]`，另有 `reason`（`SIZE` 或 `TYPE`）。类型/大小规则码为 `SIZE_EXCEEDED`、`MEDIA_TYPE_MISSING`、`MEDIA_TYPE_NOT_ALLOWED`；内容扫描规则码为 `EXECUTABLE_CONTENT`（PE/ELF/Mach-O/脚本魔数）、`ARCHIVE_PATH_TRAVERSAL`、`ARCHIVE_SYMLINK`、`ARCHIVE_ENTRY_COUNT`（上限 2000）、`ARCHIVE_RATIO`（解压比上限 200）。该扫描是**确定性的结构性静态检查，不是病毒特征库**，不得在文档或界面上被描述为杀毒。
- **CP-API-010**【调整】（CP-API-001；S5IF-G01、G02、G05、G11）OpenAPI 契约要求：
  1. 每个操作（含转发端点）的请求体与 2xx 响应体都必须是具名组件，禁止自由对象；转发端点由 S1 定义请求模型并校验后再转给提供方，禁止把自由字典直接透传给 S2 / S3 / S4；
  2. 跨切请求头（`X-Operator`、`Idempotency-Key`、`Accept-Language`、`X-Correlation-Id`、`traceparent`）与响应头（`X-Correlation-Id`、幂等重放标识 `X-Idempotent-Replay`、产物内容的 `X-Data-Classification`）必须在 OpenAPI 中声明并注明适用条件；
  3. 契约按语义化版本管理，破坏性变更提升主版本并记入 CHANGELOG；每个操作带 `x-requirements` 扩展字段，列出其实现的需求编号（`CP-*`，以及来源于 S5 的 `S5IF-*`）；`servers` 留空，由部署侧注入；
  4. 新增或修改路由时同步导出并提交 OpenAPI 文档，作为 S5 生成客户端的唯一来源。
- **CP-API-011**【调整】（CP-API-005；00 文档 6.11；S5IF-G03、G04）线上格式：字段名一律 camelCase、枚举值一律 UPPER_SNAKE，转发提供方的模型时由 S1 投影，同一响应内禁止混用两种写法（系统设置的键除外，见 CP-UI-017）。列表响应统一为 `{items, limit, total?, offset?, nextCursor?}`：普通列表用 `offset`；审计、产物、事件类列表用 `nextCursor`；`total` 代价过高时省略，不得填 0；排序参数为 `sortBy` 与 `sortDir`；规模达 5,000 条的列表必须服务端分页与排序。
- **CP-API-012**【新增】（S5IF-G06）启动步骤的端点（阶段运行与重跑、输入解析、漂移重检、评测发起、项目导出与导入、COMMIT）统一返回 202 与受理信封 `{stageId?, stepRunId, accepted}`，不返回同步结果，不暴露执行引擎等部署细节；结果经 SSE 与查询端点观察。理由：客户端不得按部署形态分支处理响应。
- **CP-API-013**【新增】（S5IF-G07）错误码归一：与 S1 目录中某码语义相同的提供方码一律归一为 S1 的码（例如 S4 的提交时间窗错误归一为 `OUTSIDE_COMMIT_WINDOW`，S4 的“环境正在使用”归一为 `INVALID_STATE`）；有独立含义且已在 S0 错误码目录登记的提供方码允许透出（例如 `AI_PROMPT_ACTIVATION_BLOCKED`、`AI_POLICY_RELAXATION_DENIED`、`AI_FEATURE_NOT_ENABLED`）；其他提供方码不得透出。每个新增的 S1 错误码必须同时登记中英文消息。按码定义的 `details` 见第 8.3 节。
- **CP-API-014**【调整】（CP-API-003；S5IF-G08）`Idempotency-Key` 只从请求头读取；COMMIT 请求的请求体不设幂等键字段，缺少该请求头时返回 400。
- **CP-API-015**【直引】（FR-SEC-007、00 文档 6.9；S5IF-G09）环境类与治理类请求只接受 Secret 引用；任何响应、SSE 事件、错误 `details` 与审计前后摘要中禁止出现凭证的值、掩码、长度或前缀。
- **CP-API-016**【新增】（S1 §8.1 转发行）转发端点的资源概念与取值以提供方公共 API 为准，S1 只做路径设计、请求校验、camelCase 投影与错误归一；转发结果不在 S1 持久化（CP-API-007）；环境保护属性每次实时查询（CP-WF-027）；其他他方数据的缓存遵循 00 文档 4.2（进程内、≤ 60 秒、仅用于展示）。
- **CP-API-017**【新增】（S5IF-GOV20、AI-LLM-005）S1 必须提供 `POST /governance/providers/{name}/test-connection`，对已登记或未登记的提供方发起一次**不计费、不生成内容**的可达性探测（S3 对提供方 `GET /models`，5 秒预算），响应 `{ref, healthy, detail}`。未登记的提供方不是错误：返回 200 与 `healthy = false`，由 `detail` 说明。探测不得回显 API Key 的值、掩码、长度或任何由密钥派生的检查值。授权动作为 `governance.manage`；方法定为 POST，避免浏览器预取把一次外部调用挂在别人的页面加载上。理由：地址写错、密钥被拒与模型比请求超时更慢是三种不同故障，此前在界面上都只剩一句 `model endpoint unreachable`。**【实现现状 2026-10-25】** 真实 S3 对未登记提供方抛 `NOT_FOUND`，S1 当前未转换，故真实链路回 404、替身回 200；该偏差需在 S1 适配器或 S3 侧消除后才能宣称本条完全满足。

### 3.2 实时事件推送（CP-EVT）

- **CP-EVT-001**【调整】（C-02、19.2.3、FR-IF-005）S1 在自身的每次状态变更中，于同一事务写入事件流记录，按 `workflowId` 分配单调递增的 `sequence`；并经 `GET /api/v1/workflows/{workflowId}/events`（SSE）推送。
- **CP-EVT-002**【新增】S1 实现 S0 的 `ProgressReporter`，把 S3 / S4 长任务回报的进度写为 `agent.progress` 或 `execution.progress` 事件流记录。理由：子系统之间不使用消息通讯，Agent 与执行的实时状态由进度回调提供。
- **CP-EVT-003**【直引】（FR-IF-005）客户端重连时携带最后收到的序号（`Last-Event-ID` 或 `?after=`），S1 补发缺失事件。
- **CP-EVT-004**【新增】事件流记录保留期默认 7 天可配置；请求的序号早于保留期时，S1 发送 `stream.reset`，客户端必须通过 REST 全量刷新。理由：补发需要明确边界。
- **CP-EVT-005**【直引】（NFR-PERF-005）从状态变更提交到推送给客户端 P95 < 2 秒；多个 `factory-api` 实例下均须满足。多实例扇出使用 PostgreSQL LISTEN/NOTIFY：事件流记录提交后发出通知，各实例收到通知后读取新记录并推送。NOTIFY 与 LISTEN 两侧必须读同一个 `FACTORY_SSE_CHANNEL`（默认 `factory_cp_events`），频道名必须是 PostgreSQL 标识符（字母、数字、下划线，且不以数字开头），非法值在启动时被 `validate_runtime()` 拒绝；不得一侧写死常量、另一侧读配置——那会让实时推送静默停止，只剩轮询兜底。`FACTORY_SSE_POLL_SECONDS` 仅是兜底轮询间隔。
- **CP-EVT-006**【直引】（C-02）事件只用于通知，状态以 REST 查询为准；事件 payload 不得包含 RESTRICTED 及以上分级的内容。
- **CP-EVT-007**【调整】（19.2.3）事件类型在总体 SRS 列表基础上增加：`agent.started` / `progress` / `completed`、`execution.started` / `progress` / `completed` / `failed`、`deployment_run.created` / `completed` / `failed` / `cancelled` / `outdated` / `drift_checked`、`stage.started` / `waiting_input` / `waiting_approval` / `passed` / `failed` / `blocked` / `invalidated` / `manual_check` / `rerun`、`question.created` / `answered`、`gate.created` / `approved` / `rejected` / `invalidated`、`change_request.updated`、`artifact.created` / `superseded`、`input.parse`、`validation.completed`、`workflow.completed` / `failed` / `cancelled`、`stream.reset`。新增事件类型必须同时写进本列表与契约，避免"推了但没名字"。

### 3.3 项目管理（CP-PRJ）

- **CP-PRJ-001**【调整】（19.2、2.5、2.6、D-15）创建项目时提交：名称、描述、负责人名称、交付模式、项目类型、目标版本约束、默认语言与翻译语言、目标环境列表（至少一个，指定设计基准环境与晋级顺序）。S1 先在事务外调用 S2 准备初始 Blueprint（暂存内容），再在一个工作单元内保存项目管理属性、创建工作流、调用 S2 登记初始 Blueprint；任一失败则整体回滚。同一工作单元内 S1 还必须把创建项目的账号登记为该项目的首位成员（无会话的 MVP 模式下跳过）：项目按成员关系隔离（FR-SEC-004），缺了这一步，创建者对自己刚建的项目也没有任何项目内动作权限，`project.create` 就成了一个建完用不了的动作。
- **CP-PRJ-002**【调整】（ASM-01）登记目标环境时，S1 调用 S4 确认 environmentKey 存在；项目至少登记一个非 PRODUCTION 类环境。
- **CP-PRJ-003**【新增】设计基准环境有且只有一个；DISCOVERY 阶段 PASSED 后更换设计基准环境，必须使 DISCOVERY 及其后全部设计阶段 INVALIDATED。理由：映射依据的发现结果随之改变。
- **CP-PRJ-004**【新增】移除目标环境时，该环境不得有进行中的 Run；历史 Run 保留。理由：执行记录不可丢失。
- **CP-PRJ-005**【调整】（2.5）交付模式只允许由 A 升级为 B：S1 调用 S2 准备（事务外）并提交（事务内）交付模式变更的新版本，成功后 PAGE_DESIGN 由 SKIPPED 变为 READY，HANDOFF 由 SKIPPED 变为 LOCKED。不支持由 B 降级为 A【新增】（理由：降级会使已批准的页面与开发包失去依据）。
- **CP-PRJ-006**【直引】（7.4）项目可归档，归档后只读。【新增】归档前工作流不得有 RUNNING 阶段；归档时全部 WAITING 的 Gate 置为 INVALIDATED，未完成的工作流置为 CANCELLED。归档项目的全部写请求返回 409 `PROJECT_ARCHIVED`。
- **CP-PRJ-007**【直引】（FR-DATA-006）项目删除只允许对已归档项目执行，删除前必须已有 PROJECT_EXPORT 产物（否则 409 `INVALID_STATE`，`details.requires = "PROJECT_EXPORT"`），删除操作写审计。【调整】删除以 202 受理并返回 `DeleteAcceptedView`（`projectId`、`deleted`、`already`、`deletedAt?`、`requires[]?`、`status?`）；S1 依次调用 S2、S3、S4 的项目数据清理 API，再删除本方数据，最后调用 S0 清理该项目产物内容（审计不删除）。重复删除返回 `already = true`，不重复清理。
- **CP-PRJ-008**【调整】（FR-BP-009、AC-DATA-01）项目导出由 S1 编排：调用 S2 导出 Blueprint 全部版本与 Registry；收集全部被 Blueprint 版本与构建记录引用的产物（含 CAPABILITY_PROFILE 与 DISCOVERY_RESULT，以保证重新编译哈希一致）；连同项目管理属性清单，调用 S0 打包为 PROJECT_EXPORT。【新增】导出不包含凭证、环境档案、审计记录与 PHI_PII 内容（只保留其元数据与哈希）。`GET /projects/{id}/export` 以 202 受理，受理信封的 `stepRunId` 就是 PROJECT_EXPORT 产物 ID，客户端经 `GET /artifacts/{id}` 与 `?download=true` 观察和下载。
- **CP-PRJ-009**【调整】（FR-BP-009）项目导入：调用 S0 解包并校验哈希 → 创建新项目 → 调用 S2 导入 Blueprint 与 Registry。【新增】导入后的项目必须在本部署重新登记目标环境；工作流从头开始，设计阶段以导入的最新版本为草稿，需重新通过各设计 Gate。理由：审批记录属于导出方部署，不能跨部署继承。
- **CP-PRJ-010**【新增】（BP-REG-009、FR-TST-002）`POST /projects` 请求体接受可选的 `codePrefix`：非空时必须匹配 `^[A-Za-z][A-Za-z0-9_]{0,15}$`，否则 422 `VALIDATION_ERROR`（`details.field = "codePrefix"`，并回显 `pattern`）；空串表示沿用 S2 的默认前缀 `GEN`。S1 把它随初始 Blueprint 信息交给 S2，由 S2 派生受管对象的 `code = {prefix}_{TYPE}_{businessKey}` 并替换作者自写的 code。该字段**只在创建时生效**：`PATCH /projects/{id}` 的允许字段不含它，提交即 409 `INVALID_STATE`；`ProjectView` 不回显它。理由：共享实例上按运行前后缀识别并清理对象（FR-TST-002）需要这个前缀，而补池、UID 与人工 Patch 都无法改变已定前缀。

### 3.4 工作流与阶段编排（CP-WF）

#### 3.4.1 结构

- **CP-WF-001**【调整】（7.1、D-01）每个项目恰好一条工作流，于项目创建时创建。阶段结构见 00 文档第 3 章。
- **CP-WF-002**【调整】（7.3）SKIPPED 的判定：模式 A 的 PAGE_DESIGN 与 HANDOFF；Blueprint 无 Fixture 规格时的 FIXTURE_IMPORT；PRODUCTION 类环境 Run 的 FIXTURE_IMPORT。交付模式与 Fixture 规格在需要时向 S2 查询，S1 不保存副本。

#### 3.4.2 阶段状态机

- **CP-WF-003**【直引】（7.3）阶段状态：LOCKED、READY、RUNNING、WAITING_INPUT、WAITING_APPROVAL、PASSED、FAILED、BLOCKED、INVALIDATED、SKIPPED、MANUAL_CHECK（总体 SRS 7.3、NFR-REL-003）。
- **CP-WF-004**【调整】（7.3）状态迁移只允许附录 A 所列路径；其他迁移请求返回 409 `INVALID_STATE`。
- **CP-WF-005**【直引】（FR-WF-001）状态迁移只能由 S1 执行；迁移前校验前置状态、必需产物、有效审批；迁移、事件流记录与审计在同一工作单元提交。
- **CP-WF-006**【直引】（FR-WF-002、NFR-CON-001）同一工作流同一时刻只能有一个阶段处于 RUNNING（包括各 Run 的步骤）。
- **CP-WF-007**【直引】（FR-WF-006、NFR-REL-001、NFR-REL-002）状态持久化；长时间运行与等待人工的流程由 Temporal 承载；进程崩溃后从最后一个持久化步骤恢复；禁止从前端状态推断系统状态。
- **CP-WF-008**【调整】（NFR-REL-003）Temporal 活动调用 S3 / S4 长任务时必须传入幂等键；活动重试时相同幂等键使被调用方继续而非重做。对 COMMIT 与 Fixture 导入，恢复时必须先调用 S4 按幂等键查询执行状态；结果无法确认时阶段进入 MANUAL_CHECK。
- **CP-WF-009**【新增】Temporal 活动只对 `retryable = true` 的错误自动重试（指数退避、次数上限可配置）；其余错误直接使阶段 FAILED 或 BLOCKED。理由：总体 SRS 19.2.2 在库调用下的执行位置。

#### 3.4.3 运行、重跑、暂停与取消

- **CP-WF-010**【直引】（FR-WF-004）用户可以暂停、恢复、取消工作流，或重跑任意已完成阶段；重跑必须产生新的产物版本。
- **CP-WF-011**【调整】（FR-WF-004）暂停：不再启动新阶段，RUNNING 阶段的调用继续至完成并记录结果，但不自动推进。取消：通过 S0 `CancellationToken` 通知正在运行的 S3 / S4 长任务；正在执行的 COMMIT 不可取消，完成后工作流再进入 CANCELLED。
- **CP-WF-012**【新增】重跑某阶段时，该阶段的 OPEN Question 置为 SUPERSEDED，WAITING 的 Gate 置为 INVALIDATED，下游按 CP-WF-015 处理。理由：避免旧问题与新运行混杂。
- **CP-WF-013**【新增】阶段 `attempt` 随每次运行递增；每次运行记录输入的 Blueprint 版本或草稿（ID + 哈希）与输出产物 ID。理由：可追溯。

#### 3.4.4 失效传播与回退

- **CP-WF-014**【调整】（D-04、3.3）Blueprint 正式版本只由 S1 的调用产生（草稿提升、交付模式升级、项目导入）；S1 从 S2 的返回值获得新版本号、变更级别、受影响阶段集合，并在同一工作单元内执行失效传播。
- **CP-WF-015**【调整】（FR-WF-003、5.2）失效传播规则：
  1. 受影响且非当前阶段的设计阶段置为 INVALIDATED，其 Gate 失效；
  2. 进行中且未进入 COMMIT 的 Run 置为 CANCELLED，其 Gate 失效；正在 COMMIT 的 Run 完成后标记 OUTDATED；
  3. 已完成的 Run 标记 OUTDATED，历史记录不变；
  4. 已 PASSED 的 HANDOFF 置为 INVALIDATED；
  5. 变更级别为 Major 时，MAPPING 及其后阶段全部失效（ARCHITECTURE_GATE 需重新通过）。
- **CP-WF-016**【直引】（FR-WF-005）校验失败或 DHIS2 VALIDATE 失败时，系统必须指出问题来源阶段（取自 S2 / S4 报告中每个问题的 `sourceStage`）；用户在来源阶段修正并产生新版本后，从该阶段重新推进。
- **CP-WF-017**【调整】（FR-MAP-004）ARCHITECTURE_GATE 通过后，S1 必须拒绝 MAPPING 之后阶段对映射与 Registry 的人工 Patch 请求；此类修改只能通过 Change Request。（S2 同时做内容侧校验。）

#### 3.4.5 Deployment Run

- **CP-WF-018**【新增】用户可为某目标环境创建 Run，前提：设计主线除 HANDOFF 外全部 PASSED 或 SKIPPED；该环境已登记在本项目且 S4 认识它；该环境最近一次连接测试通过（AC-CAP-02，无记录或失败均拒绝，`details.reason` 指明 `NO_CONNECTION_TEST`）；该环境无进行中的 Run；项目已有当前 Blueprint 版本；Run 绑定该版本。理由：D-01、FR-CAP-001。
- **CP-WF-019**【新增】Run 状态：CREATED、IN_PROGRESS、WAITING_APPROVAL、COMMITTED、VERIFIED、FAILED、CANCELLED、MANUAL_CHECK，另有 OUTDATED 标记。理由：D-01。
- **CP-WF-020**【调整】（FR-ENV-006）PRODUCTION 类环境的 Run，在 DEPLOY_GATE 审批与 COMMIT 前必须满足：同一 Blueprint 版本已有非生产环境 Run 处于 VERIFIED。例外是由管理员登记的生产豁免：`waiver = NON_PRODUCTION_VERIFIED`，可限定环境键、带理由与到期时间、可撤销；豁免生效时写入 Gate 评审上下文，供审批人看见"这一步被绕过了"。豁免的写端点属 M4，仅在 `authMode != off` 或 `FACTORY_M4_FEATURES_ENABLED=true` 时可用（CP-UI-023）。
- **CP-WF-021**【调整】（FR-EXE-002）Run 执行顺序固定为：元数据 COMMIT →（M5：DataStore）→ POST_VERIFY → FIXTURE_IMPORT →（M5：应用上传）；前一步失败则后续不执行，Run 报告说明哪些步骤已生效。
- **CP-WF-022**【调整】（FR-CAP-003、FR-CAP-005）每个 Run 步骤开始前，S1 调用 S4 检查目标环境 Capability Profile 是否过期或版本已变化；若是，Run 回到 R1 重新执行。

#### 3.4.6 工作流状态与项目里程碑

- **CP-WF-023**【调整】（7.4）工作流状态推导（按优先级）：CANCELLED（已取消）→ PAUSED（已暂停）→ COMPLETED（模式 A：至少一个 Run VERIFIED 且无进行中 Run；模式 B：另需 HANDOFF PASSED）→ FAILED（存在 FAILED 或 MANUAL_CHECK）→ BLOCKED（存在 BLOCKED）→ WAITING_USER（存在 WAITING_INPUT 或 WAITING_APPROVAL）→ ACTIVE。
- **CP-WF-024**【调整】（7.4）项目里程碑按当前 Blueprint 版本推导：DRAFT → DESIGN_APPROVED（ARCHITECTURE_GATE 通过，模式 B 另需 DESIGN_GATE）→ PACKAGE_APPROVED（任一 Run 的 PACKAGE_GATE 通过）→ DHIS2_VALIDATED → DEPLOYED → VERIFIED → HANDED_OFF（模式 B）。另提供按环境的 Run 状态视图。

#### 3.4.7 允许动作

- **CP-WF-025**【直引】（FR-IF-003、FR-UI-009）`allowed-actions` 返回当前状态下可执行的动作列表，是 UI 按钮可用性的唯一依据。
- **CP-WF-026**【新增】`allowed-actions` 同时返回不可用动作及原因码（如 `GATE_NOT_APPROVED`、`ENVIRONMENT_LOCKED`、`OUTSIDE_COMMIT_WINDOW`、`PRIOR_VERIFICATION_MISSING`）。理由：总体 SRS P-06 “Explain Before Execute”。
- **CP-WF-027**【新增】涉及环境保护属性的动作，计算时实时调用 S4 查询，不使用缓存。理由：D-14。
- **CP-WF-031**【新增】（S5IF-BP05）工作流视图与设计阶段视图必须返回当前活动草稿 ID（由 S1 按项目与阶段向 S2 查询活动草稿得到，不在 S1 另存副本）。理由：界面不得推断业务标识（S5 UI-BR-02）。

#### 3.4.8 阶段编排明细

- **CP-WF-028**【新增】S1 对每个阶段的调用如下（S2 / S3 / S4 的具体函数由其 SRS 定义）。理由：把总体 SRS 7.2 转换为跨子系统的本地职责。

| 阶段 | S1 的调用 | 完成依据（调用返回值） |
| --- | --- | --- |
| REQUIREMENTS | 每个新输入：S3 解析；用户运行：S3 启动 Requirements Agent | Agent 结果 COMPLETED → WAITING_APPROVAL |
| DOMAIN | S3 启动 Domain Agent | 同上 |
| DISCOVERY | S4 对设计基准环境探测能力并发现（Greenfield 只读取版本、能力与 default 对象） | 发现成功且无阻塞问题 → PASSED |
| MAPPING | S3 启动 Mapping Agent（模式 A 另含 Fixture Agent） | Agent 结果 COMPLETED |
| PAGE_DESIGN | 按页面调用 S3 的 API Agent 与 UI Agent，另调用 Fixture Agent | 全部页面 COMPLETED |
| BASELINE_COMPILE | S2 基线编译与环境无关校验 | 无 BLOCKING → PASSED |
| R1 BIND_COMPILE | S4 探测与发现目标环境 → S2 环境绑定编译 | 编译成功 |
| R2 STATIC_VALIDATION | S2 静态与跨产物校验 | 无 BLOCKING → WAITING_APPROVAL |
| R3 DHIS2_VALIDATE | S4 执行计划、漂移检查、Snapshot、VALIDATE | 无错误 → 创建 DEPLOY_GATE |
| R4 COMMIT | DEPLOY_GATE 批准且用户请求后：S4 COMMIT | 提交成功 |
| R5 POST_VERIFY | S4 部署后验证 | 一致 → PASSED |
| R6 FIXTURE_IMPORT | S4 Fixture VALIDATE 与 COMMIT | 成功 |
| HANDOFF | S2 生成开发包与完整性检查 | 全部通过 → WAITING_APPROVAL |

- **CP-WF-029**【调整】（FR-AG-011）每个 Agent 支持的阶段都允许不运行 Agent：用户通过 S1 提交人工 Patch（S1 调用 S2 加入草稿），并直接“提交审批”，阶段由 READY 进入 WAITING_APPROVAL。提交前 S1 先从设计基准环境补充 UID 池，提交的幂等键语义见 CP-UI-018；`Accept-Language` 取当前界面语言，经 `AgentRunRequest.language` 送到 S3（续跑的轮次同样携带）。
- **CP-WF-030**【新增】S3 返回策略拒绝或预算超限时，阶段进入 BLOCKED，展示被拒绝的产物或预算信息；人工编写路径不受影响。理由：总体 SRS AC-AG-01、NFR-COST-002 的编排侧行为。

### 3.5 Question 管理（CP-Q）

- **CP-Q-001**【调整】（11.3、FR-AG-004）S1 是 Question 的唯一所有者。来源：S3 Agent 运行结果中的 Question 列表、S2 在 Patch 基准冲突时返回的合并问题、用户在阶段内提出的问题。
- **CP-Q-002**【裁剪】（11.3、FR-UI-003）字段：ID、项目、工作流、阶段、来源（类型、agentRunId、来源内本地 ID）、文本、严重程度（HIGH / MEDIUM / LOW）、影响对象（businessKey）、**回答模式 `answerMode`（`SINGLE` / `MULTIPLE`，缺省 `SINGLE`）**、建议列表（ID、文本、理由、是否改变 UID、需重新生成的产物类型、`recommended` 推荐标记、`columnMapping?`）、是否阻塞运行、状态、回答、回答人、回答时间。回答体为 `{suggestionId?, suggestionIds[], suggestionText?, suggestionTexts[], customText?, text?, answeredAt?}`：多选答案保留全部建议 ID 与文本，不被压成一条字符串。`SINGLE` 问题一次只能选一个建议，且不能同时给自定义文本；违反返回 409 `INVALID_STATE`（`details.answerMode`、`suggestionIds`）。`MULTIPLE` 问题接受多个建议，回答文本以 `"; "` 连接。
- **CP-Q-003**【新增】状态：OPEN、ANSWERED、WITHDRAWN、SUPERSEDED；ANSWERED 后不可再迁移。理由：总体 SRS 未定义生命周期。
- **CP-Q-004**【新增】以（agentRunId、本地 ID）去重，活动重试不会产生重复问题；S3 从同一次运行的第二轮起必须使用带 attempt 的本地 ID（如 `Q2-1`），否则新问题会被去重静默丢掉。
- **CP-Q-005**【调整】（FR-AG-004）某次 Agent 运行的阻塞 Question 全部回答后，S1 调用 S3 以全部回答恢复该运行；回答可以选择建议或填写自定义文本。单题端点与整页问卷端点并存：问卷（CP-Q-008）先在一个事务内记录全部答案，再**最多 resume 一次**，避免第二题的回答落在运行已经继续之后；对非阻塞问题的迟到回答不得恢复已经前进的运行。
- **CP-Q-006**【直引】（FR-REQ-007、FR-MAP-003）存在 HIGH 级 OPEN Question 时，所在设计阶段的 Gate 禁止批准。
- **CP-Q-007**【新增】回答写审计，回答后不可修改；如需更改，须通过新 Question 或新 Patch 表达。理由：可追溯。
- **CP-Q-008**【新增】（S5IF-FLD08、契约 1.19.0）S1 必须提供 `POST /questions/answers`，一次提交同一阶段的整份问卷：请求体 `{answers: [{questionId, suggestionIds[], customText?}]}`（1～200 项，同一 questionId 最多一次）；全部答案属于同一阶段、同一工作流与同一项目，否则 409 `INVALID_STATE`（`details.stageIds` 等指明冲突）。所有答案先提交，再最多恢复一次运行；响应为 `QuestionnaireAnswerView = {answers[{questionId,status}], resumed, runId?, status?, questions[]}`，其中 `questions[]` 是恢复后该阶段**仍然 OPEN** 的问题，供界面立刻渲染下一轮问卷。授权逐题按 `question.answer` 判定；端点支持 `Idempotency-Key`，重试不得写出第二份答案。`Accept-Language` 随运行与回答送到 S3（`AgentRunRequest.language`），使问题与续跑正文使用当前界面语言。

### 3.6 Gate 与审批（CP-GATE）

#### 3.6.1 定义与创建

- **CP-GATE-001**【调整】（16.1）Gate 类型、位置与绑定对象：

| Gate | 位置 | 绑定对象 |
| --- | --- | --- |
| REQUIREMENT_GATE | REQUIREMENTS 结束 | Blueprint 工作草稿哈希、该阶段 Question |
| DOMAIN_GATE | DOMAIN 结束 | 工作草稿哈希 |
| ARCHITECTURE_GATE | MAPPING 结束 | 工作草稿哈希、DISCOVERY_RESULT |
| DESIGN_GATE | PAGE_DESIGN 结束 | 工作草稿哈希 |
| PACKAGE_GATE | R2 结束 | METADATA_PACKAGE、FIXTURE_PACKAGE、DIFF_REPORT、VALIDATION_REPORT、环境 |
| DEPLOY_GATE | R4 之前 | 载荷哈希、环境、SNAPSHOT、DHIS2_IMPORT_REPORT（VALIDATE）、EXECUTION_PLAN |
| HANDOFF_GATE | HANDOFF 结束 | FRONTEND_PACKAGE、HANDOFF_CHECK_REPORT |

- **CP-GATE-002**【调整】（16 章引言）每个 Gate 在所属阶段每次运行结束时创建一次，审批对象是整个阶段的产物集合。
- **CP-GATE-003**【直引】（16.2）Gate 数据至少包含：ID、类型、工作流、Run（如适用）、环境键、Blueprint 版本或草稿 ID、绑定对象列表、WARNING 列表、风险摘要引用、所需审批数、审批列表、状态、创建时间。状态：WAITING、APPROVED、REJECTED、INVALIDATED。
- **CP-GATE-004**【新增】WARNING 列表由 S1 在创建 Gate 时从绑定的校验报告产物中读取（问题 ID、规则 ID、对象、说明）；风险摘要取自 S2 的变更摘要与 S4 的执行计划产物。理由：Gate 内容以冻结的产物为依据，不复制他方数据。

#### 3.6.2 审批规则

- **CP-GATE-005**【直引】（FR-GATE-001）审批绑定内容哈希；任何绑定对象变化，Gate 自动 INVALIDATED。【调整】变化的判定时机：用户经 S1 提交人工 Patch 时（草稿哈希变化）；S1 产生新 Blueprint 版本时；S1 创建同类新产物时；审批与 COMMIT 请求时重新读取绑定对象的当前哈希并比较；DEPLOY_GATE 审批与 COMMIT 请求时调用 S4 漂移检查（总体 SRS FR-VAL-004）。
- **CP-GATE-006**【直引】（FR-GATE-002）审批状态只能由后端根据审批请求写入，前端点击本身不构成审批。
- **CP-GATE-007**【调整】（FR-GATE-003、D-16）审批记录操作者名称；启用认证时另记 `approverUserId` 与批准当时持有的角色（`approverRoles`，不事后重算）。`authMode=off` 时不校验审批角色；`local|oidc|both` 下按 CP-GATE-012 校验。拒绝时必须填写理由。
- **CP-GATE-008**【调整】（FR-GATE-004）批准时必须对全部 WARNING 逐条标记“接受”或“需修正”；存在“需修正”的标记时只能拒绝。
- **CP-GATE-009**【新增】审批请求必须携带审批人所见的绑定对象哈希列表；与当前值不一致时返回 409 `APPROVAL_INVALIDATED`。理由：防止基于过期页面批准。
- **CP-GATE-010**【直引】（FR-GATE-007）拒绝时阶段回到 READY，理由作为待处理反馈显示。【调整】重新提交审批时，绑定对象哈希集合必须与被拒绝的 Gate 不同。
- **CP-GATE-011**【直引】（FR-GATE-006）Gate 等待期间状态持久化；重启不改变 Gate 状态。
- **CP-GATE-012**【调整】（FR-GATE-005、D-16）DEPLOY_GATE 的所需审批数取自 S4 保护属性的实时值（`requiredApprovals`，读不到时回退 1），创建 Gate 时写入 `gate.requiredApprovals` 并由 Gate 行保留实际用过的数字；不再固定为 1。**当前实现边界**：S4 0.1.0 保存时拒绝任何非空的 `requiredApprovals`/`operatorRole`（`DX_ENVIRONMENT_INVALID`，理由写作 “take effect with M4 permissions”），因此这两个值恒为默认、审批人数实际回退 1；M4 已交付的是身份、角色与职责分离，本配置项尚未在 S4 接线。以下条件始终执行：PRODUCTION 类环境需满足 CP-WF-020；需在 S4 配置的允许提交时间窗内；S4 紧急锁定未开启；审批人须确认“实例数据库备份已由运维确认”（总体 SRS FR-SNP-003）。**身份类规则仅在启用认证时生效**（`authMode != off`）：审批人须持有与环境类别精确匹配的动作（如 `gate.approve.DEPLOY_GATE.PRODUCTION`，更窄的授权不被更粗的请求覆盖）；保护属性给出 `operatorRole` 时审批人须持有该角色（当前该值恒空，规则不触发）；**同一人不得重复审批**（审批数是人的数量）；执行本 Run 的人（`validateOperator` / `commitOperator`）不得审批其 DEPLOY_GATE（AC-GATE-04）。`authMode=off` 时没有账号可比对，重复同名审批与执行者自批**无法判定**，生产环境的多人与职责分离因此只在启用认证的部署上成立；这是当前实现的边界，不得在文档或界面上说成"off 模式也已强制"。
- **CP-GATE-013**【调整】（FR-EXE-005、FR-EXE-007）非默认导入参数（如非原子模式）与维护动作，必须在 DEPLOY_GATE 审批中作为独立确认项逐项确认。

#### 3.6.3 批准后动作

- **CP-GATE-013a**【新增】设计阶段进入 WAITING_APPROVAL 前，S1 必须在事务外调用 S2 冻结草稿（传入项目全部目标环境键）；冻结存在 BLOCKING 问题时阶段进入 BLOCKED；设计 Gate 绑定冻结得到的草稿哈希。理由：S2 以冻结 + 提升两阶段完成版本生成（BP-DRF-008/009）。
- **CP-GATE-014**【调整】（FR-BP-007、D-04）设计 Gate 批准时，S1 在同一工作单元内：写审批、调用 S2 以冻结哈希把草稿提升为正式版本、置阶段 PASSED、执行失效传播、写事件流与审计；任一失败则整体回滚，Gate 保持 WAITING。
- **CP-GATE-015**【新增】PACKAGE_GATE 批准后 Run 自动推进至 R3；HANDOFF_GATE 批准后项目里程碑变为 HANDED_OFF；DEPLOY_GATE 批准后 COMMIT 动作变为可用，但不自动执行。理由：提交是高风险动作，保留人工触发。
- **CP-GATE-016**【调整】（FR-GATE-008）Gate 创建时产生 GATE_PENDING 通知；等待超过项目配置时长（默认 24 小时）后提醒，之后每隔同样时长提醒，最多 3 次（Temporal 定时器）。

#### 3.6.4 COMMIT 前复核

- **CP-GATE-017**【直引】（FR-EXE-006、FR-EXE-004）COMMIT 请求必须在后端重新执行完整 Gate 检查：DEPLOY_GATE 为 APPROVED 且未失效；VALIDATE 所用载荷哈希、Gate 绑定载荷哈希、本次提交载荷哈希三者一致；CP-GATE-012 条件在请求时刻成立；S4 漂移检查通过。任一不满足返回 409 及对应错误码，且不调用 S4 COMMIT（总体 SRS AC-GATE-02）。
- **CP-GATE-018**【直引】（19.2.2、AC-EXE-05）COMMIT 请求在网络失败后重试时，S1 先以幂等键调用 S4 查询执行状态；只有确认未生效才允许重新调用 COMMIT。

#### 3.6.5 可解释性

- **CP-GATE-019**【调整】（16.4）Gate 说明接口聚合回答：发现了什么；准备做什么；为什么（决策 ID 与理由，调用 S2 读取）；还有什么不确定（未处理 WARNING、低置信度决策）；用户可以改变什么；批准后会发生什么、影响哪个环境、产出哪些产物。说明以绑定对象为依据，不另存副本。

### 3.7 Change Request（CP-CR）

- **CP-CR-001**【直引】（7.6、FR-WF-007）已批准或已部署项目的需求变更必须创建 CR，记录变更原因、拟影响的 Blueprint 条目（businessKey）与发起人名称。
- **CP-CR-002**【调整】（7.6）CR 状态：DRAFT → ANALYZED → APPROVED → IN_PROGRESS → COMPLETED；DRAFT / ANALYZED 可 CANCELLED；ANALYZED 可 REJECTED。批准 CR 需要 SOLUTION_ARCHITECT 角色（**本期即校验**）。
- **CP-CR-003**【调整】（FR-WF-007）影响分析：S1 调用 S2 计算受影响阶段、对象与页面，补充受影响的已部署环境（本项目 COMMITTED 或 VERIFIED 的 Run 所在环境），生成 CHANGE_IMPACT_REPORT 产物。
- **CP-CR-004**【直引】（FR-WF-008）CR 进入 IN_PROGRESS 时，从受影响的最早阶段重走主线，未受影响的阶段保留原状态。
- **CP-CR-005**【调整】（FR-CMP-009、BP-CMP-026、DX-EXE-038）CR 创建请求接受 `deletionCandidates[]`（当前版本 Registry 中处置为 `DEPRECATED` 的 businessKey），并把它保存在 CR 行、随 CR 视图返回；影响分析不得覆写该声明（S2 的影响集不携带删除候选）。创建时当场校验：项目必须已有 Blueprint 版本，每个被点名的键必须在当前版本 Registry 中为 `DEPRECATED`，否则 422 `VALIDATION_ERROR`，`details.notDeprecated` 列出对不上的名字、`details.rule = "CP-CR-005"`；项目暂无版本时同样拒绝。R1 环境绑定编译时，S1 把该项目**唯一 IN_PROGRESS 的 CR** 的 ID 与候选清单一起交给 S2（BP-CMP-026 要求两者齐备），由 S2 在编译期再核引用与关联数据。
- **CP-CR-006**【新增】同一项目同一时刻最多一个 IN_PROGRESS 的 CR。理由：避免多个版本分支并行修改同一 Blueprint。
- **CP-CR-007**【新增】主线重新通过且新版本上至少一个 Run VERIFIED 后，CR 可标记 COMPLETED。理由：定义完成条件。
- **CP-CR-008**【新增】（DX-EXE-038、FR-TST-002、总体 SRS 15.6）S1 必须提供定向删除执行入口 `POST /environments/{environment_key}/delete-objects`，把一次 CR 授权的删除载荷交给 S4：请求体 `{projectId, deletionArtifactId, expectedHash}`（`extra="forbid"`，无 `importOptions`——`importStrategy=DELETE` 与 `atomicMode=ALL` 是 S4 的安全规则，不是逐请求偏好）；`Idempotency-Key` **请求头必填**，缺失返回 400 `IDEMPOTENCY_KEY_REQUIRED`。授权复用 `environment.manage`，不新造动作名。S1 只做两项前置检查：载荷产物必须属于请求中的项目（否则 404/422 并点名字段）、环境必须处于服务状态（否则 409）；删除策略、关联数据复查、紧急锁定与"一个候选被拒则整批拒绝"全部由 S4 判定，S1 不写自己的删除审计（S4 记录 `delete.executed`）。响应为 S4 执行结果的投影 `ObjectDeletionView`（`environmentKey`、`deletionArtifactId`、`status`、`applied?`、`detail?`、`executionId?`、`reportArtifactId?`、`importReportArtifactId?`、`payloadHash?`、`issues[]`、`steps[]`），同步 200 返回——删除不属于任何工作流，S1 没有 `stageId`/`stepRunId` 可放进 202 信封；后续经 `GET /executions/{executionId}` 与 SSE 观察。

### 3.8 通知（CP-NTF）

- **CP-NTF-001**【调整】（FR-GATE-008、FR-UI-012、D-16）MVP 通知为项目级：通知记录挂在项目上，所有使用者在通知中心可见。类型：GATE_PENDING、GATE_REMINDER、GATE_APPROVED、GATE_REJECTED、GATE_INVALIDATED、QUESTION_CREATED、STAGE_FAILED、STAGE_BLOCKED、STAGE_MANUAL_CHECK、EXECUTION_COMPLETED、EXECUTION_FAILED、BUDGET_THRESHOLD、BUDGET_EXCEEDED、CREDENTIAL_EXPIRING、AUDIT_INTEGRITY_FAILED。
- **CP-NTF-002**【调整】（19.5）可选渠道：邮件（SMTP）与 Webhook（HTTPS），接收地址按项目配置（平台级通知按系统设置配置）；Webhook 以 HMAC 签名；失败指数退避重试，上限由 `FACTORY_NOTIFICATION_MAX_ATTEMPTS` 决定（默认 5，小于 1 时按 1），每次尝试记录渠道、目标、次数、状态与最后错误；重试由 `deliver_notifications` 周期任务执行（默认 300 秒）。`GET /notifications?mine=true` 在启用认证时返回"项目级通知 + 只发给我的通知"，`total` 随之重算；`authMode=off` 时该过滤退化为只看项目级通知。
- **CP-NTF-003**【新增】预算与凭证到期类通知的触发：S1 在阶段运行返回预算超限时产生 `BUDGET_EXCEEDED`（阈值告警类型保留）；每日定时调用 S4 查询即将到期的凭证（默认提前 14 天，`FACTORY_CREDENTIAL_EXPIRY_WARNING_DAYS`）。理由：子系统之间不使用消息通讯，这两类状态需由 S1 主动获取。
- **CP-NTF-004**【直引】（19.2）提供通知列表与标记已读接口；标记已读写审计与事件。

### 3.9 系统设置与定时任务（CP-SET）

- **CP-SET-001**【新增】系统设置：幂等记录保留期、事件流保留期、默认审批提醒时长、上传大小上限与允许的文件类型、平台级通知渠道（密钥以 Secret 引用保存）、已归档项目保留期限、**阶段自动推进清单 `stage_auto_continue`**（`DOMAIN`、`DISCOVERY`、`MAPPING`、`PAGE_DESIGN`、`BASELINE_COMPILE`、`HANDOFF` 的子集；`REQUIREMENTS` 永不被自动开始，Gate 审批不受该设置影响）。共 8 个键；`GET /settings` 同时返回 `items[{key,value}]` 与逐键定型的 `values`，`PUT /settings` 的未知键返回 404。每次变更写审计。
- **CP-SET-002**【新增】S1 以 Temporal 定时工作流（或 `inprocess` 引擎的 asyncio 循环）调度以下 10 个任务；`damf-worker` 进程承载它们，`factory-api` 只做迁移与注册：`link_audit`（默认 5 秒，可由 `FACTORY_AUDIT_LINK_INTERVAL_SECONDS` 覆盖，非法值回退默认）、`auto_continue`（5 秒）、`housekeeping`（S0 过期暂存、待删除重试与过期幂等记录，每小时）、`verify_chain`（每日全链校验，失败产生 `AUDIT_INTEGRITY_FAILED` 通知）、`purge_events`（每日，事件保留期）、`purge_llm_exchanges`（每日，S3 模型调用日志保留期）、`retention`（每日，已归档项目产物保留期，`archived_project_retention_days <= 0` 时跳过）、`check_credentials`（每日，凭证到期检查）、`deliver_notifications`（300 秒，投递重试）、`gate_reminders`（900 秒，Gate 催办，最多 3 次）。任务间隔在每次调度时解析，不在 import 期冻结；单个任务失败只记录，不终止循环。`FACTORY_TEMPORAL_RETENTION_HOURS` **已删除**：保留期是 Temporal namespace 的属性，而本仓库不存在创建 namespace 的逻辑，保留一个没人读的键就是假配置。

### 3.10 身份与权限（CP-IAM，本期交付；`auth=off` 时写操作受 M4 门控）

- **CP-IAM-001**【调整】（D-16 已撤销）认证与权限为本期交付。S1 必须在全部 API 处理路径上调用统一的授权钩子 `authorize(operator, projectId, action)`；`authorize()` 按部署的 `FACTORY_AUTH_MODE` 选择策略：
  ① `off`（默认）：`AllowAllPolicy`，**恒为允许**，操作者仍是 `X-Operator` 声明的名字（保持 MVP 部署与既有 S5 客户端逐请求不变）；这不是"放行读取、拒绝写入"，写操作同样放行。
  ② `local` / `oidc` / `both`：使用角色策略，按附录 B 的动作名称逐条真值判定；项目级动作还要求调用者是该项目成员；未解析到活动账号的请求一律拒绝（403 `PERMISSION_DENIED`，不是 500）。
  启用认证时 `FACTORY_HTTPS_ONLY` 必为 true，否则启动校验失败。理由：策略对象可替换，但处理路径与动作名只有一套。
- **CP-IAM-002**【裁剪】（FR-SEC-001～005、FR-GATE-003、FR-GATE-005）**本期交付**（此处【裁剪】为出处标记，非延后）：
  1. **认证方式**：本地账号（口令用 stdlib `hashlib.scrypt`，自描述参数、登录时透明升级，最短 8 位由服务层执行）与 OIDC（授权码 + userinfo 断言；issuer 必须为 HTTPS，`FACTORY_OIDC_AUTO_PROVISION` 缺省关闭）、或两者并存。会话为服务端不透明令牌（库存 SHA-256），空闲 30 分钟（`FACTORY_SESSION_IDLE_MINUTES`）与绝对 12 小时（`FACTORY_SESSION_ABSOLUTE_HOURS`）双时限；Cookie 会话的写请求须带 `X-CSRF-Token`；登录失败 5 次锁定 15 分钟，失败计数在认证事务之外提交。bootstrap 管理员由 `FACTORY_BOOTSTRAP_ADMIN_SUBJECT` + `FACTORY_BOOTSTRAP_ADMIN_SECRET_REF` 在启动时创建。
  2. **角色**：`SOLUTION_ARCHITECT`、`DHIS2_IMPLEMENTER`、`FRONTEND_DEVELOPER`、`BUSINESS_ANALYST`、`DEPLOYMENT_APPROVER`、`PLATFORM_ADMINISTRATOR` 六个内置角色；同一角色名可在组织级（`project_id` 为空）与项目级并存，两级授权的并集生效。
  3. **隔离**：项目级读取与动作以成员身份为前提（FR-SEC-004）；`GET /projects` 与 `GET /deployment-runs` 按可见项目集合在查询内过滤（组织级持有 `project.view` 者看到全组织，只有项目级授权者只看到自己所属项目，`total` 与行数一致）。
  4. **Gate**：设计 Gate 与 DEPLOY_GATE 按 CP-GATE-007/012 校验，含环境类别精确动作、`operatorRole`、审批人数与职责分离。
  5. **受限产物**：RESTRICTED / PHI_PII 产物的读取需要项目内的 `restricted_grant`（按人、按分级、带理由与到期时间）并逐次审计；`artifact.read.restricted` 与 `artifact.read.phi` 是实际调用点，不是预留动作。
  6. **生产豁免与个人通知**：CP-WF-020 的豁免、`GET /notifications?mine=true` 的个人收件箱均在本期实现；写端点受 CP-UI-023 的 M4 门控约束。
  7. **账号管理**：`GET /roles`、`GET/POST /users`、`PATCH /users/{id}`、`POST /users/{id}/password`、`PUT/DELETE /users/{id}/roles`，动作 `user.manage`；用户与角色变更全部入审计。
- **CP-IAM-003**【新增】（FR-SEC-001、CP-API-002）启用认证（`authMode != off`）后操作人身份只来自会话：S1 用会话主体（账号的 subject）覆盖请求声明的 `X-Operator`，客户端照旧发送该头时其值不被采信，也不因与之矛盾而拒绝请求；该头同时不再是必填项——CP-API-002 的 400 `OPERATOR_REQUIRED` 只在未启用认证时生效。审计、审批与 Question 回答记录中出现的都是会话主体。理由：MVP 的声明式身份与 M4 的会话身份共用同一套处理路径，覆盖规则必须唯一，否则一次操作会留下两个"人"。
- **CP-IAM-004**【新增】（FR-SEC-005）会话与 CSRF 值的交付方式：会话标识只放在 HttpOnly Cookie（默认 `factory_session`）；CSRF 值在登录成功时随会话视图返回一次，并镜像到一个可读 Cookie（默认 `factory_csrf`），使页面刷新后仍能命名该请求头；以 Cookie 会话发起的改变状态请求必须携带该头（默认 `X-CSRF-Token`），缺失或不匹配返回 403 `CSRF_FAILED`，会话过期、被撤销或账号被停用返回 401 `UNAUTHENTICATED`。设置口令与停用账号立即撤销既有会话。理由：只发一次的随机值若没有可读副本，刷新后的页面再也发不出这个头，每个写操作都会 403。
- **CP-IAM-005**【新增】（FR-SEC-001、FR-SEC-005）口令与失败计数的落地边界：
  ① 最短口令（8 位）由**服务层**在创建账号与设置口令两处共同执行，不得只写在请求 schema 上——`POST /api/v1/users` 的 `password` 若无该约束，就能建出一个 1 个字符口令的账号，而 bootstrap 管理员是在进程内直接调用服务层建号的，schema 上的规则永远管不到它。校验失败的回答只点名字段与要求，禁止引用提交的值（与 CP-SEC-004 同一条理由）。
  ② 认证失败的计数**必须在认证事务之外提交**。登录失败要抛出异常，而工作单元见到异常即回滚（`factory_foundation/db/uow.py:89`）：写在同一事务里的计数会连同这一次尝试一起消失，症状是每次都返回 401、审计里也留下了失败记录，但 `locked_until` 永不生效——速率限制形同不存在，而这正是 FR-SEC-005 要防的事。
  ③ `mustChangePassword` 是**报告位**：S1 在 `GET /auth/session` 与 `GET /users` 中如实返回，服务端**不**因此拦截任何调用。理由与新版本若要收紧的决定一并记录：硬拦会让新装机器的第一个账号（bootstrap 建的就是 `true`）在引导第一屏就面对一片 403，而引导本身需要的读取同样被拦；界面改为显式提示并给出自助修改入口（S5 顶栏、S5M「账号与会话」）。收紧为服务端拦截属于一个明确的版本决定，不是当前实现的缺陷。
  ④ 被锁定的账号在登录端点上的回答与"口令错"**逐字相同**（仅每次请求的关联 ID 不同）：区分二者等于提供一条账号枚举通道（真账号猜错五次会锁，假账号不会）。锁定状态由 `GET /api/v1/users` 的 `locked` 说给管理员，两个控制台的账号列表都画它；重置口令即解锁并撤销该账号全部会话（CP-IAM-004）。

### 3.11 运行时组装（CP-RT）

- **CP-RT-001**【调整】（24.2、24.4、D-10）S1 仓库从私有 PyPI 以固定版本依赖 S0～S4，构建 `factory-api`（REST + SSE，无状态，可水平扩展）与 `factory-worker`（Temporal 工作进程，承载工作流与活动，可水平扩展）两个镜像。
- **CP-RT-002**【直引】（FR-DEP-003）提供 Docker Compose（开发与评估，含 PostgreSQL、Temporal、对象存储、S5 静态前端）与 Helm Chart（生产）。
- **CP-RT-003**【调整】（FR-DEP-001）开发模式允许以进程内工作流适配器替代 Temporal，与 Temporal 实现同一接口。
- **CP-RT-004**【新增】进程启动时按 00 文档 6.10 的顺序执行：factory-api 以各子系统导出的 `MIGRATION_CONFIG` 调用 S0 迁移运行器（可配置为执行、仅检查或跳过）；随后调用 S0 `initialize_foundation()`、S4 / S2 / S3 的 `initialize()`，再登记 S1 自身的产物类型、审计事件类型、错误码与 ID 前缀。S1 可登记的错误码集合必须在这一步（即提供方登记之后）计算，并且只含 S1 自己拥有的码：每个码只有一个所有者（FD-COM-001a），把上游拥有的码登记进来会让启动结果取决于进程内的导入与注册顺序。`APP_ENV=production` 时 Secret 后端只注册 `memory`（FD-SEC-004 的一次性值需要）与按主密钥安装的 `db://`，**不注册 `env://`**；`FACTORY_CREDENTIAL_MASTER_KEY` 缺失或短于 32 字符时 `db://` 不注册、凭证端点回 503，进程照常启动。S4 的 `initialize()` 从环境重载 `FACTORY_DX_*` 设置（此前模块级默认与 `configure()` 调用缺失并存）。启动报告（含 `authMode`、迁移模式、已初始化/缺失的提供方、登记计数）由 `/healthz` 与 `GET /setup` 回显。
- **CP-RT-005**【新增】组装时必须运行 S0～S4 的公共 API 兼容性测试；任一失败则构建失败。理由：独立发版下的集成保障。
- **CP-RT-006**【新增】S1 必须区分存活探针与就绪探针：`GET /healthz` 只回答进程装配事实（状态、引擎、各端口 real/fakes、启动报告），**不证明 PostgreSQL 可达**；`GET /readyz` 用运行时自己的 unit of work 执行一条语句，数据库不可达返回 503 `{status: "not_ready", reason}`，可达返回 200 `{status: "ready"}`。`/readyz` 不进入 OpenAPI 文档（`include_in_schema=False`），不计入契约路径数，不作任何控制台可调的操作，并在认证白名单内。
- **CP-RT-007**【新增】部署必须提供 `damf-api` 与 `damf-worker` 两个 systemd 单元（同一 `EnvironmentFile`，`install.sh` 渲染、`start.sh` 启停与查看日志）；worker 必须等 api 通过 `/healthz` 后再启动——迁移没有并发锁，两个进程同跑 `upgrade` 只在库已是 head 时才是无害空操作。worker 承载 Temporal 工作流与 CP-SET-002 的 10 个周期任务；`FACTORY_WORKFLOW_ENGINE=inprocess` 时运行开发用的 asyncio 定时循环。`factory-api` / `factory-worker` 的命令行参数不得原地修改 `lru_cache` 的配置单例（应 `model_copy` 出局部副本）。

### 3.12 界面支撑：转发、聚合与定型（CP-UI）

本节把 S5 实现所需、而第 3.1～3.11 节尚未具体化的公共接口要求逐项固定（来源：S5 开发组《S1 公共 REST API 补充需求》v0.2.0，条目编号 `S5IF-*` 标注在各需求后）。转发端点的资源概念与取值以提供方公共 API 为准（CP-API-016），S1 负责路径、请求校验、camelCase 投影与错误归一。

#### 3.12.1 环境与能力（转发 S4）

- **CP-UI-001**【调整】（S1 §7.2 环境管理转发；S5IF-ENV01～ENV12）S1 必须提供环境的列表（可按环境类别、状态筛选，分页）、详情、创建、修改、禁用、保护属性、紧急锁定、连接测试、能力档案、能力探测、发现结果列表、即将到期凭证共 12 个端点（路径见第 8.1 节）。要求：
  1. 环境视图至少包含：环境键、名称、baseUrl、环境类别（`DEV` / `TEST` / `UAT` / `PRODUCTION`）、版本约束、认证方式（`PAT` / `BASIC`）、是否要求 TLS、数据外发策略、超时、状态（`ACTIVE` / `DISABLED`）、备注、按凭证范围的凭证记录（范围、Secret 引用、到期日、标签）、创建与更新时间；
  2. 保护属性视图至少包含：环境类别、时区、提交时间窗（按星期与时间段）、当前是否处于时间窗内、删除策略、紧急锁定及原因、变更时间与变更人、所需审批人数与操作员角色（**本期可编辑并即时生效**）；保护属性每次实时读取，不缓存（CP-WF-027）；
  3. 创建与修改须由 S1 定义请求模型并校验：Secret 引用格式非法返回 422 `VALIDATION_ERROR`；PRODUCTION 环境 discovery 与 import 引用同一 Secret 返回 422，`details` 指明冲突范围（DX-ENV-005）；禁用存在进行中执行的环境返回 409 `INVALID_STATE`；
  4. 连接测试请求携带所需凭证范围（缺省为 discovery 与 import），响应包含总体结论、逐项检查（名称、`PASSED` / `FAILED` / `SKIPPED`、说明）、DHIS2 版本、测试时间、所需范围、按范围归组的缺失权限、能力档案产物 ID、消息；
  5. 能力档案视图包含 Profile 版本、DHIS2 版本、探测时间、新鲜度（`VALID` / `EXPIRED` / `VERSION_CHANGED`）、过期原因、产物 ID、内容哈希；`includeContent=true` 时附档案内容（其字段集以 S4 发布的内容格式为准）；
  6. 紧急锁定请求为 `{locked, reason}`，响应返回锁定状态、原因、变更时间与变更人，并写审计；
  7. 即将到期凭证支持 `withinDays` 参数，返回环境键、范围、Secret 引用、到期时间、剩余天数；该字面路径不得被 `/{environmentKey}` 路由吞没；
  8. 定向删除执行 `POST /environments/{environmentKey}/delete-objects` 按 CP-CR-008 定义；它与连接测试、能力探测一样同步返回提供方结论，但必须携带 `Idempotency-Key` 请求头。
  创建、连接测试、能力探测、定向删除支持 `Idempotency-Key`（定向删除为必填）。

#### 3.12.2 Blueprint、Registry 与 Schema（转发 S2）

- **CP-UI-002**【调整】（S1 §8.1 转发行；S5IF-BP01～BP10、BP12、BP13）S1 必须提供：
  1. 分节目录（当前与可用 Schema 版本、各分节的名称、条目容器、键字段）与单个分节的自包含 JSON Schema（未知分节 404 `NOT_FOUND`，不支持的 Schema 版本 422）；
  2. 版本列表（版本号、父版本、Schema 版本、变更级别、内容哈希、产物 ID、状态、来源产物、决策、创建人与时间、所属阶段）；
  3. 条目查询：按分节、businessKey、决策 ID、页面键、来源（`APPROVED` / `DRAFT`）与版本选取，返回条目原始内容并保留分节与路径两个定位键，以及未找到的键；
  4. 当前活动草稿元数据（草稿 ID、基准版本、阶段、阶段范围、状态、哈希、是否可提升、PROPOSED Patch 数、`changeRequestId`）；**S1 必须由项目（与阶段）解析出当前活动草稿 ID，并随工作流视图与阶段视图返回**，S5 不推断该 ID；不提供整份草稿内容读取（`document` 显式为 `null`），条目内容一律经本条第 3 项以 `source=DRAFT` 读取；尚无任何草稿时返回 404 `NOT_FOUND`，`details = {reason: "NOT_GENERATED", requiredStage: "REQUIREMENTS"}`（CP-UI-022），不得把"还没有草稿"说成对象不存在；
  5. Registry 列表（可按版本与一个或多个环境取绑定；businessKey、DHIS2 类型、Disposition、状态、UID、code、是否托管、受控字段、决策 ID、观测基准、环境绑定；UID / code / Disposition 在投影中位于条目顶层）与 Managed Scope；
  6. UID 池状态（可用数、低水位、来源环境、是否需补充）与补充动作 `POST /projects/{id}/uid-pool/replenish`：请求体可选 `{environmentKey}`（缺省为设计基准环境，必须是本项目已登记的目标环境），响应为补充后 S2 报告的同一个 `UidPoolView`，不返回 S1 自算的计数；授权 `uid_pool.replenish`；
  7. 环境绑定列表（由 Run 解析版本与环境）与人工设置：请求 `{uid, note}`，`note` 必填，版本与环境由 S1 从 Run 解析，不出现在 URL；绑定视图包含 `observedHash`、`discoveryResultId`、解析方式、设置人、说明、是否待重新解析（`observedLastUpdated` 如有则返回）；
  8. 对象级 Diff：两个版本之间，或工作草稿相对基准版本（对象状态 `ADDED` / `UPDATED` / `UNCHANGED` / `REFERENCE_ONLY` / `REMOVED`，字段级变化）；
  9. 决策列表（决策 ID、类型、主题、选定方案、备选、理由、风险、置信度、影响级别、确认记录、影响的产物），以 camelCase 返回。
- **CP-UI-003**【新增】（S5IF-BP15）S1 必须提供按工作流读取 NRR 的端点：转发 S3 的 NRR 查询，支持按类别、输入产物、置信度筛选与分页，每条含类别、陈述、来源（输入产物 ID 与位置）、置信度、是否已确认。带类别的 NRR 只来自 S3；S2 `requirements` 分节不承载类别。理由：界面分两块显示——S3 的 NRR（含类别）与 S2 的已批准条目（无类别）。
- **CP-UI-004**【新增】（S5IF-BP14）S1 必须提供按工作流查询校验问题的端点，参数：`runId`、`level`、`sourceStage`、`ruleId`、`businessKey`、排序与分页。S1 确定相关的 VALIDATION_REPORT 与 DHIS2_IMPORT_REPORT 产物后调用 S2 的问题查询，按统一 Issue 结构（00 文档 6.10，级别字段 `level`）投影为 camelCase。
- **CP-UI-005**【新增】（S5IF-BP11）S1 必须提供环境绑定候选对象检索端点：由 Run 解析目标环境最近一次发现结果，转发 S4 的发现对象检索（按 DHIS2 类型与 code / name 关键字，分页），返回 UID、code、name、lastUpdated、内容哈希。S1 不解析发现结果产物内容。

#### 3.12.3 Governance（转发 S3）

- **CP-UI-006**【调整】（S1 §7.2 Governance 转发；S5IF-GOV01～GOV20）S1 必须为 S3 的每一项有类型的治理函数提供独立端点与具名请求 / 响应组件（路径见第 8.1 节）：提供方（列表、登记、启用、**连通性探测**）、模型（列表、登记、启用、**差量编辑**）、Prompt（列表、版本详情）、生效（列表、切换）、回归记录（**列表与登记**）、组织策略（读取、放宽）、评测（列表、详情含逐例结果、发起）、用量汇总、预算（读取与写入）、Agent 目录、内置提供方目录。要求：
  1. 提供方与模型视图包含接口地址与 API Key 的 Secret 引用，只回引用；登记请求中的 Key 必须是引用形式，字面值返回 422；`apiKeySecretRef` 可为空（表示该地址不需要 `Authorization`），非空时按引用语法校验；
  2. Prompt 切换不满足评测或回归门槛时透出 `AI_PROMPT_ACTIVATION_BLOCKED`（409），`details` 指明缺失项；
  3. 策略放宽必须填写理由（空理由 422）；放宽 PHI_PII 或超出上限返回 403 `AI_POLICY_RELAXATION_DENIED`；`GET /governance/policy` 同时返回生效矩阵与 `ceiling`，放宽响应带 `reason`、`change`、`createdAt`；
  4. 用量汇总的分组维度由 S1 先校验，非法维度 422，不得出现 500；
  5. 评测发起支持 `Idempotency-Key`，以 202 受理（CP-API-012）；预算写入 `PUT /governance/budget` 以 `governance.budget.set` 授权，真实写入 S3 并审计，形状不合法返回 422 `AI_BUDGET_INVALID`；
  6. S1 对 S3 治理能力的端口按函数逐项定义且带类型，不得定义为 `governance(action, payload)`；在 S3 提供有类型函数之前，只允许在 S3 适配器内部把端口方法映射到 S3 的过渡门面（AI-GOV-009），过渡至 M2 结束为止；公共 REST 契约不受适配器实现的影响；M2 结束后兼容性测试（CP-RT-005）检测到对门面的引用即构建失败；
  7. 另提供项目有效策略端点：S1 取项目全部目标环境的数据外发策略（实时查询 S4），调用 S3 计算有效策略（D-07），返回有效策略、来源与告警；
  8. 提供方连通性探测按 CP-API-017：`POST /governance/providers/{name}/test-connection`，未登记提供方返回 200 + `healthy = false`；
  9. `PATCH /governance/models/{ref}` 是**差量编辑**：`capabilities`、两个单价、`applicableAgents`、`enabled`、`endpoint`、`apiKeySecretRef` 全部可选，`extra="forbid"`，一个字段都没点名（含只传 `null`）返回 422；只写被点名的列，`capabilities` 在已存载荷上按键合并；响应为整行 `ModelView`；授权复用 `governance.model.enable`；审计记 `before` 与 `after`。`ref` 为 `provider/model`，可含 `/`；
  10. `GET /governance/regression-records` 提供读侧，可按 `promptName` / `promptVersion` 过滤；`GET /governance/provider-catalogue` 只列 OpenAI 兼容提供方，不含 `api.anthropic.com` / Gemini 原生地址，也不含任何"可达"字段。

#### 3.12.4 输入、Agent 运行与问题解释（转发 S3）

- **CP-UI-007**【调整】（S5IF-INP01～INP09）S1 必须提供：
  1. 输入解析端点：对某输入产物发起解析（PARSE_INPUT 步骤），请求体可带 `options`（`columnMapping`、`sheet`、`delimiter`、`hasHeader`、`parser`、`exportedAs`），同一端点覆盖首次解析与确认列映射后的重新解析；支持 `Idempotency-Key`，以 202 受理。解析在后台线程执行，`cp.input_parse_runs` 是跨重启的持久状态；幂等键必须包含调用方带来的键（`parse:{artifactId}:{key}`），使修好提供方后的重试**真的重跑**，而不是原样回放上次的失败；HTTP 层重复点击仍由幂等中间件拦住。同一输入同时最多一个 `PARSING` 运行（数据库部分唯一索引）；进程重启遗留的 `PARSING` 行在启动时收为 `PARSE_INTERRUPTED`；
  2. 输入列表逐项包含解析状态（`PARSING` / `PARSED` / `NEEDS_REVIEW` / `FAILED` / `POLICY_DENIED`，未解析为空）、错误码与消息（扫描件为 `SCANNED_DOCUMENT_UNSUPPORTED`）、解析器类型与版本、是否使用模型、解析 ID；解析详情取自 S3 的解析记录查询，S1 只保存本方解析运行记录；
  3. 列映射提案端点：转发 S3 的结构化提案（各工作表的表头、建议映射、识别出的类别、识别依据、受影响的键）；
  4. 问题解释端点：请求 `{projectId}`，S1 按 D-07 计算有效策略后调用 S3；响应 `{explanationArtifactId}`（可为空），正文经产物内容端点读取；只接受 VALIDATION_REPORT 与 DHIS2_IMPORT_REPORT，其他类型 404；
  5. Agent 运行查询：返回运行 ID、状态（含 `RUNNING`）、摘要、置信度、Patch ID、AGENT_OUTPUT 产物 ID、该运行的 Question（取自 S1 自有存储）、问题、预算状态、每个模型的拒绝原因、被拒绝的产物、最近进度（取自事件流最新的 `agent.progress`）；运行状态以 S1 的阶段状态校正；
  6. Agent 运行取消：响应 `{runId, status}`，该动作纳入 `allowed-actions`；
  7. 模型调用日志（S5IF-INP08/INP09）：`GET /projects/{id}/inputs/{artifactId}/llm-log` 分页列出该输入解析产生的模型调用摘要（时间、模型、是否成功、耗时、错误码），`GET .../llm-log/{exchangeId}` 返回单次交换的请求消息与模型原始答复；读取同时要求 `project.view` 与对该输入产物的读取授权（按其分级），日志正文不得进入 SSE 事件；保留期由 `purge_llm_exchanges` 每日清理。

#### 3.12.5 阶段、工作流与 Gate 视图

- **CP-UI-008**【新增】（总体 SRS FR-UI-001；S5IF-FLD01）阶段视图必须由服务端计算并返回：`inputCompleteness`（`satisfied`、`total`、`ratio`、`missing[{key, label}]`，`label` 按 `Accept-Language` 本地化）、`artifactCount`、`openQuestionCount`、`highOpenQuestionCount`、`blockingIssueCount`（取该阶段最近一次校验结果的计数）、`lastRunAt`、`currentArtifactVersion`（该阶段所依据的版本或草稿哈希）、`activeDraftId`（设计阶段）。整个工作流的阶段列表必须一次查询算完，禁止逐阶段查询。“输入完成度”的前置清单由 S1 按阶段类型定义：

| 检查项 | 适用阶段 |
| --- | --- |
| 前置阶段已完成（PASSED 或 SKIPPED） | 全部阶段 |
| 必需的上游产物已存在 | 全部阶段 |
| 至少存在一个已解析的输入 | REQUIREMENTS |
| 目标环境能力档案有效（取最近一次探测结果，不访问 DHIS2） | BIND_COMPILE 及其后的 Run 步骤 |

- **CP-UI-009**【新增】（S5IF-FLD02）阶段的 `feedback` 定型为 `{kind, reason, sourceStage, gateId, issues[], createdAt}` 或空；`kind` 取值 `GATE_REJECTED`、`PATCH_REJECTED`、`VALIDATION`、`POLICY_DENIED`、`BUDGET_EXCEEDED`、`EXECUTION_FAILED`，并声明为**可扩展枚举**（客户端遇未知值按通用形态渲染）。`inputBlueprintRef` 定型为 `{kind: DRAFT | VERSION, id, hash}`。
- **CP-UI-010**【新增】（S5IF-FLD03）S1 必须提供跨项目 Gate 列表：可按状态、项目、类型筛选，分页；元素含 Gate ID、工作流、项目 ID 与名称、阶段、Run、环境键、类型、状态、所需审批数、已审批数、WARNING 数、未处理 WARNING 数、HIGH 未决 Question 数、创建时间。`mine=true` 在启用认证时按"此人能否审批该 Gate"（项目成员身份 + 精确审批动作 + 环境类别）过滤当前页；`authMode=off` 时返回全部。**当前实现的边界**：`mine=true` 的过滤发生在分页之后，`total` 仍是未过滤的总数——服务端分页下的全局"待我审批"尚未交付，客户端不得把 `total` 当作"待我审批"的计数。
- **CP-UI-011**【新增】（S5IF-FLD04、FLD06）S1 必须提供跨项目 Deployment Run 列表：可按状态、环境键、是否 OUTDATED 筛选，默认按更新时间倒序，分页。Run 视图增补 `projectId`、`projectName`、`environmentClass`、`lastVerifiedAt`、`postVerifyArtifactId`。列表按**成员可见项目集合**在查询内过滤（与 `GET /projects` 同一规则，FR-SEC-004），`total` 与行数一致。`environmentClass` 归 S4 所有，禁止写入 S1 数据库，只能请求时查询 S4，并按 00 文档 4.2 最多做 60 秒进程内缓存。
- **CP-UI-012**【新增】（总体 SRS 18.3；S5IF-FLD05）S1 必须提供部署控制台聚合视图 `GET /deployment-runs/{runId}/summary`，一次返回：Run 与项目标识、环境键与类别、Blueprint 版本、是否 OUTDATED、状态（及 `evaluatedAt`、紧凑阶段列表）、DHIS2 版本与 Profile 版本、能力新鲜度（状态、探测时间、产物 ID）、连接（结论、缺失权限、测试时间）、漂移（是否漂移、检查时间、差异对象）、Snapshot（状态、产物 ID）、校验（静态与服务端是否通过、BLOCKING / WARNING 数、报告产物 ID、`diffReportArtifactId`）、审批（PACKAGE_GATE 与 DEPLOY_GATE 状态、所需与已有审批数）、变更统计（新建、更新、删除、Managed Scope 外引用）、维护动作、载荷（哈希、元数据包产物 ID）、提交时间窗（是否在窗内、时间窗、时区、紧急锁定）、部署后验证（时间、报告产物 ID、是否一致）、执行（ID、状态、报告与 ImportReport 产物槽位，形状按 CP-UI-024）。**硬约束：该请求不得调用任何会访问 DHIS2 的函数**——漂移只返回最近一次检查结果，能力新鲜度只返回最新档案索引，连接只返回最近一次测试结果；保护属性可实时查询（只读 S4 数据库）；其余取自 S1 自有记录与已登记的产物。不新增持久化。`changeStats` 在 S2 发布 DIFF_REPORT 读取前为空对象各计数为 0，不得伪造。
- **CP-UI-013**【新增】（S5IF-FLD05）S1 必须提供漂移重检动作 `POST /deployment-runs/{runId}/drift-check`：以 202 受理，作为步骤执行 S4 漂移检查，结果（是否漂移、检查时间、差异对象摘要）记录为 Run 的最近漂移检查结论并推送事件；检测到漂移时按 CP-GATE-005 使 DEPLOY_GATE 失效。该动作纳入 `allowed-actions`（`deployment_run.drift_check`）。
- **CP-UI-014**【新增】（S5IF-FLD07）Gate 视图的 `warnings[]` 定型为统一 Issue 结构（`level` 等字段）；`confirmationItems[]` 定型为 `{id, label, required, kind: BACKUP | IMPORT_PARAMETER | MAINTENANCE_ACTION, detail}`；`boundObjects[]` 定型为 `{refType, refId, contentHash}`（`refType` 为 `ARTIFACT` / `BLUEPRINT_DRAFT` / `BLUEPRINT_VERSION` / `ENVIRONMENT` / `EXECUTION`），其形状须保证客户端可原样回填为批准请求的 `seenBoundHashes`。
- **CP-UI-015**【新增】（S5IF-FLD08）Question 的 `suggestions[]` 定型为 `{id, text, rationale, changesUid, regenerates[], recommended?, columnMapping?}`（`columnMapping` 仅列映射类 Question 携带；`recommended` 是模型给出的推荐项标记）。`QuestionView` 另带 `answerMode`（`SINGLE` / `MULTIPLE`），与 `suggestions[]` 同源；单选与多选的作答约束见 CP-Q-002。整页问卷端点为 `POST /questions/answers`（CP-Q-008），响应中的 `questions[]` 仍用本形状。

#### 3.12.6 产物、设置与 Patch

- **CP-UI-016**【新增】（S5IF-FLD09）产物内容端点支持 `download=true`：返回 `Content-Disposition: attachment` 与文件名。文件名规则：BUSINESS_INPUT 用上传时的原文件名；PROJECT_EXPORT 用 `{项目名}-export-{UTC 日期}.zip`；其他类型用 `{类型}-{产物 ID}.{扩展名}`。不带参数时内联返回。内容已清除时，两种请求都返回 410 `ARTIFACT_PURGED`。该端点的媒体类型、`X-Data-Classification` 与 410 情形声明进 OpenAPI。
- **CP-UI-017**【新增】（S5IF-FLD10）系统设置的键与值类型定型：`idempotency_retention_hours`（整数）、`event_retention_days`（整数）、`default_gate_reminder_hours`（整数）、`max_upload_bytes`（整数）、`allowed_upload_types`（字符串数组）、`archived_project_retention_days`（整数或空）、`platform_notification_channels`（`{type, target, secretRef}` 数组，密钥只能是 Secret 引用）、`stage_auto_continue`（阶段类型字符串数组）。键在 OpenAPI 中声明为枚举；`max_upload_bytes` 与 `allowed_upload_types` 可被客户端读取（`GET /upload-limits`，可选 `projectId` 以按项目判定 `input.upload`），用于上传前预检，与 CP-API-009 的服务端校验一致。`GET /settings` 的 `values` 对象以 camelCase 给出同样的 8 个键的定型值；设置键本身保持 `snake_case`（这是 CP-API-011 的唯一例外）。
- **CP-UI-018**【新增】（S5IF-FLD11、契约 1.9.0/1.16.0）人工 Patch 提交的响应定型为 `{stageId, patchStatus, draftHash, issues[], questions[], gateInvalidated, assignedUids{businessKey: uid}}`。幂等键：调用方带来的 `Idempotency-Key` **优先**（服务端拼成 `patch:{stageId}:{key}`）；没有该头时退回 `patch:{stageId}:{attempt}:{内容 + 草稿哈希摘要}`——同一次尝试里的多条不同编辑都能落下，字节相同的重发对同一草稿只生效一次。**不得**再使用 `patch:{stageId}:{attempt}`：那会把一次尝试钉死在该尝试的第一条内容上，第二条与"改回来"都变成 422 `IDEMPOTENCY_CONFLICT`。UID：提交前 S1 先从设计基准环境补充 UID 池（与 Agent 路径一致），补池调用失败或池仍耗尽时按 S2 的 `UID_POOL_EXHAUSTED` 返回 409，`retryable` 取自错误目录（该码**不可重试**，客户端应显示"补充 UID 池"入口）；不得返回 500，也不得谎称"服务端会自动重试"——显式补池入口是 `POST /projects/{id}/uid-pool/replenish`（CP-UI-002 第 6 项）。

#### 3.12.7 允许动作扩充

- **CP-UI-019**【调整】（CP-WF-025/026；S5IF-G10、FLD12）`allowed-actions` 必须覆盖本节新增端点对应的动作（名称见附录 B），不可用时给出原因码。`targetId` 语义固定：阶段、Gate、Run、Agent 运行、产物类动作为其 ID；环境类动作为环境键；无特定目标时省略。

#### 3.12.8 依赖图、开发包文件、尚未生成的资源与 M4 端点

- **CP-UI-020**【新增】（总体 SRS 6.5、FR-UI-005）S1 必须提供对象依赖图端点，依赖图的**唯一来源是 S2 的依赖图读取（BP-TD-009）**：
  1. 参数 `runId`：取该 Run 环境绑定编译的构建；参数 `blueprintVersion`：取该版本最近一次基线编译的构建；两者都省略：取当前版本最近一次基线编译的构建；`runId` 与 `blueprintVersion` 同时给出时返回 422；
  2. 可按 DHIS2 类型与 businessKey 筛选；节点、度数、`orphans`、`unresolvedRegistryRefs` 的含义与算法以 BP-TD-009 为准（在完整图上计算），S1 只做 camelCase 投影；
  3. S1 **不得**自行由 Blueprint 版本或产物内容计算节点、度数、孤立节点或引用断点；既有的自行计算实现必须移除；
  4. 对应构建尚不存在或未产出依赖图时，按 CP-UI-022 返回 `NOT_GENERATED`（`requiredStage` 为 `BASELINE_COMPILE` 或 `BIND_COMPILE`）。
- **CP-UI-021**【新增】（总体 SRS 17.1）S1 必须提供开发包文件清单与单文件读取端点（模式 B，M3）：S1 解析项目当前 Blueprint 版本对应的 FRONTEND_PACKAGE 产物，尚无开发包时返回 `NOT_GENERATED`（`requiredStage = HANDOFF`）；有则转发 S2 的开发包文件读取（BP-HO-004）。S2 返回的 `ARTIFACT_TYPE_MISMATCH` 表示 S1 自身解析错误，映射为 500 `INTERNAL_ERROR` 并记录告警；非法路径按 S2 结果返回 `NOT_EXIST`。单文件读取支持 `download=true`（规则同 CP-UI-016）。
- **CP-UI-022**【新增】对尚未生成的派生资源（依赖图、开发包、活动草稿、OpenAPI、UI Specification 等），S1 统一返回 404 `NOT_FOUND`，`details` 为 `{reason: "NOT_GENERATED", requiredStage, runId?}`；资源确实不存在时 `details.reason = "NOT_EXIST"`。已实现的 `requiredStage`：活动草稿为 `REQUIREMENTS`（首个设计阶段尚未运行、`bp.blueprint_draft` 无行时），依赖图为 `BASELINE_COMPILE` 或 `BIND_COMPILE`，开发包为 `HANDOFF`。不得以 200 加空对象或自定义的“无内容”标记表示尚未生成。理由：客户端据此显示“完成某阶段后可查看”的引导，而不是空白页面或一条要人去找管理员的错误。
- **CP-UI-023**【新增】（CP-IAM-002）项目成员、受限产物读取授权、生产豁免的端点已实现。写操作的门控条件是**部署是否启用 M4**：`authMode != off` 或 `FACTORY_M4_FEATURES_ENABLED=true` 时写操作生效（按 `member.manage` / `restricted_grant.manage` / `production_exemption.manage` 与项目成员身份判定）；两者皆否时写操作返回 409 `FEATURE_NOT_ENABLED`（`details = {feature, milestone: "M4"}`），`allowed-actions` 以同一原因码标为不可用。读取端点始终可用，按成员关系与产物分级过滤。不得把这条门控描述成"已彻底移除"。
- **CP-UI-024**【新增】（S5IF-FLD05、FD-ART-010）Run 摘要中的产物引用统一为 `ArtifactSlot = {artifactId?, status?, contentPurged}`（缺省 `contentPurged = false`）：`snapshot`、`execution.reportArtifactId`、`execution.importReportArtifactId` 都是该对象，而不是裸字符串。`status` 是生命周期（DRAFT / PROPOSED / APPROVED / SUPERSEDED），**不表示内容是否还在**；`contentPurged = true` 的槽位必须渲染为"内容已按保留策略清除"，不得给出一个点下去就 410 的下载链接。三个字段都可为空（该步骤尚无产物）；有真实报告产物时响应必须仍是合法对象，序列化不得失败。理由：把 report/importReport 声明成裸 id 曾使有真实执行报告的生产环境对任何身份都 500。
- **CP-UI-025**【新增】（S5IF-INP08、INP09）S1 必须提供模型调用日志读取：`GET /projects/{project_id}/inputs/{artifact_id}/llm-log`（分页，默认 50、上限 200）返回该输入解析的调用摘要列表；`GET .../llm-log/{exchange_id}` 返回单次交换的请求消息与模型原始答复。读取须同时满足 `project.view` 与该产物自身的读取授权（RESTRICTED / PHI_PII 另需 grant）；响应不得包含凭证值；日志按 S3 的保留期由 `purge_llm_exchanges` 清理。
- **CP-UI-026**【新增】（S5IF-SET01）S1 必须提供 `GET /setup`，把冷启动所需事实一次回齐：`providers`（各端口 real/fakes）、`startup`（迁移模式、已应用/跳过的 schema、已初始化/缺失的提供方、登记计数、`authMode`）、`configuration`（providerMode、workflowEngine、migrationsMode、httpsOnly、authMode、`validate_runtime()` 的问题清单——只点变量名不写值）、`identity`（authMode、是否强制、M4 是否启用、用户数、是否待改口令）、`environments`（总数、在用数、非生产在用数、`neverTested[]`、逐项）、`intelligence`、`projects`、`credentials`、`engine`、`generatedAt`。它**只回事实不回判断**：可用性仍只来自 `allowed-actions`；权限不足的小节回 `null` 而不是 0；读取权与平台 `allowed-actions` 一致（`project.view`），一次回答披露的信息不得多于 `GET /environments` + `GET /projects` 已各自披露的。

### 3.13 凭证保管（CP-SEC，契约 1.7.0）

- **CP-SEC-001**【新增】（FR-SEC-006、S5IF-SEC01）S1 可以保管操作者经界面录入的凭证，但只能以**经认证加密**的形式：AES-256-GCM，密钥由部署主密钥 `FACTORY_CREDENTIAL_MASTER_KEY` 经 HKDF-SHA256 与每记录独立 salt 派生，AAD 绑定凭证引用名（把一行的密文复制到另一行必须解不开）。明文禁止成为任何列、任何返回值、任何审计字段、任何日志行。主密钥缺失或长度不足（少于 32 字符）时该后端不注册、录入端点返回 503 `SECRET_UNAVAILABLE`（`details.setting` 点名的那个变量、`details.reason = "storeDisabled"`），服务照常启动；解析失败按 `details.reason` 区分 `missing` / `keyMismatch` / `corrupt` / `readOnly`，不得只写"数据损坏"。
  保护范围必须写清：密文与主密钥在同一台机器上，因此这一层防的是业务库的读取面（备份、副本、`pg_dump`、能 `SELECT` 的人），**不防已取得主机与部署环境变量的攻击者**；需要后者仍须使用外部密钥管理（`vault://` 等引用格式保持兼容）。
- **CP-SEC-002**【新增】（S5IF-SEC02、FD-SEC-003）凭证的读取只有一条路径：`SecretResolver` 按 `db://名字` 解析，返回自掩码的 `Secret` 对象（`str`/`repr`/`for_json` 恒为 `***`，只有 `reveal()` 给值）。该后端**必须只读**：`store_secret()` 在调用方省略 scheme 时推断"唯一可写后端"，多于一个即 `SecretStoreUnsupportedError`，而 FD-SEC-004 的一次性演示凭证正依赖该推断。写入走 S1 自己的服务与端点，不走解析器。
  解析失败按引用报告，绝不报告值：主密钥与密文不匹配时 `details.reason = "keyMismatch"`（备份回滚或换密钥是两种不同的运维事实，不能都写成"数据损坏"），且只尝试一次——不得遍历候选密钥，那会造出一个解密试探预言机。
- **CP-SEC-003**【新增】（S5IF-SEC03、FR-SEC-008）删除一个仍被引用的凭证必须返回 409 `SECRET_IN_USE`，`details.referencedBy` 列出引用者（环境 × 作用域、提供方、模型）；被引用关系通过既有端口读取，S1 不另存一份索引。理由：删掉正在被引用的凭证不是清理，而是把一次正常的 DHIS2 调用变成将来某刻难以归因的失败——与 CP-PRJ-007 对"归档后先导出才能删"用的是同一个两步逻辑。
- **CP-SEC-004**【新增】（FR-SEC-008）承载凭证的请求体字段在校验失败时不得被回显：422 摘要若含客户端提交的原值，等于把密钥抄进客户端与代理日志，因此服务端必须在渲染前抹掉该字段的输入值，逐字段错误只给 `path` 与原因。被抹掉的范围包括错类型提交时包在 dict / list 里的那些值——pydantic 把它们原样写进摘要（值在每个错误的 `input` 键上，不是 `ctx.input`）。凭证端点的响应不得进入幂等响应回放表（回放表逐字存响应体，而这里的响应体正是那个长期有效的引用），因此该端点**也不声明 `Idempotency-Key`**：一次会被接受、被记录、然后被忽略的头，只会让客户端以为它有作用；重复提交的安全性来自 `PUT` 这个动作本身。响应里同样不得出现任何**由密钥派生**的检查值：行上的密钥指纹只留在库里，因为每次加密取独立 salt，它标识的是一个密文块而不是一把主密钥，既答不出"这批凭证是不是同一把密钥下写的"，就只是给离线试猜多一个靶子（判定"引用还能不能解开"是 `/setup` 的 `credentials.unresolvable` 的职责，它真的去解密）。


---

## 4. 业务规则汇总

| 编号 | 规则 | 来源 |
| --- | --- | --- |
| CP-BR-01 | 只有 S1 可以改变工作流、阶段、Run、Gate、Question、CR 的状态 | 【直引】FR-WF-001 |
| CP-BR-02 | 同一工作流同时只有一个 RUNNING 阶段 | 【直引】FR-WF-002 |
| CP-BR-03 | 审批绑定内容哈希，绑定对象变化即失效 | 【直引】FR-GATE-001 |
| CP-BR-04 | COMMIT 前后端完整复核；三处载荷哈希一致 | 【直引】FR-EXE-004/006 |
| CP-BR-05 | PRODUCTION：先在非生产验证、时间窗、未锁定、备份确认；多人审批与职责分离在启用认证时执行 | 【调整】FR-GATE-005 |
| CP-BR-06 | 存在 HIGH 级 OPEN Question 时设计 Gate 不能批准 | 【调整】FR-REQ-007 |
| CP-BR-07 | 全部 WARNING 逐条处理后才能批准 | 【直引】FR-GATE-004 |
| CP-BR-08 | 拒绝必须填写理由；重新提交须基于不同的绑定哈希 | 【调整】FR-GATE-003/007 |
| CP-BR-09 | ARCHITECTURE_GATE 通过后映射与 Registry 只能经 CR 修改 | 【调整】FR-MAP-004 |
| CP-BR-10 | 项目删除仅限已归档项目，且先生成导出 | 【直引】FR-DATA-006 |
| CP-BR-11 | 交付模式只能由 A 升级为 B | 【调整】2.5 |
| CP-BR-12 | 同一项目同一环境同时只有一个进行中的 Run；同一项目同时只有一个进行中的 CR | 【新增】 |
| CP-BR-13 | 已完成 Run 不可改写，版本更新后标记 OUTDATED | 【新增】 |
| CP-BR-14 | 状态变更、事件流记录与审计同一事务提交 | 【调整】NFR-REL-005 |
| CP-BR-15 | 改变状态的请求必须声明操作者 | 【新增】D-16 |
| CP-BR-16 | `authMode=off` 时授权恒允许、声明头即身份；启用认证后按角色与项目成员判定，未解析到活动账号即拒绝 | 【调整】CP-IAM-001 |
| CP-BR-17 | 多人审批是"人的数量"：同一人不得重复审批；执行该 Run 的人不得审批其 DEPLOY_GATE；两项仅在启用认证时可判定 | 【调整】CP-GATE-012 |
| CP-BR-18 | 只有当前版本 Registry 中处置为 DEPRECATED 的 businessKey 才能被 CR 声明为删除候选 | 【新增】CP-CR-005 |
| CP-BR-19 | 上传必须通过类型/大小检查与结构性内容扫描，命中即 422 | 【调整】CP-API-009 |
| CP-BR-20 | `codePrefix` 只在创建项目时生效，创建后不可修改；S1 不落库 | 【新增】CP-PRJ-010 |

---

## 5. 数据需求（schema `cp`）

全部实体含创建与更新时间，略。

| 实体 | 最少字段 | 来源 |
| --- | --- | --- |
| Project | id、name、description、ownerName、milestoneStatus（推导值）、deliveryMode、projectType、versionConstraints、defaultLanguage、translationLanguages、archived、archivedAt、deletedAt、currentBlueprintVersion（S2 版本号引用） | 【调整】23.1、D-15 |
| ProjectTargetEnvironment | projectId、environmentKey（S4 引用）、promotionOrder、isDesignReference | 【新增】D-01 |
| ProjectSetting | projectId、gateReminderHours、notificationEmails、notificationWebhookRef（Secret 引用） | 【新增】CP-NTF-002 |
| Workflow | id、projectId、status、paused、cancelled、currentStageId、version（乐观并发）、lastEventSeq（本工作流最近分配的事件序号，事务内加行锁递增） | 【调整】23.1、CP-EVT-001 |
| Stage | id、workflowId、deploymentRunId（可空）、stageType、status、attempt、inputBlueprintRef、outputArtifactIds、pageScope、failureSourceStage、feedback、startedAt、completedAt；同一工作流至多一个 RUNNING 阶段由部分唯一索引保证 | 【调整】23.1、CP-WF-006 |
| StageRun | stageId、attempt、agentRunIds / executionIds / buildIds、idempotencyKey、result（含基线编译的 `blueprintVersion`、漂移重检的 `driftVerdict`、解析结果） | 【新增】CP-WF-013、CP-UI-013 |
| DeploymentRun | id、workflowId、environmentKey、blueprintVersion、status、outdated、changeRequestId、version、validatePayloadHash、validateOperator、commitOperator、executionId、startedAt、completedAt；漂移结论存于 BIND_COMPILE 的 StageRun.result，不在本表 | 【新增】D-01、CP-UI-013 |
| InputParseRun | id、projectId、workflowId、stageId、inputArtifactId、status（`PARSING` / `PARSED` / `NEEDS_REVIEW` / `FAILED` / `POLICY_DENIED`）、idempotencyKey、options、operator、parseId、stepRunId、nrrArtifactId、parserType、parserVersion、usedModel、errorCode、errorMessage、startedAt、finishedAt；同一输入至多一个 `PARSING` 行 | 【新增】CP-UI-007 |
| Question | id、projectId、workflowId、stageId、sourceKind、agentRunId、sourceLocalId、text、severity、**answerMode（`SINGLE` / `MULTIPLE`）**、affects、suggestions、blocking、status、answer（含 suggestionId(s)/suggestionText(s)/customText/text）、answeredBy、answeredAt；（agentRunId、sourceLocalId）唯一 | 【调整】23.1、CP-Q-002/004、迁移 0005 |
| Gate | id、workflowId、deploymentRunId、stageId、type、environmentKey、blueprintRef、boundRefs、warnings、riskSummaryRef、requiredApprovals、status、invalidatedReason、reminderCount | 【调整】23.1 |
| Approval | id、gateId、approverName、approverUserId（off 时为空）、approverRoles（批准当时持有的角色）、decision、comment、warningDispositions、confirmations、seenBoundHashes | 【调整】23.1、CP-GATE-007/012 |
| ChangeRequest | id、projectId、reason、affectedBusinessKeys、deletionCandidates、requestedBy、status、impactReportArtifactId、affectedStages、approvedBy、rejectedReason、resultBlueprintVersion；同一项目至多一个 IN_PROGRESS（部分唯一索引） | 【调整】23.1、CP-CR-005、迁移 0001 |
| EventStreamRecord | workflowId、sequence、eventType、occurredAt、payload、expiresAt | 【新增】CP-EVT-001 |
| Notification | id、projectId（可空，平台级为空）、recipientUserId（个人通知，项目级为空）、type、objectRef、message、read | 【调整】23.1、CP-NTF-001 |
| NotificationDelivery | notificationId、channel、target、attempts、status、lastError、nextAttemptAt | 【新增】CP-NTF-002 |
| SystemSetting | key、value（JSONB）、updatedBy | 【新增】CP-SET-001 |
| IdempotentResponse | id、scope、idempotencyKey、statusCode、contentType、body、createdAt、expiresAt（由 S0 幂等记录的 resultRef 引用） | 【新增】CP-API-003：S0 幂等存储只保存结果引用，重放响应需由 S1 保存 |
| User | id、subject、source（local / oidc）、displayName、email、passwordHash（scrypt，仅本地账号）、passwordAlgo、status（ACTIVE / DISABLED）、failedAttempts、lockedUntil、lastLoginAt、mustChangePassword；（source、subject）唯一 | 【新增】CP-IAM-002、迁移 0002 |
| UserRole | id、userId、role、projectId（空为组织级）、grantedBy、grantedAt；（userId、role、projectId）唯一 | 【新增】CP-IAM-002 |
| ProjectMember | id、projectId、userId、role（项目内角色，可空）、addedBy；（projectId、userId）唯一 | 【新增】CP-IAM-002 |
| Session | id、tokenHash（不存令牌原文）、userId、csrfTokenHash、authSource、lastSeenAt、expiresAt、absoluteExpiresAt、revokedAt | 【新增】CP-IAM-004 |
| RestrictedGrant | id、projectId、userId、classification、grantedBy、reason、expiresAt、revokedAt；（projectId、userId、classification）唯一 | 【新增】CP-IAM-002 |
| ProductionExemption | id、projectId、environmentKey（空=全项目）、waiver（`NON_PRODUCTION_VERIFIED`）、reason、grantedBy、expiresAt、revokedAt | 【新增】CP-WF-020 |
| SecretRecord | id、name（唯一）、ciphertext、algo、keyFingerprint（只在库内）、createdBy、rotatedAt；值不是列 | 【新增】CP-SEC-001、迁移 0003 |

`codePrefix` 不落 `cp` 库：它随项目创建请求交给 S2，只影响 S2 派生的受管对象 code（CP-PRJ-010）。

数据管理：
- **CP-DATA-01**【直引】（FR-DATA-003）`cp` schema 变更通过迁移脚本管理。
- **CP-DATA-02**【直引】（NFR-CAP-004、NFR-CAP-005）支持至少 50 个并发活跃项目；历史记录全部可查询。
- **CP-DATA-03**【直引】（NFR-REL-007）`cp` schema 与 Temporal 数据 RPO ≤ 15 分钟、RTO ≤ 4 小时，每季度演练备份恢复。

---

## 6. 流程需求

### 6.1 输入上传

1. 用户上传 → 调用 S0 暂存内容（流式、回读校验哈希）→ S1 做文件类型与大小校验、结构性内容扫描（CP-API-009）→ 在工作单元内登记 BUSINESS_INPUT 产物并写审计。
2. 调用 S3 解析该输入（后台线程，`cp.input_parse_runs` 记录 `PARSING`）→ 202 受理；解析结果展示于 REQUIREMENTS 阶段。重复解析以调用方的 `Idempotency-Key` 参与幂等键，修好提供方后重试真的会重跑（CP-UI-007）。

### 6.2 设计阶段（以 MAPPING 为例）

1. 用户运行 → 校验 READY → 阶段 RUNNING → 活动中调用 S3 启动 Agent（传入幂等键、进度回调、取消令牌、项目默认语言或 `Accept-Language` 指定的界面语言）。
2. S3 返回 WAITING_INPUT 与 Question → S1 保存 Question（含 `answerMode` 与推荐建议），阶段 WAITING_INPUT → 用户逐题回答，或经 `POST /questions/answers` 一次提交整份问卷 → 全部阻塞问题回答后最多恢复一次运行。
3. S3 返回 COMPLETED（或用户以人工编写方式提交审批）→ S1 调用 S2 冻结草稿（传入项目全部目标环境）→ 冻结存在 BLOCKING 时阶段 BLOCKED；否则以冻结哈希创建 ARCHITECTURE_GATE → 阶段 WAITING_APPROVAL → 通知。
4. 等待期间用户经 S1 追加人工 Patch → 草稿哈希变化 → Gate INVALIDATED → 用户重新提交审批。人工 Patch 的幂等键以调用方的 `Idempotency-Key` 为准，提交前从设计基准环境补池（CP-UI-018）。
5. 批准 → CP-GATE-014 单一工作单元完成版本提升与失效传播 → 下一阶段 READY。若启用认证，审批人按环境类别精确动作、`operatorRole`、人数与职责分离校验（CP-GATE-012）。

### 6.3 Deployment Run

1. 创建 Run（校验设计主线、环境登记、最近连接测试通过、无进行中 Run）→ R1：调用 S4 探测与发现 → 调用 S2 环境绑定编译；该项目若有 IN_PROGRESS 的 CR，把 CR ID 与其删除候选一并交给编译。
2. R2：调用 S2 校验 → PACKAGE_GATE → 批准。
3. R3：调用 S4 VALIDATE 流程（执行计划、漂移、Snapshot、VALIDATE）→ 创建 DEPLOY_GATE；所需审批数取 S4 保护属性。
4. 审批时调用 S4 漂移检查；检测到漂移 → DEPLOY_GATE INVALIDATED → R3 回到 READY。用户也可显式触发漂移重检（`POST /deployment-runs/{id}/drift-check`）。
5. 用户请求 COMMIT（幂等键）→ CP-GATE-017 复核（含多人审批与职责分离）→ 活动中调用 S4 COMMIT → 结果无法确认 → MANUAL_CHECK。
6. R5 → R6（如适用）→ Run VERIFIED → 更新里程碑。

### 6.4 Change Request

1. 创建 CR（DRAFT，可带 `deletionCandidates`；S1 当场校验其均为当前版本的 DEPRECATED 条目）→ 影响分析 → CHANGE_IMPACT_REPORT → ANALYZED。
2. 批准（`change_request.approve`，启用认证时需 SOLUTION_ARCHITECT）→ 开始执行 → 从最早受影响阶段 INVALIDATED 开始重走 → 新版本上 Run VERIFIED → COMPLETED。

### 6.5 项目导出与导入

见 CP-PRJ-008、CP-PRJ-009；导出以 Temporal 活动异步执行，完成后以 PROJECT_EXPORT 产物提供下载。删除项目必须已有该产物（CP-PRJ-007）。

### 6.6 定向删除（CR 授权之后）

1. CR 声明删除候选 → 批准并 `start` → R1 环境绑定编译携带 CR ID 与候选，S2 产出可提交的删除载荷（含 `deletionCandidates` 与 UID）。
2. 操作员调用 `POST /environments/{environment_key}/delete-objects`，提交 `{projectId, deletionArtifactId, expectedHash}`，并携带 `Idempotency-Key`。
3. S1 校验载荷归属与服务状态 → S4 重跑关联数据检查、遵守 `deletionPolicy` 与紧急锁定、整批提交或整批拒绝 → S1 投影 `ObjectDeletionView`；后续经 `GET /executions/{id}` 与 SSE 观察。
4. 删除不是工作流步骤，不产生 `stageId`/`stepRunId`，也不写 S1 自己的删除审计（S4 记录 `delete.executed`）。

### 6.7 整页问卷与下一轮

1. 阶段 WAITING_INPUT，界面读取该阶段全部 OPEN Question（含 `answerMode` 与推荐建议）。
2. 一次提交 `answers[]` → S1 单事务记录全部答案 → 若已无阻塞问题则最多 resume 一次。
3. 响应返回恢复后仍 OPEN 的问题（第二轮问题已带 attempt 前缀的本地 ID），界面直接渲染下一轮；已答问题不可修改（CP-Q-007）。

---

## 7. 跨子系统调用需求

### 7.1 S1 对 S0 的使用

工作单元；产物暂存、登记、查询、状态变更、来源查询、清除、通用 Diff、打包与解包；审计写入与查询、链接与校验；幂等存储；Secret 解析（通知渠道密钥）；错误模型；ID、哈希、时钟；操作上下文、日志与追踪；进度与取消协议（S1 实现 `ProgressReporter`）；迁移运行器。

### 7.2 S1 对 S2 / S3 / S4 公共 API 的需求

以下为 S1 的需求清单，具体签名由提供方 SRS 定义。

| 提供方 | S1 需要的能力 | 关键返回信息 |
| --- | --- | --- |
| S2 | 依赖图读取；开发包文件清单与读取；分节目录与分节 Schema；版本列表；按范围读取条目（含草稿）；按项目查询活动草稿；Registry（多环境绑定）、环境绑定（含观测基准与处置）、Managed Scope、UID 池状态与**补充 UID 池**；决策列表；版本间与草稿相对基准版本的 Diff；问题查询（统一 Issue 结构）；设置环境绑定；为项目补充 UID 池（运行 MAPPING、PAGE_DESIGN 阶段、人工 Patch 与执行 CR 前，传入设计基准环境）；初始化 Blueprint（含 `codePrefix`）；读取当前版本与草稿元数据（交付模式、Fixture 规格有无、草稿哈希 / 可提升性）；提交人工 Patch；提升草稿为版本；交付模式变更；影响分析；基线编译；环境绑定编译（环境、DISCOVERY_RESULT、CAPABILITY_PROFILE、CR ID 与删除候选）；校验；读取决策（Gate 说明用）；导出与导入 Blueprint；复现；开发包生成与完整性检查；项目数据清理 | 新版本号、变更级别、受影响阶段集合；产物 ID；每个问题的 ID、级别与 `sourceStage`；Patch 冲突时的合并问题；Registry 条目的处置与 UID |
| S3 | 解析记录查询；结构化列映射提案；模型调用日志（摘要与单次交换）；NRR 查询；有类型的 Governance 函数（提供方含**连通性探测**、模型含**差量编辑**、Prompt 与生效、回归记录读写、组织策略与放宽、有效策略计算、评测含逐例结果、用量、预算读写、Agent 目录）；运行查询（含 `RUNNING`、拒绝原因与最近进度）；解析输入；启动 Agent 运行（阶段、页面范围、草稿 ID、设计基准环境键、项目全部目标环境的数据外发策略与上限、所需产物 ID、**界面语言 `language`**、幂等键、进度、取消）；阶段内对话；问题解释；以回答恢复运行；取消运行；查询运行；Governance 管理（供公共 API 转发）；项目数据清理；模型调用日志清理 | 运行状态（COMPLETED / WAITING_INPUT / FAILED / POLICY_DENIED / BUDGET_EXCEEDED）、Question 列表（含 `answerMode` 与推荐建议）、被拒绝产物、预算状态、提供方健康结论 |
| S4 | 环境管理与查询（含凭证记录）；保护属性（含时区、删除策略、锁定原因与变更人、`requiredApprovals`、`operatorRole`、`llmDataPolicy` 上限）；最近一次连接测试结果、最新能力档案索引、发现结果列表、发现对象检索（均不访问 DHIS2）；环境存在性与保护属性查询（类别、时间窗、紧急锁定、数据外发策略）；执行结果对账与人工核实结果回写（释放环境锁）；环境绑定编译前的发现需传入由 S2 Registry 得到的明确对象清单；环境管理（供公共 API 转发）；连接测试；能力探测与新鲜度检查；发现；VALIDATE 流程；COMMIT；**定向删除 `delete_objects`**；按幂等键查询执行；部署后验证；Fixture 导入；漂移检查；即将到期凭证查询；项目数据清理 | 产物 ID（计划、Snapshot、ImportReport、执行报告）；漂移结果；执行状态（含无法确认）；每个问题的 `sourceStage`；删除执行结果（`ExecutionOutcome`） |

---

## 8. 接口需求

### 8.1 公共 REST API（/api/v1）

路径中 `{id}` 为对应资源的 ID。里程碑列按 S5 优先级映射（P0 → M1、P1 → M2、P2 → M3）。

| 分组 | 端点 | 需求 | 里程碑 |
| --- | --- | --- | --- |
| 项目 | `POST /projects` · `GET /projects` · `GET/PATCH /projects/{id}` · `POST /projects/{id}/archive` · `DELETE /projects/{id}` · `GET /projects/{id}/export`（202）· `POST /projects/import`（202）· `POST /projects/{id}/upgrade-mode` · `GET/PUT /projects/{id}/settings` | CP-PRJ | M1～M3 |
| 项目有效策略 | `GET /projects/{id}/effective-policy` | CP-UI-006 | M1 |
| 目标环境 | `GET/PUT /projects/{id}/target-environments` | CP-PRJ-002～004 | M1 |
| 输入产物 | `POST /projects/{id}/inputs` · `GET /projects/{id}/inputs`（含解析状态）· `POST /projects/{id}/inputs/{artifactId}/parse`（202，含 `options`）· `GET /projects/{id}/inputs/{artifactId}/column-mapping` · `GET /projects/{id}/inputs/{artifactId}/llm-log` · `GET /projects/{id}/inputs/{artifactId}/llm-log/{exchangeId}` | CP-API-009、CP-UI-007、CP-UI-025 | M1 / M2 |
| 工作流 | `GET /projects/{id}/workflow` · `GET /workflows/{id}`（含活动草稿 ID）· `POST /workflows/{id}/pause` · `/resume` · `/cancel` · `GET /workflows/{id}/allowed-actions` | CP-WF、CP-UI-002、CP-UI-019 | M1 |
| 阶段 | `GET /workflows/{id}/stages`（含聚合字段）· `GET /stages/{id}` · `POST /stages/{id}/run`（202）· `/rerun`（202）· `/submit-for-approval` · `/resolve-manual-check` · `POST /stages/{id}/patches` · `GET/POST/DELETE /stages/{id}/chat` | CP-WF、CP-UI-008/009/018、CP-UI-025 | M1 / M2 |
| Agent 运行 | `GET /workflows/{id}/agent-runs/{runId}` · `POST /workflows/{id}/agent-runs/{runId}/cancel` | CP-UI-007 | M1 |
| Deployment Run | `POST /workflows/{id}/deployment-runs` · `GET /workflows/{id}/deployment-runs` · `GET /deployment-runs`（跨项目）· `GET /deployment-runs/{id}` · `GET /deployment-runs/{id}/summary` · `POST /deployment-runs/{id}/drift-check`（202）· `POST /deployment-runs/{id}/commit`（202，`Idempotency-Key` 必需）· `POST /deployment-runs/{id}/cancel` | CP-WF-018～022、CP-UI-011/012/013、CP-API-014 | M1 / M2 |
| 环境绑定 | `GET /deployment-runs/{id}/environment-bindings` · `PUT /deployment-runs/{id}/environment-bindings/{businessKey}` · `GET /deployment-runs/{id}/environment-bindings/{businessKey}/candidates` | CP-UI-002、CP-UI-005 | M2 |
| 执行 | `GET /executions/{id}` · `GET /executions/{id}/report` · `POST /executions/{id}/reconcile` | CP-WF-008、CP-GATE-018 | M2 |
| Question | `GET /questions`（跨项目）· `GET /workflows/{id}/questions` · `GET /questions/{id}` · `POST /questions/{id}/answer` · `POST /questions/answers`（整页问卷，CP-Q-008）· `POST /stages/{id}/questions` | CP-Q、CP-UI-015 | M1 |
| Gate | `GET /gates`（跨项目）· `GET /workflows/{id}/gates` · `GET /gates/{id}` · `GET /gates/{id}/explanation` · `POST /gates/{id}/approve` · `POST /gates/{id}/reject` | CP-GATE、CP-UI-010、CP-UI-014 | M1 |
| 变更请求 | `POST /projects/{id}/change-requests` · `GET /projects/{id}/change-requests` · `GET /change-requests/{id}` · `POST /change-requests/{id}/impact` · `/approve` · `/reject` · `/start` · `/complete` · `/cancel` | CP-CR | M2 |
| 产物 | `GET /workflows/{id}/artifacts` · `GET /artifacts/{id}` · `GET /artifacts/{id}/content`（`?download=true`）· `GET /artifacts/{id}/diff?against=` · `GET /artifacts/{id}/lineage` · `POST /artifacts/{id}/reproduce` · `POST /artifacts/{id}/explain` · `POST /artifacts/{id}/purge` | CP-UI-016、CP-UI-007 | M1 / M2 |
| 校验问题 | `GET /workflows/{id}/validation-issues?runId=&level=&sourceStage=&ruleId=&businessKey=` | CP-UI-004 | M2 |
| Blueprint 与 Schema | `GET /projects/{id}/blueprint/section-schemas` · `GET /projects/{id}/blueprint/section-schemas/{section}` · `GET /projects/{id}/blueprint/versions` · `GET /projects/{id}/blueprint/entries` · `GET /projects/{id}/blueprint/draft` · `GET /projects/{id}/blueprint/diff?from=&to=` 或 `?draft=true` · `GET /projects/{id}/decisions` · `GET /workflows/{id}/nrr` | CP-UI-002、CP-UI-003 | M1 / M2 |
| Registry | `GET /projects/{id}/registry` · `GET /projects/{id}/registry/managed-scope` · `GET /projects/{id}/uid-pool` · `POST /projects/{id}/uid-pool/replenish`（从设计基准环境补池） | CP-UI-002、CP-UI-018 | M2 |
| 依赖图 | `GET /workflows/{id}/object-graph?runId=&blueprintVersion=&dhis2Type=&businessKey=` · `GET /projects/{id}/object-graph`（兼容别名，等价于按项目当前构建读取） | CP-UI-020 | M2 |
| 开发包文件 | `GET /projects/{id}/frontend-package/files` · `GET /projects/{id}/frontend-package/files/{path}`（`?download=true`）· `GET /projects/{id}/package-files`（某次构建的元数据包文件清单） | CP-UI-021、CP-UI-024 | 本期 |
| 成员与授权 | `GET/POST /projects/{id}/members` · `PUT /projects/{id}/members` · `DELETE /projects/{id}/members/{userId}` · `GET/POST /projects/{id}/artifact-grants` · `DELETE /projects/{id}/artifact-grants/{userId}` · `GET/POST /projects/{id}/production-exemptions` · `DELETE /production-exemptions/{id}` | CP-UI-023、CP-IAM-002 | 本期 |
| 环境 | `GET /environments` · `POST /environments` · `GET /environments/expiring-credentials` · `GET /environments/{key}` · `PATCH /environments/{key}` · `POST /environments/{key}/disable` · `GET /environments/{key}/protection` · `POST /environments/{key}/emergency-lock` · `POST /environments/{key}/test-connection` · `GET /environments/{key}/capability-profile` · `POST /environments/{key}/probe-capabilities` · `GET /environments/{key}/discovery-results` · `POST /environments/{key}/delete-objects`（定向删除，`Idempotency-Key` 必填） | CP-UI-001、CP-CR-008 | M1 / M2 |
| Governance | `GET /governance/provider-catalogue` · `GET/POST /governance/providers` · `PATCH /governance/providers/{name}` · `POST /governance/providers/{name}/test-connection` · `GET/POST /governance/models` · `PATCH /governance/models/{ref}`（**差量编辑**，契约 1.15.0：没点名的列不动，空差量与未声明字段各 422，审计记下 `before`；授权复用 `governance.model.enable`，与提供方 PATCH 复用 `provider.enable` 同一手） · `GET /governance/prompts` · `GET /governance/prompts/{name}/versions/{version}` · `GET/POST /governance/prompts/activations` · `GET/POST /governance/regression-records` · `GET /governance/policy` · `POST /governance/policy/relaxations` · `GET /governance/evaluations` · `GET /governance/evaluations/{evalRunId}` · `POST /governance/evaluations`（202）· `GET /governance/usage` · `GET/PUT /governance/budget` · `GET /governance/agents` | CP-UI-006、CP-API-017 | M1 / M2 / M3 |
| 审计 | `GET /projects/{id}/audit` · `GET /audit`（游标分页）· `POST /audit/verify` · `GET /audit/commit-trace/{executionId}` | FR-SEC-014 | M2 |
| 通知 | `GET /notifications?projectId=` · `POST /notifications/{id}/read` | CP-NTF | M1 |
| 事件流 | `GET /workflows/{id}/events`（SSE） | CP-EVT | M1 |
| 系统设置 | `GET/PUT /settings` | CP-SET、CP-UI-017 | M1 |
| 身份与权限 | `POST /auth/login` · `POST /auth/logout` · `GET /auth/session` · `GET /auth/oidc/request` · `POST /auth/oidc/exchange` · `GET/POST /users` · `PATCH /users/{id}` · `POST /users/{id}/password` · `GET/PUT/DELETE /users/{id}/roles` · `GET /roles` | CP-IAM-001～005、CP-UI-023 | `auth=off` 时仅回会话；`local/oidc/both` 本期交付 |
| 凭证与装机 | `GET/PUT/DELETE /secrets[/{name}]`（`PUT` 不含 `Idempotency-Key`；响应只回引用）· `GET /setup` | CP-SEC-001～004、S5IF-SET01 | 本期 |
| 平台 | `GET /allowed-actions`（平台级）· `GET /upload-limits` · `GET /healthz`（不含 `/api/v1` 前缀） | CP-WF-025、CP-API-009 | M1 |

Governance 端点中：提供方与模型的列表与登记、有效策略为 M1；启用、Prompt、生效、策略放宽、评测查询、用量为 M2；回归记录登记、评测发起、预算、Agent 目录为本期交付。

### 8.2 S1 对外提供的库 API

S1 是最上层编排者，不向 S0～S4 提供库 API（依赖规则禁止它们调用 S1）。S1 包对外只暴露运行时入口（ASGI 应用、Temporal worker 入口）与 `ProgressReporter` 实现。

### 8.3 统一错误响应

【调整】（19.2.1）字段：`errorCode`、`message`（按 `Accept-Language` 返回简体中文或英文）、`stage`、`artifactId`、`retryable`、`correlationId`、`details`。错误码在总体 SRS 列表基础上【新增】：`OPERATOR_REQUIRED`、`REQUEST_IN_PROGRESS`、`NOT_FOUND`、`INVALID_STATE`、`IDEMPOTENCY_CONFLICT`、`PROJECT_ARCHIVED`、`BUDGET_EXCEEDED`、`ENVIRONMENT_LOCKED`、`OUTSIDE_COMMIT_WINDOW`、`PRIOR_VERIFICATION_MISSING`、`UPLOAD_REJECTED`、`ARTIFACT_PURGED`（理由：S1 新增判定需要可区分的错误码）。提供方码的透出与归一规则见 CP-API-013。其中 `ENVIRONMENT_LOCKED` 的所有者是 S4（该条件由 S4 的 DX-ENV-007 定义），S1 只透出该码、不登记它；该码不可重试——紧急锁定是人工状态，重试不会自行解除。

【新增】（S5IF-G07）按码定义的 `details`：

| 错误码 | HTTP | `details` |
| --- | --- | --- |
| `OPERATOR_REQUIRED` | 400 | `{header: "X-Operator"}` |
| `INVALID_STATE` | 409 | `{currentState, requiredStates[], targetId}` |
| `IDEMPOTENCY_CONFLICT` | 422 | `{scope}` |
| `REQUEST_IN_PROGRESS` | 409 | `{scope}` |
| `APPROVAL_INVALIDATED` | 409 | `{gateId, mismatches[{refType, refId, expectedHash, actualHash}]}` |
| `ENVIRONMENT_LOCKED` | 409 | `{environmentKey, lockReason, lockChangedAt}` |
| `OUTSIDE_COMMIT_WINDOW` | 409 | `{environmentKey, windows[], environmentTimezone, currentTime}` |
| `UPLOAD_REJECTED` | 422 | `{reason: SIZE | TYPE, maxBytes, allowedTypes[]}` |
| `PRIOR_VERIFICATION_MISSING` | 409 | `{environmentKey, blueprintVersion, requiredEvidence[]}` |
| `VALIDATION_ERROR` | 422 | `{fields[{path, message}]}` |
| `ARTIFACT_PURGED` | 410 | `{artifactId}` |
| `NOT_FOUND` | 404 | `{reason: NOT_GENERATED | NOT_EXIST, requiredStage?, runId?}`（CP-UI-022） |
| `FEATURE_NOT_ENABLED` | 409 | `{feature, milestone}`（CP-UI-023） |

`details` 中永远不得出现凭证（CP-API-015）。

---

## 9. 非功能需求

| 编号 | 要求 | 来源 |
| --- | --- | --- |
| CP-NFR-01 | 服务重启不丢失任何工作流、阶段、Gate、Run、Question 状态 | 【直引】NFR-REL-001 |
| CP-NFR-02 | 月可用性 ≥ 99.5%（不含计划维护；本期起为高可用部署） | 【直引】NFR-REL-006 |
| CP-NFR-03 | 核心 API P95 < 500 ms；工作流与产物元数据查询 P95 < 300 ms（不含 LLM 与 DHIS2 调用） | 【直引】NFR-PERF-001/002 |
| CP-NFR-04 | 事件推送 P95 < 2 秒 | 【直引】NFR-PERF-005 |
| CP-NFR-05 | Agent、编译、执行在 `factory-worker` 中执行，不阻塞 API 请求 | 【直引】NFR-PERF-006 |
| CP-NFR-06 | 创建类、执行类请求幂等 | 【直引】NFR-CON-003 |
| CP-NFR-07 | 指标至少包括：工作流总数与失败数、阶段耗时、Gate 平均等待时长、事件推送延迟、活动重试次数 | 【裁剪】NFR-OBS-003 |
| CP-NFR-08 | 核心领域逻辑单元测试行覆盖率 ≥ 85%（状态机与 Gate 规则 ≥ 90%） | 【调整】NFR-MNT-004 |
| CP-NFR-09 | 错误消息支持简体中文与英文 | 【调整】NFR-UX-003 |

---

## 10. 跨系统假设

| 编号 | 假设 | 不成立时的影响 |
| --- | --- | --- |
| CP-ASM-01 | S2 的草稿提升、模式变更、导入 API 返回新版本号、变更级别与受影响阶段集合，且可加入调用方工作单元 | 无法同步失效传播 |
| CP-ASM-02 | S2、S4 的校验与导入报告中每个问题带 ID、级别与 `sourceStage` | 无法定位来源阶段与逐条处理 WARNING |
| CP-ASM-03 | S3、S4 的长任务 API 接受幂等键、`ProgressReporter`、`CancellationToken`，并声明为外部 I/O 函数 | 无法安全重试与展示进度 |
| CP-ASM-04 | S4 以幂等键持久化执行，并提供按幂等键查询 | 无法防止重复 COMMIT |
| CP-ASM-05 | S4 提供环境类别、时间窗、紧急锁定的实时查询与漂移检查 | 无法判定 DEPLOY_GATE |
| CP-ASM-06 | S3 在 Agent 结果中返回带（agentRunId、本地 ID）的 Question，并支持以回答恢复 | WAITING_INPUT 无法恢复 |
| CP-ASM-07 | S2、S3、S4 提供按项目的数据清理 API | 项目删除不完整 |
| CP-ASM-08 | S0～S4 均提供测试替身（Fake） | S1 无法独立开发与测试 |
| CP-ASM-09 | S2 提供分节目录、活动草稿查询、多环境绑定读取（含观测基准）、决策列表、草稿 Diff 与问题查询；S3 提供解析记录、列映射提案、NRR 查询、有类型的 Governance 函数与含 `RUNNING` 的运行查询；S4 提供最近连接测试结果、最新能力档案索引、发现结果列表与发现对象检索（均不访问 DHIS2） | 相应端点只能以测试替身联调，或无法满足 CP-UI-012 的“零 DHIS2 调用”约束 |

---

## 11. 验收标准

| 编号 | 给定 / 当 | 则 | 里程碑 | 来源 |
| --- | --- | --- | --- | --- |
| AC-WF-01 | 新建项目 | 返回全部阶段、当前阶段与下一步；模式 A 的 PAGE_DESIGN 与 HANDOFF 为 SKIPPED | M1 | 【直引】 |
| AC-WF-02 | Agent 运行期间 `factory-worker` 崩溃 | 恢复后以同一幂等键继续，不重复不可逆操作，无法确认的进入 MANUAL_CHECK | M1 | 【调整】 |
| AC-WF-03 | 产生 Major 级新版本 | MAPPING 及其后阶段 INVALIDATED，相关 Gate INVALIDATED | M1 | 【调整】 |
| AC-GATE-01 | 绑定对象哈希变化 | Gate INVALIDATED | M1 | 【直引】 |
| AC-GATE-02 | DEPLOY_GATE 未批准时调用 COMMIT | 409 `GATE_NOT_APPROVED`，未调用 S4 COMMIT | M1 | 【直引】 |
| AC-GATE-03 | 拒绝并填写理由 | 阶段 READY，理由显示为反馈，产生 GATE_REJECTED 通知 | M1 | 【调整】 |
| AC-GATE-04 | PRODUCTION 环境执行者本人作为审批人之一 | 审批被拒 | 本期 | 【直引】（本期实现） |
| AC-AUD-01 | 任一 COMMIT | 追溯查询返回环境、操作者、审批人、载荷与哈希、Blueprint 版本、Gate、执行记录、ImportReport | M2 | 【直引】 |
| AC-DATA-01 | 导出项目并导入另一部署 | Blueprint 与 Registry 完整恢复，重新编译哈希一致（与 S2 联合） | M2 | 【调整】 |
| AC-EXE-05 | COMMIT 期间网络中断后客户端重试 | 先按幂等键查询 S4，不产生重复提交 | M2 | 【调整】 |
| CP-AC-01 | 改变状态的请求缺少 `X-Operator` | 400 `OPERATOR_REQUIRED` | M1 | 【新增】 |
| CP-AC-02 | 同一 `Idempotency-Key` 不同请求体 | 422 `IDEMPOTENCY_CONFLICT` | M1 | 【新增】 |
| CP-AC-03 | SSE 断线后携带序号重连 | 补发缺失事件；序号过期时收到 `stream.reset` | M1 | 【新增】 |
| CP-AC-04 | 审批请求中的哈希与当前绑定哈希不一致 | 409 `APPROVAL_INVALIDATED` | M1 | 【新增】 |
| CP-AC-05 | 设计 Gate 批准时 S2 提升版本失败 | 审批、阶段、审计全部回滚，Gate 保持 WAITING | M1 | 【新增】 |
| CP-AC-06 | 已 VERIFIED 的 Run 所基于版本被新版本取代 | Run 标记 OUTDATED，历史记录不变 | M2 | 【新增】 |
| CP-AC-07 | PRODUCTION Run 无非生产 VERIFIED 时请求 COMMIT | 409 `PRIOR_VERIFICATION_MISSING` | M2 | 【新增】 |
| CP-AC-08 | DEPLOY_GATE 审批时 S4 检测到漂移 | Gate INVALIDATED，R3 回到 READY | M2 | 【新增】 |
| CP-AC-09 | 删除未归档项目 | 409 `INVALID_STATE` | M2 | 【新增】 |
| CP-AC-10 | 取消工作流时 S3 Agent 正在运行 | Agent 在安全点停止，阶段不推进 | M1 | 【新增】 |
| CP-AC-11 | 用 OpenAPI 客户端生成工具处理 S1 的 OpenAPI 文档 | 所有请求体与 2xx 响应体都有具名组件，无自由对象 | M1 | 【新增】S5IF-AC01 |
| CP-AC-12 | 遍历全部操作的响应 | 字段名无 snake_case（系统设置的键除外），枚举值为 UPPER_SNAKE | M1 | 【新增】S5IF-AC02、AC18 |
| CP-AC-13 | 以假环境启动后查询环境列表 | 返回 CP-UI-001 全部字段，含凭证到期时间，不含凭证值或掩码 | M1 | 【新增】S5IF-AC03 |
| CP-AC-14 | 为 PRODUCTION 环境的 discovery 与 import 配置同一 Secret 引用 | 422，错误码属 S1 目录，`details` 指明冲突范围 | M1 | 【新增】S5IF-AC04 |
| CP-AC-15 | 连接测试账户缺少元数据导入权限 | 逐项检查齐备，`missingAuthorities.import` 非空，结论为失败 | M1 | 【新增】S5IF-AC05 |
| CP-AC-16 | 紧急锁定开启时查询允许动作 | `execution.commit` 不可用，原因码 `ENVIRONMENT_LOCKED`，`targetId` 为环境键 | M1 | 【新增】S5IF-AC06 |
| CP-AC-17 | 读取 `mapping` 分节的 Schema | 返回自包含 Draft 2020-12 文档，可直接用于编辑器校验 | M1 | 【新增】S5IF-AC07 |
| CP-AC-18 | 未运行的阶段提交人工 Patch 后提交审批 | 阶段进入 WAITING_APPROVAL 并创建 Gate | M1 | 【新增】S5IF-AC08（回归 CP-WF-029） |
| CP-AC-19 | 查询工作流的阶段列表 | 每个阶段含 CP-UI-008 全部聚合字段，一次查询返回 | M1 | 【新增】S5IF-AC09 |
| CP-AC-20 | 打开 Dashboard | 单次跨项目 Gate 查询与单次跨项目 Run 查询即得全部数据 | M1 | 【新增】S5IF-AC10 |
| CP-AC-21 | 查询部署控制台聚合视图 | 返回 CP-UI-012 全部字段，P95 < 500 ms，请求期间对 DHIS2 的调用次数为 0；重检漂移须经漂移重检动作显式触发 | M2 | 【新增】S5IF-AC11 |
| CP-AC-22 | 以同一幂等键两次发起同一输入的解析 | 返回同一首次结果，不产生重复 Question | M2 | 【新增】S5IF-AC12 |
| CP-AC-23 | 查询未回答的 Question | `suggestions[]` 每项含 `id`、`text`、`rationale`、`changesUid`、`regenerates` | M1 | 【新增】S5IF-AC13 |
| CP-AC-24 | 批准 Gate 时提交过期的绑定哈希 | 409 `APPROVAL_INVALIDATED`，`details` 含 `gateId` 与 `mismatches[]` | M1 | 【新增】S5IF-AC14（回归 CP-AC-04） |
| CP-AC-25 | 逐条对照 S5 需求追溯表 | 已交付端点的 `x-requirements` 含对应 `CP-*` 与 `S5IF-*` 编号 | M1 | 【新增】S5IF-AC15 |
| CP-AC-26 | 以 `download=true` 读取产物内容 | 返回附件与按规则生成的文件名；不带参数时内联；内容已清除时两种请求都返回 410 | M2 | 【新增】S5IF-AC16 |
| CP-AC-27 | COMMIT 请求缺少 `Idempotency-Key` 请求头 | 返回 400；请求体中不存在幂等键字段 | M2 | 【新增】S5IF-AC17 |
| CP-AC-28 | 连续读取三页审计记录 | 使用 `nextCursor` 翻页，不返回 `total` | M2 | 【新增】S5IF-AC19 |
| CP-AC-29 | 发起阶段运行 | 返回 202 与 `{stageId, stepRunId, accepted}`，不含 `engine` 字段 | M1 | 【新增】S5IF-AC20 |
| CP-AC-30 | 跨项目 Run 列表中的环境类别 | 取自 S4 实时查询或 ≤ 60 秒进程内缓存，S1 数据库中不存在该字段 | M2 | 【新增】CP-UI-011 |
| CP-AC-31 | 基线编译前读取依赖图 | 404 `NOT_FOUND`，`details.reason = NOT_GENERATED`、`requiredStage = BASELINE_COMPILE` | M2 | 【新增】CP-UI-022 |
| CP-AC-32 | `auth=off` 且未设 `FACTORY_M4_FEATURES_ENABLED=true` 时添加项目成员 | 409 `FEATURE_NOT_ENABLED`；`allowed-actions` 中该动作不可用。启用认证或显式打开 M4 开关后按角色与成员关系放行 | 本期 | 【新增】CP-UI-023 |
| CP-AC-33 | 分别以省略参数、`blueprintVersion`、`runId` 读取依赖图 | 分别对应当前版本基线构建、指定版本基线构建、该 Run 的环境绑定构建；响应与 S2 返回结果逐字段一致（仅命名投影），S1 中不存在自行计算图的代码路径 | M2 | 【新增】CP-UI-020 |
| CP-AC-34 | 同一阶段有 3 道 OPEN Question（1 道阻塞、1 道多选），一次 `POST /questions/answers` 提交全部答案 | 全部答案先在一个事务内落库，再最多 resume 一次；响应 `QuestionnaireAnswerView.questions[]` 只含恢复后仍 OPEN 的问题；重复 `questionId` → 422，跨阶段/工作流/项目 → 409；同 `Idempotency-Key` 重试不写第二份答案 | 本期 | 【新增】CP-Q-008、S5IF-FLD08 |
| CP-AC-35 | 同一阶段尝试内连续提交两次内容不同的手工 Patch，再原样重发第二次 | 两次不同内容都落为 PROPOSED，第二次不因 `patch:{stage}:{attempt}` 被钉死；原样重发同键同内容只生效一次；调用方给了 `Idempotency-Key` 时以该键为准 | 本期 | 【新增】CP-UI-018 |
| CP-AC-36 | 创建项目时给 `codePrefix=ACME`，随后 PATCH 该字段 | 创建成功，S2 初始 Blueprint 的 `generationPolicy.codePrefix=ACME`；`PATCH /projects/{id}` 携带 `codePrefix` → 409 `INVALID_STATE`；`ProjectView` 不回显该字段（S1 不落库） | 本期 | 【新增】CP-PRJ-010 |
| CP-AC-37 | UID 池耗尽后调用 `POST /projects/{id}/uid-pool/replenish` | 返回更新后的 `UidPoolView`；补池在事务外访问设计基准环境；手工 Patch 在补池失败或仍耗尽时回 409 `UID_POOL_EXHAUSTED`（不可重试），`allowed-actions` 给出补池入口 | 本期 | 【新增】CP-UI-018、S5IF-BP18 |
| CP-AC-38 | 对已登记但地址不可达的提供方调用 `POST /governance/providers/{name}/test-connection` | 返回 200 + `{ref, healthy:false, detail:"<传输层原因>"}`，不生成内容、不计费、响应与 SSE 中不出现密钥值/引用；替换 Test 数据库为真实 S3 后，未登记提供方当前回 404（已知偏差，见 CP-API-017） | 本期 | 【新增】CP-API-017、S5IF-GOV20 |
| CP-AC-39 | `PATCH /governance/models/{ref}` 只给 `unitPriceInputPer1k`；再分别发空差量与未知字段 | 只写单价一列，其余列与 `capabilities` 保持原值；响应为整行 `ModelView`；空差量 → 422、未知字段 → 422（`extra="forbid"`）；审计含 `before`/`after` 且标为编辑 | 本期 | 【新增】CP-UI-006、S5IF-GOV06 |
| CP-AC-40 | `POST /environments/{key}/delete-objects` 缺 `Idempotency-Key`；载荷产物不属于该项目；正常提交 | 缺头 → 400 `IDEMPOTENCY_KEY_REQUIRED`；产物不属于项目 → 404/422 并点名字段；正常提交同步 200 + `ObjectDeletionView`（拒绝也以 `status=FAILED, applied=false` 返回），不套 202 | 本期 | 【新增】CP-CR-008、S5IF-ENV13 |
| CP-AC-41 | 非平台管理员读取 `GET /projects` 与 `GET /deployment-runs` | 只返回该操作者是成员的项目及其 Run；不在成员关系中的项目按不存在处理（不泄露存在性）；平台管理员按角色目录可见全部在册项目 | 本期 | 【新增】CP-PRJ-003、FR-SEC-004 |
| CP-AC-42 | 停止 PostgreSQL 后分别请求 `/readyz` 与 `/healthz` | `/readyz` 返回 503 `{status:"not_ready", reason}`，`/healthz` 仍返回 200 的进程事实；`/readyz` 不出现在 OpenAPI，路径数仍为 147 | 本期 | 【新增】CP-RT-006 |

---

## 12. 里程碑分工

| 里程碑 | S1 交付内容 |
| --- | --- |
| M1 | API、SSE、项目（不含导出导入）、工作流与设计阶段编排（对接 S2 / S3 / S4 Fake）、Question、设计 Gate、DEPLOY_GATE 基础规则、通知、系统设置、定时任务、运行时组装；契约要求（CP-API-010～016）；界面支撑中 S5 标为 P0 的部分：环境端点、分节目录与 Schema、活动草稿、Agent 运行查询与取消、阶段聚合字段、跨项目 Gate 列表、部署控制台聚合视图的框架、Gate / Question / 设置定型、允许动作扩充、提供方与模型、有效策略 |
| M2（MVP） | Deployment Run 全流程、PACKAGE_GATE、漂移失效、MANUAL_CHECK、Change Request、导出与导入、COMMIT 追溯查询、PRODUCTION 先验证规则与时间窗、锁定、备份确认；界面支撑中 S5 标为 P1 的部分：输入解析与列映射、NRR、版本 / 条目 / Registry / Managed Scope / UID 池 / 决策 / Diff、环境绑定与候选对象、校验问题、跨项目 Run 列表、漂移重检、产物下载、问题解释、其余 Governance 查询与操作 |
| M3（本期一并交付） | HANDOFF 编排与 HANDOFF_GATE、交付模式升级；回归记录登记、评测发起、预算读取、Agent 目录 |
| M4（本期一并交付） | 身份与权限（CP-IAM）、多人审批与职责分离、生产豁免、高可用部署、备份恢复演练 |

> **本轮裁决（V1.3.0）**：里程碑分行仅为历史排期，M3／M4 的内容一律为本期必须交付；第 8 章各表的"里程碑"列同此解释。高可用部署与备份恢复演练属部署形态，不进界面，但仍在本期交付清单内。`【裁剪】` 是出处标记（00 文档 §0.3），与范围删减无关，保留不动。

---

## 13. 未决问题

无。

---

## 附录 A：阶段状态迁移表

| 当前状态 | 允许的目标状态 |
| --- | --- |
| LOCKED | READY、SKIPPED、INVALIDATED |
| READY | RUNNING、WAITING_APPROVAL（人工编写直接提交）、SKIPPED、INVALIDATED、LOCKED |
| RUNNING | WAITING_INPUT、WAITING_APPROVAL、PASSED、FAILED、BLOCKED、MANUAL_CHECK、INVALIDATED |
| WAITING_INPUT | RUNNING、FAILED、INVALIDATED |
| WAITING_APPROVAL | PASSED、READY、INVALIDATED |
| PASSED | RUNNING（重跑）、INVALIDATED |
| FAILED | READY、INVALIDATED |
| BLOCKED | READY、INVALIDATED |
| MANUAL_CHECK | PASSED、FAILED |
| INVALIDATED | READY、LOCKED |
| SKIPPED | LOCKED、READY（交付模式升级） |

触发说明：自动运行的阶段为 DISCOVERY、BASELINE_COMPILE、R1、R2、R3、R5、R6；FAILED / BLOCKED → READY 由用户重试或来源问题经新版本修正触发；MANUAL_CHECK → PASSED / FAILED 由用户核实后附说明标记。

## 附录 B：授权钩子动作名称（现行生效，非预留）

动作常量以 `factory_control/iam.py` 为唯一来源，以下按域分组：

- **项目与输入**：`project.create`、`project.view`、`project.edit`、`project.upgrade_mode`、`project.archive`、`project.delete`、`project.export`、`project.import`、`input.upload`、`input.parse`、`input.confirm_column_mapping`
- **设计与阶段**：`stage.run`、`stage.chat`、`patch.submit`、`question.answer`、`agent.cancel`、`registry_binding.set`、`uid_pool.replenish`
- **部署与执行**：`deployment.create`、`deployment_run.drift_check`、`execution.commit.<ENV_CLASS>`、`environment.create`、`environment.edit`、`environment.disable`、`environment.test_connection`、`environment.probe_capabilities`、`environment.emergency_lock`、`environment.manage`、`frontend_package.download`
- **审批与变更**：`gate.approve.<GATE_TYPE>`、`gate.approve.DEPLOY_GATE.<ENV_CLASS>`、`change_request.create`、`change_request.approve`
- **产物与审计**：`artifact.read`、`artifact.read.restricted`、`artifact.read.phi`、`artifact.download`、`artifact.explain`、`artifact.purge`、`audit.view`、`audit.verify`
- **治理与设置**：`governance.manage`、`governance.policy.relax`、`governance.model.register`、`governance.model.enable`、`governance.provider.register`、`governance.provider.enable`、`governance.prompt.activate`、`governance.regression.record`、`governance.evaluation.run`、`governance.budget.set`、`settings.manage`、`secret.manage`
- **身份与成员**：`user.manage`、`member.manage`、`restricted_grant.manage`、`production_exemption.manage`

以上名称同时用于 `allowed-actions`（CP-UI-019）。两条不单设动作的复用：`environment.delete_objects` 复用 `environment.manage`；提供方连通性探测 `provider.test_connection` 复用 `governance.provider.enable`（S5IF-GOV20）。`auth=off` 下全部动作恒允许；`local|oidc|both` 下由内置角色目录、项目成员关系与职责分离规则判定，身份与成员类动作不再有 `FEATURE_NOT_ENABLED` 的里程碑门。

## 附录 C：S1 产生的审计事件

`cp.project.created` / `updated` / `archived` / `exported` / `imported` / `deleted` / `environment_updated`、`cp.input.uploaded` / `parse_started` / `parsed`、`cp.artifact.purged` / `explained` / `grant_created` / `grant_revoked` / `read_restricted`、`cp.workflow.created` / `paused` / `resumed` / `cancelled` / `completed` / `updated`、`cp.stage.started` / `completed` / `invalidated` / `rerun` / `patch_submitted` / `manual_check_resolved`、`cp.question.created` / `answered` / `withdrawn` / `superseded`、`cp.gate.created` / `approved` / `rejected` / `invalidated`、`cp.deployment_run.created` / `completed` / `cancelled` / `outdated` / `committed` / `verified` / `drift_checked` / `failed`、`cp.agent_run.started` / `completed` / `cancelled`、`cp.validation.completed`、`cp.execution.started` / `completed` / `failed`、`cp.change_request.created` / `updated` / `analyzed` / `approved` / `rejected` / `started` / `completed` / `cancelled`、`cp.notification.created` / `read`、`cp.settings.updated`、`cp.schedule.completed`、`cp.registry.binding_set`、`cp.uid_pool.replenished`、`cp.environment.created` / `updated` / `disabled` / `lock_changed` / `connection_tested` / `capabilities_probed`、`cp.user.created` / `updated` / `disabled` / `password_set` / `role_changed`、`cp.project.member_added` / `member_removed`、`cp.auth.login` / `login_failed` / `logout`、`cp.production.exemption_granted` / `exemption_revoked`、`cp.secret.written` / `deleted`、`cp.governance.provider_registered` / `model_registered` / `prompt_activated` / `policy_relaxed` / `evaluation_run`。完整清单以 `registrations.S1_AUDIT_EVENT_TYPES` 为准；总体 SRS 21.4 中属于 S2、S3、S4 的审计事件由各自产生。

## 附录 D：源文档引用表

| # | 文档 | 类型 | 引用范围 | 处理方式 |
| --- | --- | --- | --- | --- |
| 1 | 总体 SRS V1.4.0 | 总体需求规格 | 2.5、3、4.2～4.3、7、16、18.1（Settings）、19.2、21.4、22.1～22.5、23、24、27.1 | 抽取改写 |
| 2 | 总体 SRS V1.4.0 | 总体需求规格 | 附录 A 术语表 | 全文引用 |
| 3 | 总体 SRS V1.4.0 | 总体需求规格 | 2.4、21.1（身份与权限） | 注释引用（本期已实装） |
| 4 | 00 总体架构与集成约定 V1.3.0 | 共享知识库 | 全文 | 全文引用 |
| 5 | S0 公共基础库 SRS V1.0.2 | 子系统需求规格 | 公共 API 概要 | 章节引用 |
