# S1 公共 REST API 补充需求（S5 接口缺口清单）

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | S1 公共 REST API 补充需求 |
| 文档版本 | 0.4.0（随契约 1.19.0 **反向校准**：147 条路径 / 172 个操作，142 个操作带 `S5IF-*` 追溯。新增 S5IF-GOV20（提供方测连接）、S5IF-INP08/INP09（输入解析的模型调用日志）、S5IF-BP18（UID 池补池）、S5IF-BP19（项目 `codePrefix`）、S5IF-ENV13（定向删除）、S5IF-FLD13（CR 删除候选）、S5IF-FLD14（清除回执）；S5IF-FLD08 改写为整页问卷（`POST /questions/answers`、`answerMode`、`recommended`）；S5IF-FLD05 换成 Run summary 的 `execution`／`snapshot` 槽位与 `contentPurged`；S5IF-BP05 补 `NOT_GENERATED` + `requiredStage`（CP-UI-022）。此前各版的路径数与契约版本号都是当轮快照，本版一律以 1.19.0 为准） |
| 提出方 | S5 Factory UI 开发组 |
| 适用读者 | S1 开发组（实现方）；架构组（裁决）；测试组（联调）；S2 / S3 / S4 开发组（第 9 章上游依赖） |
| 上位文档 | 总体 SRS V1.4.0；《00 总体架构与集成约定》V1.3.0；S1 SRS V1.4.0（尤其 §8.1、§8.3）；S5 SRS V1.5.0；S5-M SRS V1.5.0 |
| 需求编号前缀 | `S5IF-` |
| 来源标记 | 【直引】【调整】【新增】，定义见 00 文档 0.3 |

### 0.1 本文的定位

本文**不修改**任何已批准的 SRS，只做一件事：把 S5 的需求（`UI-*`）与 S1 **当前实际提供**的 REST 接口逐条比对，把差额写成可实施的接口要求交给 S1。

本文对 S1 的约束力来自上位文档已有的义务：S1 SRS §8.1 已把环境管理与 Governance 列为"供公共 API 转发"，并单列了"补充端点（S5 所需）"一行；00 文档 §5 规定 S5 只可调 S1。因此本文绝大多数条目是**提醒 S1 兑现已认领的义务**，少数条目（跨项目 Gate 列表、部署控制台聚合视图等）是【新增】，需要架构组裁决。

### 0.2 权威边界（本文不重新定义的内容）

本文**不修改、不重新规定**任何已在其他 SRS 中设计的内容。凡下列情形，本文只引用编号、指出"取不到数据"，不给出替代设计：

| 主题 | 权威所在 | 本文的态度 |
| --- | --- | --- |
| 转发资源的响应模型与字段命名 | S1 SRS §8.1"转发"行原文：**"资源模型由提供方公共 API 定义"** → 即 `S2/factory_blueprint/api`、`S3/factory_intelligence/api`、`S4/factory_dhis2/api` 的 Pydantic 模型 | **不定义**。第 4/5/6 章的字段表一律降级为"S5 需要显示的概念清单"，最终形状由提供方 DTO 决定 |
| 转发端点的路径与动词 | S1 自行设计（S1 SRS §8.1 只列了 6 条具体路径，其余以"分组＋能力"表述） | 本文给的是**建议路径**，标 `（建议）`，S1 可另行命名，只要语义与数据齐备 |
| Environment 字段、连接测试八项、凭证范围、时间窗格式、删除策略取值 | S4 SRS §3.1／§5／DX-ENV-*／DX-CON-*／DX-CAP-*；S4 正在按增量实现 | 只列"S5 要显示什么"与"现 S1 镜像 DTO 装不下什么"，不裁决字段名（S4 的 `api.py` 定稿后以其为准） |
| 分节 Schema 的内容、Registry 条目字段、绑定三元组、Diff 结构、Issue 结构 | S2 SRS 与 `S2/src/factory_blueprint/dto/` | 同上，只标注 S2 侧未导出／有丢字段的位置 |
| NRR 类别、解析状态取值、Governance 各 action 的返回内容 | S3 SRS 与 `S3/src/factory_intelligence/` | 同上 |
| SSE 事件、幂等、`X-Operator`、错误信封 | S1 SRS §3.1／§3.2／§8.3 —— **已实现** | 第 2 章确认无需改动 |

推论：**"代码里还没有"不等于"缺需求"**。S4 的 `api.py` 在 0.2.0 时只是占位（其 README 自述"完整 api 在增量 7 补全后替换本文件"），S2/S3 也在持续实现中——这类事实本文一律归入第 9 章"上游实现进度"，只标影响与判定日期，不作为需求提出，也不据此设计绕过方案。本文对 S1 的诉求只有两种：**(i)** 已规定的能力需要一个 REST 出口；**(ii)** SRS 未覆盖、但 S5 无法自行达成的数据聚合（第 11 章，**已于 2026-09-29 裁决**）。

### 0.3 核对口径

1. 以 `S1/docs/openapi.json`（**147 条路径 / 172 个操作**，其中 **142 个操作**的 `x-requirements` 含至少一个 `S5IF-*`；`S5/contract/openapi.json` 与 `S5M/contract/openapi.json` 两份随包副本与它逐字相同）与 `S1/src/factory_control/rest/routes/` 的 15 个路由模块逐一比对，两个集合一致——即 **OpenAPI 未遗漏已实现的路由**。本文第 4～8 章与第 12A 章描述的每一条端点现在都在契约里；反向核对（第 15 章）确认没有一条只存在于本文。
2. 现有响应字段以契约组件（`components/schemas`，288 个）与代码投影为准（`services/*.py` 的视图函数、`rest/schemas/*.py` 的 Pydantic 模型）；**提供方资源**的字段以提供方源码为准，且本文只描述"需要哪些概念"。
3. 转发可行性以 `ports/protocols.py` 已声明的端口方法与 `adapters/registry.py` 的 `METHOD_MAP` 为准。

### 0.4 结论摘要

按"是否已被上位 SRS 规定"分类，而不是按工作量分类。下表的前三行是 **0.2.0 提交时的缺口统计**，保留作分类依据；**截至契约 1.19.0，A／B／C 三类的端点与契约项均已交付**，C 类的裁决过程见第 11 章。第四行是 0.4.0 反向校准时补入、此前本文漏记的条目（它们早已在契约里，不是新差距）。

| 类别 | 含义 | 端点 | 字段／定型项 | 契约治理项 |
| --- | --- | --- | --- | --- |
| **A 类** | S1 SRS §8.1 已**逐字声明**路径，代码未实现 → 照做即可，无需设计 | 5 | 0 | 0 |
| **B 类** | 能力与"供公共 API 转发"已规定（S1 §8.1 转发行、§7.2，或 S2／S3／S4 SRS），但 REST 出口未规定 → 需 S1 设计路径，**形状跟提供方 DTO** | 44 | 7 | 4 |
| **C 类** | SRS 未覆盖、S5 又无法自行达成 → 真正的新增需求（**已裁决**，见第 11 章） | 6 | 2 | 7 |
| **0.4.0 追加** | 1.19.0 契约内已有、本文此前漏记的 S5 面向条目（6 端点＋2 字段） | 6 | 2 | 0 |
| 合计 | | **61** | **11** | **11** |

另有 **12 项**上游实现进度与派生依赖（第 9 章，其中 UP10／UP11 由本轮裁决派生，UP12 由 0.4.0 校准补入）。

**C 类 15 项已于 2026-09-29 全部裁决：采纳，其中 6 项调整实现方式或归属**——结论见第 11 章，各条目正文中标注 **【裁决 2026-09-29】** 的段落即最终要求，实施以那些段落为准。

**0.2.0 提交时对 S1 最有价值的一句话（现状见括号）**：55 个端点里有 **20 个在 S1 内部已经具备全部条件，只差 `rest/app.py` 里一个路由和一次投影**——12 个环境类方法在 `ports/protocols.py:306-336` 声明过并已在 `adapters/registry.py:105` 登记；`get_section_schema`（`:165`）、`set_environment_binding`（`:153`）、`uid_pool_status`（`:151`）、`get_decisions`（`:146`）、`diff_objects`（`:207`）、`explain_issues`（`:284`）、`cancel_agent_run`（`:190`）同样如此；`services/inputs.py:141` 的 `parse_input` 已经写完并接通了 S3 与进度回报。**这 20 条现在都已注册路由**，路由模块按资源族拆到了 `rest/routes/*.py`（15 个模块），`rest/app.py` 只负责装配。

### 0.5 三个前置问题的现状（0.2.0 提出，1.19.0 复核）

三条都曾阻塞 S5 的契约生成。下表保留提出时的证据，最后一列是 1.19.0 的复核结论。

| # | 问题 | 性质 | 证据（0.2.0） | 1.19.0 现状 |
| --- | --- | --- | --- | --- |
| 1 | **UI-APP-007 曾无法满足** | 未达 CP-API-001 | 契约里的 2xx 响应体全部是 `{"type":"object","additionalProperties":true}`，请求体同为自由对象；`X-Operator`、`Idempotency-Key`、`Accept-Language`、`X-Correlation-Id` 完全未声明（纯中间件行为） | **已解决**：288 个 `components/schemas`，每个操作都 `$ref` 到具名组件；跨切头声明为 `components/parameters`（`XOperator`／`XIdempotencyKey`／`XAcceptLanguage`／`XCorrelationId`／`XCsrfToken`／`XTraceparent`）。仅 `SuggestionView` 保留 `additionalProperties:true`，那是列映射建议的扩展位（S5IF-FLD08） |
| 2 | **S1 曾两套命名并存** | 与 S1 自己的约定不一致（非 SRS 冲突） | `_Mirror` 配了 `alias_generator=to_camel`，但有 5 处绕过别名导出（`GET /executions/{id}`、`reconcile`、`commit-trace` 的 `execution` 子对象、内嵌 `issues[]`、Gate 说明的 `why.decisions[]`） | **已解决**：`GET /executions/{id}` 现为 `ExecutionView`，`why.decisions[]` 走 `model_dump(by_alias=True)`（`services/gates.py:1024`），`commit-trace` 的 `execution` 是 `CommitTraceView` 子组件。S5IF-G03 转为回归约束 |
| 3 | **三组资源曾无 REST 出口** | B 类为主 | `Environments`、`Governance`、`Blueprint/Registry` 无任何端点，而 S1 §7.2 已把"环境管理（供公共 API 转发）""Governance 管理（供公共 API 转发）"列为 S1 需要并向外提供的能力 | **已解决**：`/environments`（11 条路径／13 个操作）、`/governance`＋`effective-policy`（18／24）、Blueprint／Registry 组（15 条）全部在契约中 |

---

## 1. 术语、约定与优先级

### 1.1 优先级定义

| 级别 | 含义 | 对应 S5 里程碑 |
| --- | --- | --- |
| P0 | S5 的 M1 交付无法进行 | M1 |
| P1 | S5 的 MVP 验收无法进行 | M2 |
| P2 | M3 功能所需 | M3 |

### 1.2 "落地动作"取值

| 取值 | 含义 |
| --- | --- |
| 路由 | `ports/protocols.py` 已有端口方法，或 S1 服务函数已存在，只需在 `rest/app.py` 注册路由并投影 |
| 端口+路由 | BlueprintPort／ExecutionPort／IntelligencePort 需先补方法，再加路由 |
| 门面契约化 | S3 的 `governance(action, payload) -> Any` 必须拆为逐个有类型的端口方法与响应模型 |
| 定型 | 端点与字段已存在，但响应是自由对象，需声明为 OpenAPI 组件 |
| 字段 | 端点已存在，需增补缺失字段 |

### 1.3 分类归属（A／B／C）

| 类别 | 判定标准 | 本文的语气 |
| --- | --- | --- |
| **A** | S1 SRS §8.1 已逐字写出该路径 | "按已声明的契约实现即可"，不讨论设计 |
| **B** | 能力已在某份 SRS 规定（S1 §8.1 转发行／§7.2，或 S2／S3／S4 SRS），但 REST 出口未规定 | 建议路径（标 `（建议）`），**响应形状一律跟提供方 DTO，本文不定义**；本文只说明"S5 要显示的概念"与"现 S1 镜像 DTO 装不下哪些概念" |
| **C** | 任何 SRS 都未覆盖，且 S5 无法自行达成（须服务端聚合或新建查询） | 真正的新增需求；**已于 2026-09-29 裁决**，结论见第 11 章，条目正文中【裁决 2026-09-29】段落为最终要求 |

归属一览（前七行是 0.2.0 的原始归属，末两行是 0.4.0 反向校准时补入的成员；两者今天都已在契约内）：

| 类别 | 成员 |
| --- | --- |
| **A（5 端点）** | S5IF-BP02、BP10、ENV07、ENV12、INP05 —— 此五条路径出自 S1 §8.1"补充端点（S5 所需）"行 |
| **B（44 端点）** | ENV01～ENV06、ENV08～ENV11；BP01、BP03～BP09、BP12、BP13、BP15；GOV01～GOV19；INP01、INP03、INP06、INP07 |
| **B（7 字段／定型）** | INP02、FLD06、FLD07、FLD08、FLD10、FLD11、FLD12 |
| **B（4 契约）** | G01、G02、G09、G10 —— 派生自 CP-API-001/002/003、FR-SEC-007、CP-WF-025 的既有义务 |
| **C（6 端点）** | BP11、BP14、FLD03、FLD04、FLD05、FLD09 |
| **C（2 字段／定型）** | FLD01、FLD02 |
| **C（7 契约）** | G03、G04、G05、G06、G07、G08、G11 |
| **B（0.4.0 追加 6 端点）** | GOV20、INP08、INP09、BP18、ENV13、FLD14 —— 能力早已在 S3／S2／S4 侧规定（AI-LLM-005、AI-INP-*、BP-REG-008a、DX-EXE-038、FD-ART-010），本文此前漏记 |
| **B（0.4.0 追加 2 字段）** | BP19（项目 `codePrefix`，BP-REG-009）、FLD13（CR 删除候选，CP-CR-005） |

### 1.4 对所有新增端点的通用要求

1. 路径前缀 `/api/v1`（CP-API-001）；`environmentKey` 一律作为路径参数或请求体字段显式出现，禁止默认环境（DX-ENV-004 / FR-ENV-003）。
2. 改变状态的方法（POST/PUT/PATCH/DELETE）必须携带非空 `X-Operator`，缺失返回 400 `OPERATOR_REQUIRED`（CP-API-002，现由 `rest/middleware.py` 的 `RequestContextMiddleware` 实现，新路由自动继承）。姓名超出 ISO-8859-1 时浏览器发不出该头，客户端改发 `encodeURIComponent(name)`，S1 由 `rest/middleware.py:decode_operator` 还原；curl 直发 UTF-8 字节同样还原（CP-API-002 的 1.6.0 补充）。
3. 创建类与执行类必须支持 `Idempotency-Key`（CP-API-003）。本文在每条端点上单独标注是否需要。
4. 转发型端点**不得在 S1 持久化结果**（CP-API-007、D-14）；环境保护属性必须实时查询、不得缓存（CP-WF-027）。
5. 时间一律 ISO 8601 UTC；哈希一律 `sha256:<hex>`；ID 为带类型前缀的 ULID（00 文档 §6.7）。
6. 失败响应一律使用 S1 §8.3 的统一信封（`errorCode/message/stage/artifactId/retryable/correlationId/details`），`message` 按 `Accept-Language` 本地化。
7. 列表端点支持 `limit`/`offset` 与排序参数（CP-API-005），响应信封统一为 `{items,total,limit,offset}`（见 S5IF-G04）。

---

## 2. 现有能力确认（无需 S1 改动）

为避免 S1 重复劳动，确认以下 S5 需求**已由现有端点满足**：UI-APP-006、UI-OP-001～003（`X-Operator` 由 S5 侧发送即可）、UI-REQ-001～004（`Idempotency-Key` 与错误信封已实现）、UI-RT-001～005（SSE、按序号补发、`stream.reset` 已实现）、UI-GATE-001/003/004/006（Gate 说明端点已具备 `found/willDo/why/uncertain/canChange/afterApproval` 六段；`seenBoundHashes` 与 `invalidatedReason` 已有）、UI-OPS-001（CR 全生命周期）、UI-OPS-003（通知与未读数）、UI-WB-004/005（产物内容、Diff、Lineage 可支撑视图切换与检查器大部分字段）、UI-MAN-001/004（Patch 提交与提交审批）、UI-WB-008（`resolve-manual-check` 与 `reconcile`）、UI-Q-002/003（`severity`、`blocking`、`answeredBy`、`answeredAt` 已有）、UI-ADM-004 的读写通道（`GET/PUT /settings`）。

**注意不要误判为已满足**：UI-PRJ-001（Dashboard 的待审批 Gate 与最近执行 → 见 C 类 FLD03/FLD04）、UI-PRJ-002（向导需环境清单 → B 类 ENV01）、UI-PRJ-004（环境增删与导出下载 → ENV01、C 类 FLD09）、UI-GATE-002/005（WARNING 与确认项逐条处理 → B 类 FLD07）、UI-OPS-002（审计与追溯功能已在，但 `execution` 子对象大小写待修 → G03）。

### 2.1 其余已成型的 S5 面向端点（按路径索引）

下列端点不承载"缺口"，因此不单列章节；它们只承载跨切要求（G01／G03／G05 等）或已交付的行为要求。列在这里是为了让第 15 章的核对可以逐条对上：**凡是带 `S5IF-*` 追溯的操作，本文都能指名它是哪一条**。

| 族 | 端点（前缀 `/api/v1`） | 承载条目 |
| --- | --- | --- |
| 工作流与阶段 | `GET /api/v1/workflows/{workflowId}`、`POST /api/v1/workflows/{workflowId}/pause`、`POST /api/v1/workflows/{workflowId}/resume`、`POST /api/v1/workflows/{workflowId}/cancel`、`GET /api/v1/workflows/{workflowId}/allowed-actions`、`GET /api/v1/workflows/{workflowId}/deployment-runs`、`POST /api/v1/workflows/{workflowId}/deployment-runs`、`GET /api/v1/workflows/{workflowId}/events`、`POST /api/v1/stages/{stageId}/rerun`、`POST /api/v1/stages/{stageId}/resolve-manual-check`、`POST /api/v1/stages/{stageId}/submit-for-approval`、`POST /api/v1/stages/{stageId}/questions`、`GET /api/v1/stages/{stageId}` | G01、G05（SSE 头与补发）、G06（rerun 202）、G10／FLD12、FLD01、FLD02、FLD06 |
| 项目 | `GET /api/v1/projects/{projectId}`、`PATCH /api/v1/projects/{projectId}`、`DELETE /api/v1/projects/{projectId}`、`POST /api/v1/projects/{projectId}/archive`、`POST /api/v1/projects/{projectId}/upgrade-mode`、`GET /api/v1/projects/{projectId}/export`、`GET /api/v1/projects/{projectId}/settings`、`PUT /api/v1/projects/{projectId}/settings`、`GET /api/v1/projects/{projectId}/target-environments`、`PUT /api/v1/projects/{projectId}/target-environments`、`GET /api/v1/projects/{projectId}/workflow`、`GET /api/v1/upload-limits` | G01、G02、FLD09（导出下载）、FLD10（项目设置与上传上限） |
| Gate 与问题 | `POST /api/v1/gates/{gateId}/approve`、`POST /api/v1/gates/{gateId}/reject`、`GET /api/v1/gates/{gateId}/explanation`、`GET /api/v1/questions/{questionId}` | G03、FLD02、FLD07、BP13、FLD08 |
| Change Request | `GET /api/v1/projects/{projectId}/change-requests`、`POST /api/v1/projects/{projectId}/change-requests`、`GET /api/v1/change-requests/{crId}`、`POST /api/v1/change-requests/{crId}/approve`、`POST /api/v1/change-requests/{crId}/cancel`、`POST /api/v1/change-requests/{crId}/complete`、`POST /api/v1/change-requests/{crId}/impact`、`POST /api/v1/change-requests/{crId}/reject`、`POST /api/v1/change-requests/{crId}/start` | G01、FLD13（删除候选） |
| 产物、审计、执行与通知 | `GET /api/v1/artifacts/{artifactId}`、`GET /api/v1/artifacts/{artifactId}/content`、`GET /api/v1/artifacts/{artifactId}/diff`、`GET /api/v1/artifacts/{artifactId}/lineage`、`POST /api/v1/artifacts/{artifactId}/reproduce`、`POST /api/v1/artifacts/{artifactId}/purge`、`GET /api/v1/audit`、`GET /api/v1/audit/commit-trace/{executionId}`、`POST /api/v1/audit/verify`、`GET /api/v1/executions/{executionId}`、`GET /api/v1/executions/{executionId}/report`、`POST /api/v1/executions/{executionId}/reconcile`、`POST /api/v1/notifications/{notificationId}/read`、`POST /api/v1/deployment-runs/{runId}/cancel` | G01、G02、G03、G05、G04（审计游标）、BP12、FLD05、FLD09、FLD14（purge，契约待补挂） |
| 认证与身份（不由 S5 直接驱动业务） | `/auth/*`、`/users`、`/roles`、`/projects/{id}/members`、`/projects/{id}/artifact-grants`、`/production-exemptions` | 仅 G01／G02 的定型义务；S5 只消费 `/auth/session` 与 `/auth/login`，其余属 UI-SET／CP-IAM 的范围 |

---

## 3. 通用契约要求（S5IF-G）

> 分类：G01、G02、G09、G10 属 **B 类**（分别可由 CP-API-001"以 OpenAPI 3.1 描述…供 S5 使用"、FR-SEC-007、CP-WF-025/026 直接推出）；G03～G08、G11 属 **C 类**（SRS 未规定 wire 命名、信封形状、`details` 结构等，需裁决，见第 11.3 节）。
>
> **1.19.0 复核：本章 11 条全部已落地。** G01（288 个组件，无未定型响应体）、G02（请求模型齐备，`extra="forbid"` 用于两处 PATCH）、G03（camelCase + UPPER_SNAKE，见 §0.5 第 2 行）、G04（`{items,limit,total?,offset?,nextCursor?}`）、G05（6 个跨切头声明为 `components/parameters`）、G06（`202` + `AcceptanceView`）、G07（`ErrorDetails` 按码声明，含 `reason`／`requiredStage`）、G08（commit 请求体已无幂等键）、G11（`info.version` 1.19.0 + `docs/CHANGELOG.md` + `x-requirements`）。以下保留要求原文与验收口径，供回归使用；个别"现状"描述已按 1.19.0 改写。

> 这一章是 UI-APP-007 与 UI-IF-002 能否成立的前提。若 S1 认为工作量大，请优先做 G01/G03/G05——它们决定 S5 是"生成契约"还是"手抄契约"。

### S5IF-G01　响应体必须类型化　【调整】CP-API-001　P0

把全部 **172 个操作**（含 `/healthz`）+ 本文新增端点的 2xx 响应声明为 OpenAPI 3.1 组件（`components/schemas`），实现方式建议：为每个端点定义 Pydantic 响应模型并在装饰器上声明 `response_model`，然后重跑 `S1/scripts/export_openapi.py`。

```jsonc
// 现状（0.2.0 时全部 71 个操作皆如此；1.19.0 的 172 个操作已全部具名）
"responses": { "200": { "content": { "application/json": {
  "schema": { "additionalProperties": true, "type": "object" } }}}}}
// 要求（1.19.0 已达到：288 个组件，每个操作 $ref 到具名组件）
"responses": { "200": { "content": { "application/json": {
  "schema": { "$ref": "#/components/schemas/ProjectView" } }}}}}
```

验收：`openapi-typescript` 生成的类型中，`Response 类型不应再出现 `{[key:string]: unknown}``；S5 的 `scripts/sync-contract.mjs` 漂移检查报告零 `untyped-response`。唯一允许的 `additionalProperties:true` 是 `SuggestionView`（列映射建议的扩展位，S5IF-FLD08）。

### S5IF-G02　请求体必须类型化，禁止自由 dict 端口入参　【调整】CP-API-001　P0

`create_environment(spec: dict[str, Any])`（`ports/protocols.py:310`）、`update_environment(..., patch: dict)`（`:312`）、`submit_patch(patch)` 等以自由字典穿透到 S2/S4 的接口，必须在 S1 侧定义请求模型并声明 `response_model`/`requestBody`，由 S1 负责字段校验后再转成提供方需要的形状。理由：否则错误以提供方内部异常的形式返回，S5 无法做 UI-REQ-002 的错误码映射。

### S5IF-G03　响应命名统一为 camelCase　【新增·C 类】（SRS 未规定 wire 命名；依据为 S1 自身 `_Mirror` 的 `alias_generator=to_camel`）　P0

禁止把 `Model.model_dump()` 直接作为响应；一律 `model_dump(by_alias=True)`（`ports/dto.py:33-35` 的 `_Mirror` 已配 `alias_generator=to_camel`，只需在导出时带上别名）。需修正的泄漏点：

| 端点／字段 | 现状 | 位置 |
| --- | --- | --- |
| `GET /executions/{id}` 全量（`execution_id`、`report_artifact_id`、`import_report_artifact_id`、`payload_hash`、`applied`、`steps`、`issues`） | snake_case | `rest/app.py:607` |
| `issues[]` 元素（`issue_id`、`rule_id`、`object_key`、`object_uid`、`source_stage`、`suggested_action`） | snake_case | `ports/dto.py:41-57`，凡内嵌处皆受影响 |
| `POST /executions/{id}/reconcile`（`outcome`、`evidence`） | 碰巧一致 | `rest/app.py:621` |
| `GET /audit/commit-trace/{id}` 的 `execution` 子对象 | snake_case | `services/audit_query.py:160` |
| Gate 说明 `why.decisions[]`（`decision_id`、`affected_artifacts`、`impact_level`） | snake_case | `services/gates.py:775` |

**【裁决 2026-09-29】采纳，并提升为全局约定**：写入 00 文档 §6.x —— 公共 REST 的**字段名一律 camelCase、枚举值一律 UPPER_SNAKE**；转发提供方 DTO（S2/S3 的模型是 snake_case）时，由 S1 投影成 camelCase。例外：**系统设置的键属于"数据"而非字段名**，可保持 `snake_case` 原样，但必须在 OpenAPI 中声明为枚举（见 S5IF-FLD10）。过渡要求：S5 已按现状录制契约，若 S1 分阶段修复，请**同一响应内不要混用两种大小写**，并把改名列入主版本变更（S5IF-G11）。

**【1.19.0 复核】已全部修正**：上表 5 处泄漏点现在都是 `by_alias=True` 或具名组件（`ExecutionView`、`CommitTraceView`、`why.decisions[]` 见 `services/gates.py:1024`），`issues[]` 为 `IssueView`。本表保留作回归清单：新增投影若再直接 `model_dump()`，就是退回这里。

### S5IF-G04　分页信封统一　【新增·C 类】（CP-API-005 只要求"支持分页与排序"，未规定信封形状）　P1

现在三种形状并存，S5 无法写通用列表组件：

| 形状 | 现有端点举例 |
| --- | --- |
| `{items,total,limit,offset}` | `GET /projects`、`GET /notifications`、`GET /projects/{id}/audit` |
| `{items,total}` | `GET /workflows/{id}/artifacts`、`GET /audit`、`GET /projects/{id}/inputs` |
| `{items}` | `GET /workflows/{id}/stages`、`/gates`、`/questions`、`/deployment-runs`、`/change-requests`、`/target-environments`、`GET /settings` |

**【裁决 2026-09-29】调整后采纳**：统一信封为 `{items, limit, total?, offset?, nextCursor?}`。纯 `offset` 分页不适合审计这类百万级列表，且 S0 的产物与审计查询本身就是 keyset 分页，因此：**普通列表用 `offset`；审计、产物、事件类列表用 `nextCursor`**；`total` 在代价过高时**省略而不是填 0**；排序参数统一为 `sortBy` + `sortDir`。对 5,000 对象规模（NFR-CAP-001、UI-REV-006）必须真正支持服务端分页与排序。

**【1.19.0 复核】已落地**：`ProjectPage`／`ArtifactPage`／`AuditPage` 等页面组件都带 `items,limit,total?,offset?,nextCursor?`；审计与产物用 `nextCursor`，普通列表用 `offset`。上表是 0.2.0 的三种旧形状，仅供对照。

### S5IF-G05　跨切请求头与响应头必须在 OpenAPI 声明　【新增】　P0

`docs/openapi.json` 必须声明下列头，S5 不能靠口口相传：`X-Operator`（必填条件＝POST/PUT/PATCH/DELETE）、`Idempotency-Key`（作用域＝`"{METHOD} {route-template}"`）、`Accept-Language`（`zh-CN`/`en`）、`X-Correlation-Id`（入站沿用、出站回显）、`traceparent`、`X-CSRF-Token`（会话登录后）；响应头 `X-Idempotent-Replay: true`、`X-Idempotency-Stored: <responseId>`、产物内容的 `X-Data-Classification`。实现：`openapi` 的 `components.parameters` + 各操作引用；中间件行为已在 `rest/middleware.py:37-49,203-208,249`。

**【裁决 2026-09-29】采纳**，成本很低。响应头**名称以 S1 实际实现为准**并写进文档：代码里是 `X-Idempotent-Replay`（`rest/middleware.py:40`），而 S1 的 CTD 用的是 `Idempotent-Replayed`，两处不一致，需以实现为基线统一后再声明进 OpenAPI。

**【1.19.0 复核】已落地**：请求头声明为 6 个具名参数组件（`XOperator`／`XIdempotencyKey`／`XAcceptLanguage`／`XCorrelationId`／`XCsrfToken`／`XTraceparent`），由 `rest/route_meta.py` 按方法自动挂载；`PUT /secrets/{name}` 明确不挂 `XIdempotencyKey`（该路径整段排除在回放表外，见 §12A）。

### S5IF-G06　`run`/`rerun` 响应形状统一　【新增】　P1

现按引擎配置返回两种形状：引擎受理信封 `{stageId,accepted,engine,idempotencyKey?,workflowId?}`（`engine/inprocess.py:52`、`engine/temporal.py:169-175`）或同步阶段结果 `{status,agentRunId,stageRunId,readyForApproval,...}`（`services/steps.py:59-95`）。

**【裁决 2026-09-29】采纳**，与 S1 的异步步骤执行模型一致（所有长任务皆异步）。统一返回 **202** 与 `{stageId, stepRunId, accepted}`；**不暴露 `engine` 字段**——部署形态属内部细节。结果经 SSE 与 `GET /stages/{id}` 观察。

**【1.19.0 复核】已落地**：`run`／`rerun`／`commit`／`governance/evaluations` 都回 `202` + `AcceptanceView`（`stageId` 仅在步骤属于某阶段时出现），`engine` 已不在任何响应里。

### S5IF-G07　错误码目录补齐与冲突消解　【调整】S1 §8.3　P0

1. 新增端点会引入新的失败原因，S1 须为其登记错误码并补 `S1/src/factory_control/i18n.py` 的 `_ZH`/`_EN` 两个目录（两语言键集合必须严格相等，CP-NFR-09）。
**【裁决 2026-09-29】采纳，并明确归一规则**：S1 维护公共错误码目录。判定分两种——

1. **含义与 S1 目录中某个码相同的提供方码，一律归一为 S1 的码**，例如 `DX_OUTSIDE_COMMIT_WINDOW` → `OUTSIDE_COMMIT_WINDOW`；
2. **有独立含义、且已在 S0 目录登记的提供方码允许透出**，例如 `AI_PROMPT_ACTIVATION_BLOCKED`——S5 需要据此在 Prompt 页展示"缺少通过的评测或回归记录"（S5IF-GOV09）。

未登记且与 S1 码语义重叠的提供方码不得直接透出。重申：`details` 中**永远不得出现凭证**（S5IF-G09）。
3. 明确 `details` 的按码形状（S5 需在 UI 中渲染），至少定义：`OPERATOR_REQUIRED`、`INVALID_STATE`、`IDEMPOTENCY_CONFLICT`、`APPROVAL_INVALIDATED`（现含 `{gateId,mismatches[]}`）、`ENVIRONMENT_LOCKED`（需含 `environmentKey`、`lockReason`）、`OUTSIDE_COMMIT_WINDOW`（需含 `environmentKey`、`windows[]`、`environmentTimezone`、`currentTime`）、`UPLOAD_REJECTED`（需含 `maxBytes`、`allowedTypes`）、`PRIOR_VERIFICATION_MISSING`（需含 `environmentKey`、`requiredEvidence[]`）。

### S5IF-G08　幂等键优先级显式化　【新增】　P1

`POST /deployment-runs/{id}/commit` 同时接受请求头 `Idempotency-Key` 与请求体 `idempotencyKey`（`rest/app.py:591`），两者并存时行为未定义。

**【裁决 2026-09-29】采纳并更进一步**：首次正式发布，无历史兼容负担，因此**直接删除请求体中的 `idempotencyKey`**，以 **`Idempotency-Key` 请求头为唯一来源**；commit 请求缺少该头时返回 400。这同时消除了"同键两处给、值不同"这一类竞态，并让 UI-REQ-004 的"重试复用同键"只有一个落点。

**【1.19.0 复核】已落地**：`POST /deployment-runs/{runId}/commit` 与 `POST /environments/{key}/delete-objects` 的请求体里都没有 `idempotencyKey`；后者还要求删除类请求体 `extra="forbid"`，把误写的该字段直接拒掉（S5IF-ENV13）。

### S5IF-G09　凭证零明文，含 SSE 与 details　【调整】FR-SEC-007、00 文档 §6.9　P0

本文所有环境类与治理类端点：请求只接受 Secret 引用，响应只回引用与到期时间，**禁止**回显任何值、掩码值、长度或前缀。SSE 事件 payload、错误 `details`、审计 `before/after` 同样禁止。

**【调整 2026-10-04，契约 1.7.0】**
1. 引用格式白名单为 `db://`、`env://`、`vault://`、`secret://`、`kms://`（`rest/schemas/environments.py:REFERENCE_SCHEMES`）。其中**只有 `db://`（S1 加密凭证库，S5IF-SEC01）与 `env://`（部署环境变量）在本进程里真有后端**（`bootstrap.py` 只注册 `env`、`memory`、`db`）；其余三种保留为兼容语法，界面不得把它们当可选项呈现——给操作者一个解析不到的地址，比少给一个选项坏得多。
2. "唯一能写入凭证值的地方"是 `PUT /api/v1/secrets/{name}`（S5IF-SEC01）：值只在那一次请求体里出现，服务端立即加密并只回引用。环境类与治理类端点的规则不变：它们**永远**只接受引用。
3. 校验失败也不得回显：422 摘要里若含客户端提交的原值，等于把密钥抄进客户端与代理日志，因此承载凭证的字段（`material`）的输入值必须在渲染前抹掉，逐字段错误只给 `path` 与原因（CP-SEC-004）。
4. 凭证端点的响应不得进入幂等响应回放表（回放表逐字存响应体）。

界面侧的承诺相应改写：S5/S5M 提供**一次性录入控件**（`type="password"`、`autocomplete="new-password"`、提交后不驻留、不回显），而不是"完全没有输入框"（UI-ADM-002 的调整与新增的 UM-ADM-004，理由见各自 SRS）。

### S5IF-G10　`allowed-actions` 保持按钮可用性唯一来源　【直引】CP-WF-025/026　P0

新增端点对应的动作必须一并进入 `allowed-actions`（`services/workflow.py:628-726`），并带 `reasonCode`（目录见 `domain/actions.py:23-41`）。要求见 S5IF-FLD12。

### S5IF-G11　契约变更需可判定版本　【调整】UI-IF-002、00 文档 §6.1　P0

`info.version` 0.2.0 时是 `0.1.0`，且没有 servers 与变更日志；**1.19.0 时是 `1.19.0`，`docs/CHANGELOG.md` 逐版记录到 1.19.0，`servers` 仍为空数组**（见下）。**【裁决 2026-09-29】调整后采纳**：① 语义化版本与 `docs/CHANGELOG.md` 按建议执行，破坏性变更提升主版本；② 追溯字段**不做成 S5 专用的 `x-s5if`**，改用通用的 **`x-requirements: ["CP-API-001", "S5IF-ENV01", ...]`**，这样 S1 自己的需求编号（`CP-*`）也能挂上，测试组可复用同一字段做覆盖追溯（1.19.0 已有 142 个操作挂载）；③ `servers` 一栏留空由部署侧注入（S5 与 S1 同域，见 UI-APP-008），但需在文档中写明该约定——`info.description` 已写明"契约测试不得钉死主机"。

---

## 4. 环境与能力（转发 S4）——S5IF-ENV

> 分类：**A 类 2 条**（S5IF-ENV07、ENV12，路径出自 S1 §8.1"补充端点"行）；**B 类 10 条**（其余环境端点，能力出自 S4 SRS §7.1 与 S1 §7.2"环境管理（供公共 API 转发）"，路径为本文建议）；0.4.0 追加 **S5IF-ENV13**（定向删除，DX-EXE-038）。**本组无 C 类**：S5 需要的每一项环境数据都能在 S4 SRS 中找到规定。
>
> **1.19.0 状态**：本组 13 条全部在契约内（`/environments` 共 11 条路径／13 个操作），下面保留原始要求与验收口径；ENV13 是唯一由 1.9.0 新增的路径。
>
> 这一组在 0.2.0 时**全部只需加路由**：`ExecutionPort` 已声明 `get_environment`（`ports/protocols.py:306`）、`list_environments`（`:308`）、`create_environment`（`:310`）、`update_environment`（`:312`）、`disable_environment`（`:314`）、`get_protection`（`:316`）、`set_emergency_lock`（`:318`）、`list_expiring_credentials`（`:322`）、`test_connection`（`:324`）、`check_capability_freshness`（`:332`）、`probe_capabilities`（`:334`）、`discover`（`:336`），`adapters/registry.py:105` 的 `METHOD_MAP` 已登记，`FACTORY_FAKE_ENVIRONMENTS` 已能为开发模式提供假环境（`adapters/registry.py:173-180`）。唯一后补的是 ENV13 的 `delete_objects`（1.9.0 才进 `METHOD_MAP`）。
>
> 命名权威：环境的最终字段名以 **S4 的 `factory_dhis2.api` DTO 定稿为准**（S4 SRS §7.1；0.2.0 时其 `api.py` 还是增量 1 占位，见第 9 章）。下表只做概念对齐，**不重新规定 S4**。

### 4.1 概念对齐表（S4 已落库列 → S5 需显示的概念 → S1 镜像 DTO 现状）

S4 的 `models/environment.py:29-97` 是该实体当前唯一的字段事实来源，但**命名权仍属 S4**：下表列出 S5 要显示的概念、S4 已落的列、以及 S1 现镜像 DTO 装不下哪些概念，供 S1 写转发投影时对照。

| S5 需显示的概念（建议名） | 类型 | S4 列 | 说明 | 枚举取值（出自 S4 `enums.py`） |
| --- | --- | --- | --- | --- |
| `environmentKey` | string | `environment.key` | 全局对象，不含项目信息（D-03） | |
| `name` | string | `name` | DX-ENV-001 列出但 14.1 示例未含 | |
| `baseUrl` | string | `base_url` | | |
| `environmentClass` | enum | `environment_class` | PRODUCTION 触发全部保护规则 | `DEV`/`TEST`/`UAT`/`PRODUCTION`（`enums.py:9-15`） |
| `versionConstraint` | string\|null | `version_constraint` | 列为可空，DX-ENV-001 列为必需 → 请裁决 | |
| `authMode` | enum | `auth_mode` | 默认 `PAT`（DX-ENV-006） | `PAT`/`BASIC`（`enums.py:23-27`） |
| `tlsRequired` | bool | `tls_required` | DX-CLI-006 | |
| `llmDataPolicy` | enum | `llm_data_policy` | 默认 `PRIVATE_NETWORK`；项目有效策略＝组织与全部目标环境最严者（D-07，由 S1 计算） | `LOCAL_ONLY`/`PRIVATE_NETWORK`/`APPROVED_EXTERNAL`/`UNRESTRICTED`（`enums.py:61-84`） |
| `timeoutSeconds` | int | `timeout_seconds` | 默认 60（DX-CLI-001） | |
| `status` | enum | `status` | 禁用需无进行中执行（DX-ENV-009） | `ACTIVE`/`DISABLED`（`enums.py:31-35`） |
| `notes` | string\|null | `notes` | | |
| `secretRefs` | object | `environment_credential.secret_ref` | **键为凭证范围，值为 Secret 引用**；禁止值/掩码/长度（S5IF-G09） | 范围：`discovery`/`import`/`tracker`/`datastore`/`apps`/`maintenance`（`enums.py:39-47`，MVP 仅前两项） |
| `credentials` | array | `environment_credential` | `{scope, secretRef, expiresAt, label}`，`expiresAt` 可空 | |
| `createdAt`/`updatedAt` | datetime | 继承列 | | |

保护属性（PRODUCTION 关键）：

| S5 需显示的概念（建议名） | 类型 | S4 列 | 说明 |
| --- | --- | --- | --- |
| `environmentClass` | enum | `environment_protection.environment_key` 关联 | |
| `timezone` | string | `timezone` | 时间窗解释所用时区，默认 `UTC` |
| `commitWindows` | array | `commit_windows` | 形状 `[{"days":[2,4],"start":"20:00","end":"23:00"}]`，`days` 为 ISO 星期（1=周一） |
| `deletionPolicy` | enum | `deletion_policy` | 默认 `SAFE_ONLY`；PRODUCTION 默认 `FORBID`。**取值是 S4 自定（SRS 只列字段未枚举取值，`enums.py:88-98`），需 S4 确认后再定 S5 文案，见 S5IF-UP09** |
| `emergencyLocked` | bool | `emergency_lock` | 开启后 COMMIT／Fixture 导入／维护动作全部拒绝，读不受影响（DX-ENV-007、DX-BR-11） |
| `lockReason` / `lockChangedAt` / `lockChangedBy` | | `lock_reason` / `lock_changed_at` / `lock_changed_by` | |
| `withinCommitWindow` | bool | 计算 | UI-GATE-005 与 UI-DEP-002 都要显示 |
| `requiredApprovals` / `operatorRole` | | 列已建；S4 0.1.0 保存时恒拒绝非空值 | S5 只读展示默认值。**当前无法设置**：尽管 S1 的 M4 认证已交付，S4 的 `_validate_m4_fields` 仍以 “take effect with M4 permissions” 拒绝任何非空值，因此两端都不提供编辑入口 |

### 4.2 端点清单

| # | 方法与路径 | 优先级 | 落地动作 | 来源 | 请求 | 响应 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S5IF-ENV01 | `GET /api/v1/environments` | P0 | 路由 | UI-ADM-001、UI-PRJ-002 | `limit,offset,environmentClass,status` | `{items:[EnvironmentView],total,limit,offset}` | 新建项目向导选环境也用它 |
| S5IF-ENV02 | `GET /api/v1/environments/{environmentKey}` | P0 | 路由 | UI-ADM-001 | — | `EnvironmentView` | 端口 `:306`；404 `NOT_FOUND` |
| S5IF-ENV03 | `POST /api/v1/environments` | P0 | 路由 | UI-ADM-001 | `CreateEnvironmentRequest`（§4.1 字段 + `credentials[]`） | `EnvironmentView`，201 | 幂等必需；PRODUCTION 须 `discovery` 与 `import` 引用不同 Secret，否则 422（DX-ENV-005、DX-AC-02）；Secret 引用格式非法 → 422 `VALIDATION_ERROR`（`S4 registry.py:136` 同类校验） |
| S5IF-ENV04 | `PATCH /api/v1/environments/{environmentKey}` | P0 | 路由 | UI-ADM-001 | `UpdateEnvironmentRequest`（仅提交字段） | `EnvironmentView` | 端口 `:312` |
| S5IF-ENV05 | `POST /api/v1/environments/{environmentKey}/disable` | P1 | 路由 | UI-ADM-001 | `{reason}` | `{environmentKey,status:"DISABLED"}` | 有进行中执行 → 409（`DX_ENVIRONMENT_IN_USE`，归一为 `INVALID_STATE`） |
| S5IF-ENV06 | `GET /api/v1/environments/{environmentKey}/protection` | P0 | 路由 | UI-GATE-005、UI-DEP-002 | — | `EnvironmentProtectionView`（§4.1 下表中全部字段，**须含 `deletionPolicy`**） | 端口 `:316`；禁止缓存（CP-WF-027）；0.2.0 时 DTO `ports/dto.py:245-255` 缺 `deletionPolicy` 与 `lockChangedAt/By`，**1.19.0 已按 §4.1 保护属性表补齐** |
| S5IF-ENV07 | `POST /api/v1/environments/{environmentKey}/emergency-lock` | P0 | 路由 | UI-ADM-001（SRS §8.1 已声明此路径） | `{locked:bool, reason:string\|null}` | `{environmentKey,emergencyLocked,lockReason,lockChangedAt,lockChangedBy}` | 端口 `:318`（已含 `operator`，比 S4 签名多此参数）；须写审计 `dx.environment.lock_changed` |
| S5IF-ENV08 | `POST /api/v1/environments/{environmentKey}/test-connection` | P0 | 路由 | UI-ADM-001 | `{requiredScopes:string[]}`（缺省＝`["discovery","import"]`） | `ConnectionCheckView`（§4.3） | 幂等必需；端口 `:324`；八项检查见 DX-CON-001，只读探测（DX-CON-003），结果须持久化并写审计（DX-CON-004） |
| S5IF-ENV09 | `GET /api/v1/environments/{environmentKey}/capability-profile` | P0 | 路由 | UI-ADM-001、UI-DEP-002 | `?includeContent=false` | `CapabilityProfileView`（§4.4） | 端口 `check_capability_freshness:332`；索引按 `(environment_key, probed_at)` 取最新（`S4/src/factory_dhis2/models/profile.py:34`） |
| S5IF-ENV10 | `POST /api/v1/environments/{environmentKey}/probe-capabilities` | P1 | 路由 | UI-ADM-001 | `{}` | `{capabilityProfileArtifactId,version,profileVersion}` | 端口 `:334`；幂等必需 |
| S5IF-ENV11 | `GET /api/v1/environments/{environmentKey}/discovery-results` | P1 | 路由 | UI-REV-003、UI-DEP-003 | `limit,offset,projectId,scope` | `{items:[DiscoveryResultView],total,limit,offset}` | 端口 `:336`；`DiscoveryResultView`＝`{artifactId,projectId,scope,objectCount,blockingIssues[],version,discoveredAt,contentHash}`（列见 `S4 models/profile.py:39-63`） |
| S5IF-ENV12 | `GET /api/v1/environments/expiring-credentials` | P0 | 路由 | UI-ADM-001（SRS §8.1 已声明此路径） | `?withinDays=30` | `{items:[{environmentKey,scope,secretRef,expiresAt,daysRemaining}]}` | 端口 `:322`；注意此字面量路径须与 `/{environmentKey}` 路由区分（FastAPI 声明顺序） |
| S5IF-ENV13 | `POST /api/v1/environments/{environmentKey}/delete-objects` | P1 | 路由 | FR-TST-002、DX-EXE-038（契约 1.9.0 新增） | `{projectId,deletionArtifactId,expectedHash}`；**必须带 `Idempotency-Key`**，缺头 400 `IDEMPOTENCY_KEY_REQUIRED` | `ObjectDeletionView`＝`{environmentKey,deletionArtifactId,status,applied,detail,executionId,payloadHash,reportArtifactId,importReportArtifactId,issues[],steps[]}` | 同步返回 S4 的执行结论（不套 202：删除不属于任何工作流，S1 拿不出 `stepRunId`）；`status` 是 S4 的 `COMPLETED`/`FAILED`/`NEEDS_MANUAL_CHECK`，**拒绝也以 `FAILED`+`applied=false` 返回而不是 HTTP 错误**；请求体 `extra="forbid"`，**不接受 `idempotencyKey` 字段**（S5IF-G08）与 `importOptions`（`DELETE`+`ALL` 是 S4 的安全规则，不是每次请求的偏好）。载荷来自本次 Run 的删除产物（`deletionArtifactId`+`expectedHash` 成对），S5 只展示与二次确认，不自行拼清单。授权复用 `environment.manage`，**没有 `environment.delete_objects` 动作名**——因此 `allowed-actions` 里问不到它，S5 只能按环境管理权限决定按钮是否出现；这是本次核对的遗留口径，见 §15 |

> 说明：`GET /environments/{key}/snapshots` 与漂移检查结果**不单列**，它们属于某个 Run，放在 S5IF-FLD05 的部署控制台聚合视图里返回，避免 S5 为一个面板发四次请求。

### 4.3 连接测试转发投影需覆盖的概念　【B 类·定型】

现 DTO（`ports/dto.py:324-329`）＝`{ok,version,checks,capability_profile_artifact_id,message}`，其中 `checks` 是自由数组，且**没有"缺失权限"**，无法满足 UI-ADM-001 的"连接测试结果（各检查项、缺失权限）"。**1.19.0 已按下列概念定型为 `ConnectionCheckView`**（含 `checks[]` 八项与 `missingAuthorities`）。

这些概念的**数据来源与枚举取值属 S4**（八项检查见 DX-CON-001，按范围报告具备／缺失见 DX-CON-002，落库形状见 `S4/src/factory_dhis2/models/environment.py:105-120` 含 `missing_authorities`、`required_scopes`、`result`、`tested_at`、`operator`；单项状态枚举 `PASSED`/`FAILED`/`SKIPPED` 见 `S4 enums.py:264-269`）。下面的 JSON 只是**S1 侧转发投影需要覆盖的概念清单与建议拼法**，最终字段名以 S4 `api` 定稿后 S1 的投影为准：

```jsonc
{
  "environmentKey": "TEST-01",
  "ok": false,
  "result": "FAILED",                    // 总体结论
  "dhis2Version": "2.41.4",
  "testedAt": "2026-09-28T09:12:03Z",
  "requiredScopes": ["discovery","import"],
  "checks": [                             // 八项，逐项可显示
    {"name":"addressReachable","state":"PASSED","detail":null},
    {"name":"tlsValid","state":"PASSED","detail":"TLS 1.3"},
    {"name":"authentication","state":"PASSED","detail":null},
    {"name":"versionIdentified","state":"PASSED","detail":"2.41.4 via /api/system/info"},
    {"name":"versionConstraint","state":"PASSED","detail":"matches 2.41.x"},
    {"name":"metadataRead","state":"PASSED","detail":"scope=discovery"},
    {"name":"metadataImport","state":"FAILED","detail":"403 on authority M:metadata:add"},
    {"name":"serviceAccountRole","state":"SKIPPED","detail":"not required"}
  ],
  "missingAuthorities": { "import": ["M:metadata:add"] },   // DX-CON-002，按范围归组
  "capabilityProfileArtifactId": "bart_01H...",
  "message": "元数据导入权限缺失"
}
```

### 4.4 能力档案与新鲜度转发投影需覆盖的概念　【B 类·定型】

```jsonc
{ "environmentKey":"TEST-01", "profileVersion":"Dhis241Profile",
  "dhis2Version":"2.41.4", "probedAt":"...", "stale":false, "staleReason":null,
  "freshness":"VALID",                     // VALID | EXPIRED | VERSION_CHANGED（大小写敏感，S4 enums.py:181-186）
  "artifactId":"bart_01H...", "contentHash":"sha256:...",
  "content": null }                        // includeContent=true 时为 CAPABILITY_PROFILE 文档
```

现 `CapabilityFreshness` DTO 只有 `{state,capability_profile_artifact_id,version}`（`ports/dto.py:258-262`），且 `state` 是无约束 `str`。索引字段事实见 `S4 models/profile.py:18-36`（含 `stale_reason`、`idempotency_key`）。

**注意**：`content` 的字段集依赖 S4 的契约包（CAPABILITY_PROFILE 产物格式），该包目前不存在（S5IF-UP02），因此 `includeContent` 的定型延后，但**索引层字段可立即实施**。

### 4.5 S5 需要但依赖上游的部分

`content`（`includeContent=true`）、Discovery 的对象级分页列表（UI-REV-003"按 Registry／按对象类型"视图与 UI-DEP-003 候选对象）都依赖 DISCOVERY_RESULT / CAPABILITY_PROFILE 的产物内容格式发布，见 S5IF-UP01/UP02/BP11。

---

## 5. Blueprint、Registry 与 Schema（转发 S2）——S5IF-BP

> 分类：**A 类 2 条**（S5IF-BP02、BP10，路径出自 S1 §8.1）；**B 类 11 条**（能力出自 S2 SRS 与 S1 §7.2／§8.1"转发 Blueprint、Registry、对象级 Diff"一行，**响应字段一律以 `factory_blueprint.api` 的 DTO 为准，本文不重新定义**）；**C 类 2 条**（BP11 候选对象分页列表、BP14 跨产物校验问题列表，见第 11 章）。BP16／BP17 是后续追加的两条转发读，0.4.0 再补 BP18（补池）与 BP19（`codePrefix`）。
>
> **1.19.0 状态**：BP01～BP19 全部在契约内（Blueprint／Registry 组 15 条路径，另加 `uid-pool/replenish`）。
>
> 现状：BlueprintPort（`ports/protocols.py` 第 29-182 行区段）已有 `get_section_schema:165`、`set_environment_binding:153`、`registry_object_refs:143`、`uid_pool_status:151`、`get_decisions:146`、`diff_objects:207`、`get_current_version`、`get_project_semantics`、`get_draft`、`reproduce:211`；0.2.0 时**缺** Registry 条目读取、Managed Scope、版本列表、条目内容读取、绑定列表、草稿内容读取这 6 类端口方法。S2 侧对应能力已存在（`S2/src/factory_blueprint/readmodel.py`），只是 S1 未声明端口。**1.19.0：这 6 类端口与路由都已就位**，BP01～BP19 全部可调。
>
> 表中"响应"列写的是 **S5 需要显示的概念**与对应 S2 DTO 的位置；S2 的 DTO 字段名是 snake_case（`S2 dto/` 无 camelCase 序列化别名），转发时由 S1 决定投影，本文不裁决命名，仅要求满足 S5IF-G03 的大小写一致性。

| # | 方法与路径 | 优先级 | 落地动作 | 来源 | 关键说明 |
| --- | --- | --- | --- | --- | --- |
| S5IF-BP01 | `GET /api/v1/projects/{projectId}/blueprint/section-schemas` | P0 | 端口+路由 | UI-MAN-002 | 返回 `{currentSchemaVersion,availableVersions,sections:[{section,entryContainer,keyField}]}`。S2 有 `available_versions()`/`current_schema_version()`（`S2 schema/store.py:36-45`）与节布局表（`S2 content/layout.py:46-75`），但**无"列出全部节"函数**，故 S1 需序列化 `Section` 枚举（17 值，见 §5.1） |
| S5IF-BP02 | `GET /api/v1/projects/{projectId}/blueprint/section-schemas/{section}` | P0 | 路由 | UI-MAN-002（SRS §8.1 已声明此路径） | 直接转发 `blueprint.get_section_schema`。返回**自包含**的 Draft 2020-12 文档（`$ref` 已内联到 `definitions`，`S2 schema/store.py:128-180`），S5 用它生成表单（React Hook Form）与 Monaco JSON 校验器，满足 UI-MAN-002 与 BP-SCH-006。未知 section → 404 `NOT_FOUND`；不支持的 `schemaVersion` → 422 `BP_UNSUPPORTED_SCHEMA_VERSION` |
| S5IF-BP03 | `GET /api/v1/projects/{projectId}/blueprint/versions` | P1 | 端口+路由 | UI-REV-001（版本与 Diff 选择器） | 转发 S2 `list_versions(project_id,page,page_size)`（`readmodel.py:79-90`），返回 `VersionInfo` 全字段：`{version,parentVersion,schemaVersion,changeLevel,contentHash,artifactId,status,sourceArtifactIds,decisionIds,createdBy,createdAt,promotedFromStage}`（`S2 dto/blueprint.py:33-51`） |
| S5IF-BP04 | `GET /api/v1/projects/{projectId}/blueprint/entries` | P1 | 端口+路由 | UI-REQS-004、UI-WB-004、UI-REV-001/002 | 转发 S2 `get_entries(projectId, EntrySelector, version)`（`readmodel.py:145-182`）。查询参数 `sections[]`、`businessKeys[]`、`decisionIds[]`、`pageKeys[]`、`source=APPROVED|DRAFT`、`version`。响应 `{version,source,entries[],notFound[]}`；`entries[]` 为原始节 JSON（camelCase）加 `_section`、`_path` 两个定位键（`readmodel.py:221-232`）——**请保留这两个键**，S5 的检查器与"来源跳转"依赖它们 |
| S5IF-BP05 | `GET /api/v1/projects/{projectId}/blueprint/draft` | P0 | 端口+路由 | UI-WB-003（上下文要显示草稿及哈希）、UI-MAN-001/003 | **【裁决 2026-09-29】缩小范围**（见第 11.5 节 UP03 的回答）：S5 **不要求**整份草稿内容读取，条目内容一律走 S5IF-BP04 的 `source=DRAFT`。因此本条只需返回草稿元数据 `{draftId,baseVersion,stage,stageScope,status,contentHash,promotable,openPatchCount,changeRequestId,document}`（`services/blueprint_reads.py:draft`）。**唯一硬要求**：S1 必须能由 `projectId`（＋阶段）解析出当前活动的 `draftId` 并随阶段或工作流视图返回——S5 不持有业务状态、不能猜该 ID（UI-BR-02）。**`document` 恒为 `null`**（S2 未导出整份读取，UP03），是显式的"没有这个读法"，不是遗漏。**首个设计阶段没跑过时没有草稿**：返回 404 `NOT_FOUND` + `details.reason=NOT_GENERATED` + `details.requiredStage=REQUIREMENTS`（CP-UI-022，1.16.0 之上修正）；S5 据此渲染空态并引导去 REQUIREMENTS，**不得**把它当"对象不存在"报错，也**不得**期望 200 空体（见 §5.2） |
| S5IF-BP06 | `GET /api/v1/projects/{projectId}/registry` | P1 | 端口+路由＋上游 | UI-REV-003（按 Registry 视图）、UI-WB-005（检查器） | 转发 S2 `get_registry(project_id, version, environment_key)`（`readmodel.py:278-288`），字段：`{businessKey,dhis2Type,disposition,status,uid,code,managed,controlledFields,decisionId,observed:{sourceEnvironment,observedLastUpdated,observedHash,discoveryResultId},environmentBindings}`（`S2 dto/registry.py:18-44`）。**两点需 S2 确认（S5IF-UP05）**：`environmentBindings` 只含单环境且仅在传入 `environment_key` 时填充（`S2 registry/queries.py:72-74`），故跨环境一览需 S1 多次调用或 S2 提供更宽的读取；`observedHash`/`discoveryResultId` 位于 `observed` 子对象而非顶层——UI-WB-005 要求 UID/code/Disposition 直接可取，S1 投影时可拍平。`disposition` 取值 `CREATE`/`UPDATE`/`REUSE`/`REFERENCE`/`EXTERNAL`/`DEPRECATED`（`S2 enums.py:110-118`），`status` 取 `PROPOSED`/`APPROVED` |
| S5IF-BP07 | `GET /api/v1/projects/{projectId}/registry/managed-scope` | P1 | 端口+路由 | UI-DEP-002（Managed Scope 外引用计数）、D-03 | 转发 S2 `get_managed_scope(project_id, version, environment_key)`（`readmodel.py:291-295`）→ `[{businessKey,dhis2Type,uid,disposition,code,controlledFields,observedLastUpdated,observedHash}]` |
| S5IF-BP08 | `GET /api/v1/projects/{projectId}/uid-pool` | P1 | 路由 | UI-MAN-003（`UID_POOL_EXHAUSTED` 时界面只显示等待） | 端口 `uid_pool_status:151` 已有；返回 `{projectId,available,lowWaterMark,sourceEnvironment,needsReplenishment}`（`S2 dto/registry.py:77-84`） |
| S5IF-BP09 | `GET /api/v1/deployment-runs/{runId}/environment-bindings` | P1 | 端口+路由＋待确认 | UI-DEP-003 | 转发 S2 `get_bindings(project_id, version, environment_key)`（`readmodel.py:298-313`）→ `BindingInfo`。⚠️ **两点需 S2 确认（S5IF-UP05）**：① `BindingInfo` 已声明 `observedLastUpdated` 与 `discoveryResultId`，但经公共 API 取得时恒为 `None`（`readmodel.py:302-311` 对比 `dto/registry.py:69-71`），而 UI-DEP-003 与 D-02 的基准判定需要 `observedHash`/`discoveryResultId`/`pendingReparse` 三元组；② 完整三元组目前只在未导出的 `registry/queries.binding_rows()`（`queries.py:137-160`）中。若 S2 确认公共 API 不提供，则 S1 需改由 `get_registry` 的 `observed` 子对象拼装。其余字段：`resolution: AUTO\|MANUAL`、`resolvedBy`、`note`、`pendingReparse` |
| S5IF-BP10 | `PUT /api/v1/deployment-runs/{runId}/environment-bindings/{businessKey}` | P1 | 路由 | UI-DEP-003（SRS §8.1 已声明此路径） | 端口 `set_environment_binding:153` 已有（参数 `project_id,version,environment_key,business_key,uid,note`）。S1 需从 `runId` 解析 `version` 与 `environmentKey`。请求 `{uid, note}`；`note` 必填（S2 `registry/binding.py:303` 缺 note 抛 `REGISTRY_ERROR`），S5 界面已强制填写。`version`/`environmentKey` 不出现在 URL，由 S1 解析后转发，避免 S5 猜测 |
| S5IF-BP11 | `GET /api/v1/deployment-runs/{runId}/environment-bindings/{businessKey}/candidates` | **P1** | 端口+路由（**归属改 S4**） | UI-DEP-003"选择该环境中的候选对象" | **【裁决 2026-09-29】采纳，但实现归属改为 S4**：候选对象来自 DISCOVERY_RESULT，该产物格式归 S4 所有；若由 S1 解析，S1 就依赖了 S4 的内部格式。故由 **S4 新增只读函数** `search_discovery_objects(discovery_artifact_id, dhis2_type, query, limit, offset)`——只读产物内容、**不访问 DHIS2**（S5IF-UP10），S1 负责转发与 camelCase 投影。定级 **P1**（非 P2）：MVP 的 UI-DEP-003 就要用。过渡：S5 暂用 `GET /artifacts/{id}/content` 自行筛选，但受 UP02 格式未定与 5,000 对象规模（NFR-CAP-001、UI-REV-006）限制，不可长期保留 |
| S5IF-BP12 | `GET /api/v1/projects/{projectId}/blueprint/diff` | P1 | 路由 | UI-REV-001（Diff 视图）、UI-WB-004 | 端口 `diff_objects:207` → S2 `diff_versions`（`S2 diff/service.py:19-30`）。响应 `{projectId,kind,fromVersion,toVersion,environmentKey,objects[],counts}`，`objects[]`＝`{businessKey,dhis2Type,uid,state,disposition,section,changes:[{field,before,after}]}`，`state` ∈ `ADDED`/`UPDATED`/`UNCHANGED`/`REFERENCE_ONLY`/`REMOVED`。⚠️ **版本 vs 草稿的 Diff 不存在**（`S2 diff/` 全无 draft 引用，见 S5IF-UP04），MVP 的"草稿相对当前版本的改动"无处可取 |
| S5IF-BP13 | `GET /api/v1/projects/{projectId}/decisions` | P1 | 路由 | UI-WB-005（决策）、UI-GATE-001（为什么） | 端口 `get_decisions:146` 已有；返回 `{decisionId,type,subject,selected,alternatives,rationale,risks,confidence,impactLevel,confirmation,affectedArtifacts}`（`S2 dto/registry.py:87-102`）。⚠️ 0.2.0 时 S1 以 `by_alias=False` 透出为 snake_case（`services/gates.py:775`），**1.19.0 已改为 `model_dump(by_alias=True)`（`services/gates.py:1024`）**；`impactLevel` 仍不做枚举强制（`S2 registry/queries.py:100`），S5 按可扩展枚举渲染 |
| S5IF-BP14 | `GET /api/v1/workflows/{workflowId}/validation-issues?runId=&level=&sourceStage=&ruleId=&businessKey=` | P1 | 端口+路由 | UI-REV-005（按级别／来源阶段／规则／对象筛选） | **【裁决 2026-09-29】采纳，并统一 Issue 模型**。问题散落在两类报告中：S2 的 VALIDATION_REPORT 与 S4 的 DHIS2_IMPORT_REPORT。定案三步：① **Issue 结构以 S2 的 BP-VAL-001 为唯一定义**，级别字段统一叫 **`level`**（`BLOCKING｜WARNING｜INFO`），S4（DX-EXE-027）与 S3 均遵循；② **S2 新增 `query_issues(report_artifact_ids, filters, paging)`**，S4 的导入报告问题按同一结构写入，故 S2 可一并查询；③ S1 按左列暴露端点并做 camelCase 投影。这同时消除了 S2 用 `level`、S3 用 `severity` 的分歧（`S3 dto/common.py:105-138` 需改名）。S2 现成形状见 `S2 dto/issue.py:35-73`，其 `summary()`（`:63-73`）给出七字段精简形可直接用于列表行 |
| S5IF-BP15 | `GET /api/v1/workflows/{workflowId}/nrr` | P1 | 端口+路由＋待确认 | UI-REQS-004（按类别展示 NRR、来源位置、置信度、跳回原文） | 带 `category` 的 NRR 只存在于 S3 的 `NRR` 产物内；S2 的 `requirements.items` 因 `additionalProperties:false` **放不下 `category`**，其字段为 `{businessKey,statement,source{artifactId,location,decisionId,note},rationale,priority,status,confidence,actors,openQuestions}`（`S2 schemas/1/sections.json`），S3 明确记录了丢弃 `category` 的投影（`S3 parsing/base.py:148-165`）。13 个类别定义在 `S3 parsing/base.py:43-58`。**【S5 建议，见第 11.5 节 UP06】由 S3 提供 NRR 产物读取函数、S1 转发**（解析记录归 `ai` schema，符合 D-14），**不建议给 S2 的封闭 Schema 加 `category`**；S5 的 REQUIREMENTS 视图分两块显示（S3 的 NRR 含类别／S2 已批准条目无类别）。过渡：M1 先按 S5IF-BP04 的条目视图显示，不按类别分组 |
| S5IF-BP16 | `GET /api/v1/workflows/{workflowId}/object-graph?runId=&blueprintVersion=&dhis2Type=&businessKey=`（`GET /api/v1/projects/{projectId}/object-graph` 保留为已弃用别名，`view`／`environmentKey`／`q`／`limit` 不再有意义） | P1 | 端口+路由（S2 需新增关系读） | UI-REV-002（对象图）、UM-REV-002 | 响应为 `WorkflowObjectGraphView`：`kind`、`blueprintVersion`、`buildId`、`runId`、`schemaVersion`、`nodes[]`（仅 businessKey／dhis2Type／uid／disposition／managed／inPayload／inDegree／outDegree）、`edges[]`、`orphans[]`、`filters`、`unresolvedRegistryRefs[]`；`matched`、`counts`、`truncated`、`total`、`view` 已移除，计数由前端在返回集上算，孤立与断点由 S2 在完整图上算（CP-UI-020、BP-TD-009） |

| S5IF-BP17 | `GET /api/v1/projects/{projectId}/package-files?version=&environmentKey=` | P1 | 端口+路由（**归属 S2**） | UI-REV-003（元数据审核按文件视图） | **文件分组的数据由 S2 提供**：包内文件清单与哈希已存在 `bp.build_record.output_hashes`（键为 `METADATA_PACKAGE/<路径>` 或 `METADATA_BASELINE/<路径>`），但未经 `api` 暴露；若由 S1 解析包字节，S1 就依赖了 S2 的内部格式（与 S5IF-BP11 同一理由）。故 S2 新增只读 `get_package_manifest(project_id, version, environment_key, *, session)`，返回 `PackageManifest{projectId, blueprintVersion, buildId, kind, environmentKey, packageHash, files[]}`，`files[]`＝`{path, role, contentHash, dhis2Type, objectCount}`；`role` ∈ `payload｜manifest｜graph｜report｜view｜document`；`objectCount` 对不含对象的文件为 **null**（不是 0，否则会被读成空包）。S1 只转发并做 camelCase 投影。**未编译过的版本是 404 `NOT_GENERATED` + `requiredStage=HANDOFF`**（`rest/routes/blueprint.py:243-244`，CP-UI-022），界面据此显示『尚未编译』并引导去 HANDOFF，而不是画一棵并不存在的路径树，也不是 200 + 空清单（见 §5.2）。对象的身份仍只有 Registry 一个答案（与 S5IF-BP16 同一原则）；`README.md` 一类的文件不计数。 |
| S5IF-BP18 | `POST /api/v1/projects/{projectId}/uid-pool/replenish` | P1 | 路由（S2 `replenish`，标 `@external_io`） | UI-MAN-003（`UID_POOL_EXHAUSTED` 时人工补池）、BP-REG-008a | 请求体可省或 `{environmentKey?}`（缺省＝设计参照环境）；响应整行 `UidPoolView`＝`{projectId,available,lowWaterMark,sourceEnvironment,needsReplenishment}`。**这是 `uid_pool.replenish` 动作唯一的写路径**：此前该动作在 `allowed-actions` 里恒答"可用"，而全库无人调用，手工提交第一条 `CREATE` 必撞 `UID_POOL_EXHAUSTED`，界面却提示"重试"——重试必然同样失败。补池会访问目标实例的 UID 端点，故在 S1 事务之外执行；幂等由 `Idempotency-Key` 承担。**注意编号**：1.19.0 的 `x-requirements` 把本路径挂在 `S5IF-BP09` 上（与"环境绑定读取"重号），S1 应改挂 `S5IF-BP18`（见 §15） |
| S5IF-BP19 | `POST /api/v1/projects` 请求体字段 `codePrefix` ＋ 读回 | P1 | 字段 | UI-PRJ-002（新建项目向导）、BP-REG-009、FR-TST-002 | 创建时可选，**留空即 S2 默认 `GEN`**；非空时匹配 `^[A-Za-z][A-Za-z0-9_]{0,15}$`（S1 `rest/schemas/requests.py:_CODE_PREFIX`）。它写进 Blueprint 的 `generationPolicy.codePrefix`，S2 用它派生每个受管对象的 `code` 并**替换作者自写的 code**——因此这是让 code 能指回创建它的那次 Run 的唯一时机，`POST /stages/{id}/patches` 改 code 无效。**读回**走 S5IF-BP04（`section=generationPolicy`），`ProjectView` 不含此字段、创建响应也不回显。⚠️ 两处取值域不一致待收敛：S2 的 schema 是 `^[A-Z][A-Z0-9_]{0,15}$`（只收大写），S1 的请求校验收小写——S5 的表单在 S1 收紧前**只允许大写**，见 §15 |

### 5.1 `section` 取值清单（S5IF-BP01/02 的路径参数枚举）`project`、`requirements`、`domain`、`useCases`、`actors`、`organizationModel`、`accessModel`、`architecture`、`mapping`、`registry`、`pages`、`apiIntents`、`uiIntents`、`rules`、`fixtures`、`environmentConstraints`、`generationPolicy`（共 17 项，与 `S2/src/factory_blueprint/enums.py:15-34` 的 `Section` 枚举一致，数据文件 `S2/src/factory_blueprint/schemas/1/sections.json`）。

整对象型（无按键条目的节）：`project`、`generationPolicy`。条目容器与键字段（`S2 content/layout.py:46-75`）：多数为 `businessKey`；`/architecture/decisions` 为 `decisionId`；`/pages/items` 为 `pageKey`；`/apiIntents/items` 与 `/uiIntents/items` 为 `intentKey`。

### 5.2 派生产物的"尚未生成"语义（CP-UI-022）

派生产物（草稿、对象图、包文件清单、前端包、Run 的执行/校验槽位）在产生它的阶段跑完之前并不存在。S1 的统一答复是 **404 `NOT_FOUND` + `details.reason=NOT_GENERATED` + `details.requiredStage`**，`requiredStage` 点名"跑完哪个阶段就有"（`REQUIREMENTS`／`BASELINE_COMPILE`／`BIND_COMPILE`／`HANDOFF`）。`details.reason` 的另一取值 `NOT_EXIST` 才是"这个对象真的不在"。

对 S5 的约束（也是本条要 S1 守住的口径）：

1. **不得用 200 + 空体／空数组**表示"还没有"——两种答复在界面上是两件事（空态引导 vs 错误弹窗）；
2. 受影响条目：S5IF-BP05（草稿）、BP16（对象图）、BP17（包文件清单）、BP13／BP14 之外的派生产物，以及 S5IF-FLD05 的 `snapshot`／`validation`／`execution` 槽位（槽位 `null` 表示"该阶段还没产出"，与 404 不同——summary 本身永远 200）；
3. `requiredStage` 是**引导**，不是权限或状态错误：S5 显示"先去完成 X 阶段"，并在 `allowed-actions` 允许时给一个直达按钮。S5M 的 `queries.ts` 已按此把 `NOT_GENERATED` 解析为 `undefined`（空态），而不是抛错。

---

## 6. Governance（转发 S3）——S5IF-GOV

> 分类：**B 类 20 条全部**（GOV20 由 0.4.0 补入，契约 1.16.0 新增）。S1 §7.2 已把"Governance 管理（供公共 API 转发）"列为 S1 需要并对外提供的能力，S1 §8.1"转发"行亦已列 Prompt、Model、数据策略、预算、评测；因此本章**不是新增需求**，只是把这行笼统规定拆成可实施的清单。所有字段内容以 `factory_intelligence.api` 为准，路径为本文建议。**本组无 A 类、无 C 类**。
>
> 现状（0.2.0）：S3 的全部治理能力收敛在一个无类型门面上——`governance(action, payload) -> Any`（`S3/src/factory_intelligence/api.py:374-381`，实现 `governance/dispatcher.py:482-490`），**每个返回都是手工拼的 camelCase dict，没有任何 Pydantic 模型**（`dispatcher.py` 全文）。S1 的端口同样是 `governance(action, payload)`（`ports/protocols.py:293`）。
>
> 因此本组 19 条的落地动作统一标为"门面契约化"：S1 须为每个 action 定义请求／响应模型，即使内部仍调用同一个门面。这是 UI-ADM-003 与 UI-APP-007 能同时成立的唯一办法。S3 的 `action` 词表见 `dispatcher.py:453-475`，未知名抛 `AI_GOVERNANCE_ACTION_UNKNOWN`（`details` 含 `known` 列表）。
>
> **1.19.0 状态**：20 条全部在契约内（`/governance` 18 条路径／24 个操作 ＋ `GET /projects/{id}/effective-policy`）。S1 侧已为每个 action 建立独立请求／响应模型（`rest/schemas/governance.py`，817 行），门面契约化完成；GOV20 走的是新端口 `check_provider_health`，不是门面 action。

| # | 方法与路径 | 优先级 | 对应 action | 请求／响应字段（camelCase，按 S3 实际返回） | 来源 |
| --- | --- | --- | --- | --- | --- |
| S5IF-GOV01 | `GET /api/v1/governance/providers` | P0 | `provider.list` | `{items:[{id,name,providerClass,endpoint,enabled,adapter,apiKeySecretRef}]}` | UI-ADM-003 |
| S5IF-GOV02 | `POST /api/v1/governance/providers` | P0 | `provider.register` | `{name,providerClass,endpoint,apiKeySecretRef?,adapter?,enabled?,operator}`；`providerClass` ∈ `LOCAL_ONLY`/`PRIVATE_NETWORK`/`APPROVED_EXTERNAL`/`UNRESTRICTED`；`apiKeySecretRef` **可省**（契约 1.7.1）：省略或空串表示"这个地址不需要 `Authorization` 头"，这是本机 Ollama / LM Studio / llama.cpp 的常态，也是目录里 `local` 条目的默认形态；非空时必须是引用形式，写字面值 → 422（`S3 llm/registry.py:136`）。界面不得为省掉这一栏而编一个不存在的引用名——登记会通过、解析会失败 | UI-ADM-002/003、S5IF-G09 |
| S5IF-GOV03 | `PATCH /api/v1/governance/providers/{name}` | P1 | `provider.enable` | `{enabled,operator}` → `{ref,enabled}` | UI-ADM-003 |
| S5IF-GOV04 | `GET /api/v1/governance/models` | P0 | `model.list` | `{items:[ModelView]}`，`ModelView`＝`{id,provider,model,ref,providerClass,capabilities{structuredOutput,vision,maxContextTokens},enabled,unitPriceInputPer1k,unitPriceOutputPer1k,applicableAgents:{agentType:priority},lastEvalScore,lastEvalRunId}`（`S3 governance/dispatcher.py:161-179`） | UI-ADM-003 |
| S5IF-GOV05 | `POST /api/v1/governance/models` | P0 | `model.register` | `{provider,model,capabilities,unitPriceInputPer1k,unitPriceOutputPer1k,applicableAgents,enabled,operator}` | UI-ADM-003 |
| S5IF-GOV06 | `PATCH /api/v1/governance/models/{ref}` | P1 | `model.enable`（沿用动作名） | **已扩成真编辑（契约 1.15.0）**：请求体是差量，`{capabilities?,unitPriceInputPer1k?,unitPriceOutputPer1k?,applicableAgents?,enabled?,endpoint?,apiKeySecretRef?}`，**没点名的列一律不动**；一个字段都没有 → 422（`details.fields[]` 说明"至少要给一个字段"）；多余字段 → 422（`extra="forbid"`，`capabilities` 本身仍是自由映射——它要能表达"只改这一个能力"）；**字段值给 `null` 视为没点名**（不写回）。响应是整行 `ModelView`（含 `endpoint`/`apiKeySecretRef` 的回填值）。`capabilities` 也是差量：只给 `maxContextTokens` 时另外两个开关保持原值；`applicableAgents: {}` 是值（"任何 Agent 都不许用"），不是缺省。`apiKeySecretRef` 非空时必须是引用形式，写字面值 → 422。授权沿用 `governance.model.enable`（与 GOV03 的提供方 PATCH 复用 `provider.enable` 同一手，action 词表不变）。之所以不能拿"重新登记"当编辑：`POST` 会把 `endpoint`／`apiKeySecretRef` 按缺省写回 NULL，静默清掉模型级覆盖，且审计里没有 `before`；现在 S3 的 `update_model` 记 `before`／`after` 并标为编辑动作 | UI-ADM-003 |
| S5IF-GOV07 | `GET /api/v1/governance/prompts` | P1 | `prompt.list` | `{items:[{id,name,version,qualifiedName,agentType,blueprintSchemaRange,dhis2VersionRange,prohibitions,sourceHash,active}]}`；`active` 由激活表算出（`dispatcher.py:187-200`） | UI-ADM-003 |
| S5IF-GOV08 | `GET /api/v1/governance/prompts/{name}/versions/{version}` | P1 | `prompt.get` | `{id,name,version,agentType,systemRules,body,inputSchemaRef,outputSchemaRef,blueprintSchemaRange,dhis2VersionRange,prohibitions,applicableStages,sourceHash}`；未找到 → 404（`AI_PROMPT_NOT_FOUND`） | UI-ADM-003 |
| S5IF-GOV09 | `POST /api/v1/governance/prompts/activations` | P1 | `prompt.activate` | `{agentType,promptName,promptVersion,modelList[],operator}` → `{agentType,promptVersionId,modelList,evalRunId,regressionPassedRef,activatedBy}`。**评测门槛须映射为可显示的 409**：缺通过评测或回归 → `AI_PROMPT_ACTIVATION_BLOCKED`（`S3 prompts/activation.py:202,207,223`；通过分默认 `0.95`，`S3 config.py:69`）。S5 据此实现 UI-ADM-003 的"缺失时不可切换" | UI-ADM-003 |
| S5IF-GOV10 | `GET /api/v1/governance/prompts/activations` | P1 | （S3 有 `list_activations`；1.19.0 已由 S1 建路由并定型） | `{items:[{agentType,promptVersionId,name,version,modelList,activatedBy,evalRunId,regressionPassedRef}]}` | UI-ADM-003 |
| S5IF-GOV11 | `GET`＋`POST /api/v1/governance/regression-records` | P2 | `regression.record`（读为 `list_regressions`） | 写：`{promptName,promptVersion,recordedBy,passed,model,source,note}` → 201 `{id,passed,promptVersionId}`。读：`?promptName=&promptVersion=` → `{items:[RegressionRecordView]}`，回答"这个 Prompt 版本有没有 Golden Project 结果"（AI-REG-003）——GOV09 的激活被 409 拒绝时，界面据此直接指出缺哪一条，而不是让操作者去猜 | UI-ADM-003 |
| S5IF-GOV12 | `GET /api/v1/governance/policy` | P0 | `policy.org.get` | `{effective:{allowed:{PUBLIC:[…],INTERNAL:[…],CONFIDENTIAL:[…],RESTRICTED:[…],PHI_PII:[]},sources[],warnings[]},isDefault}`（`S3 policy/model.py:170-179`）。要求同时返回**项目级有效策略**（D-07：组织 ∩ 全部目标环境最严者），S5 需在项目页显示：`GET /projects/{id}/effective-policy` | UI-ADM-003、D-07 |
| S5IF-GOV13 | `POST /api/v1/governance/policy/relaxations` | P1 | `policy.org.relax` | `{change:{level,addClasses[]},reason,operator,scope?}` → `{id,scope,operator,roleChecked,roleGate}`。`reason` 必填（空 → 422）；放宽 PHI_PII → 403（`AI_POLICY_RELAXATION_DENIED`，`S3 policy/orgpolicy.py:94`）；超出矩阵上限 → 同码（`:175`）。UI-ADM-003 的"需填写理由"据此实现 | UI-ADM-003 |
| S5IF-GOV14 | `GET /api/v1/governance/evaluations` | P1 | `eval.list` | `{items:[{id,agentType,prompt,model,evalSetVersion,score,passed,summary}]}` | UI-ADM-003 |
| S5IF-GOV15 | `GET /api/v1/governance/evaluations/{evalRunId}` | P1 | `eval.get` | 上表 + `caseCount`；须能取到逐例结果（S3 `EvalResult.cases[{caseId,passed,score,kind,diff}]`、`injectionCases`、`injectionPassed`，`S3 dto/result.py:152-179`）。0.2.0 时门面不返回 `cases`，**1.19.0 的 `EvaluationView` 已含 `cases`/`injectionCases`/`injectionPassed`/`caseCount`** | UI-ADM-003 |
| S5IF-GOV16 | `POST /api/v1/governance/evaluations` | P2 | `run_evaluation`（**不是** `eval.run`） | `{agentType,qualifiedPrompt,model,evalSetVersion?,operator}` → `EvalResult`。幂等必需。⚠️ 门面的 `eval.run` 恒抛 `VALIDATION_ERROR`（`details={use:"run_evaluation"}`，`dispatcher.py:409-416`），S1 必须走 `api.run_evaluation` 端口路径；且公开签名收一个限定串 `prompt_version`（`name.vN`）而内层是 `(name,version:int)`，请保持一致 | UI-ADM-003 |
| S5IF-GOV17 | `GET /api/v1/governance/usage` | P1 | `usage.rollup` | `?projectId=&groupBy=stage,agent,model` → `{groupBy,dimensions,items:[{projectId,workflowId,stage,agentType,model,provider,calls,inputTokens,outputTokens,estimatedCost,failedCalls}]}`（`S3 dto/result.py:136-149`）。⚠️ 未知维度抛**裸 `ValueError` 而非 FactoryError**（`S3 cost/usage.py:171-175`），S1 须先校验，否则变成 500 | UI-ADM-003 |
| S5IF-GOV18 | `GET`＋`PUT /api/v1/governance/budget` | P2 | `budget.get`＋`budget.set` | `{projectId,state,spent,limit,currency,thresholdReached}`，`state` ∈ `UNSET`/`OK`/`WARNING`/`EXCEEDED`。**预算写入已随 M4 交付**：S3 的 `set_budget` 在 `DELIVERED_CAPABILITIES` 内（`S3 feature.py`），S1 提供 `PUT /governance/budget?projectId=`（body `{limit,currency,thresholds}`，清除则 `limit:null`），`allowed-actions` 以 `governance.budget.set` 报告可用性；写成功回显新的 `state`，`BUDGET_EXCEEDED` 仍按 §8.3 信封返回。要求：S5 端点存在、可读可写；`Idempotency-Key` 已按 `PUT` 声明（`rest/middleware.py` 的 `IDEMPOTENT_METHODS`），带与不带都只影响重放，不改变结果 | UI-ADM-003 |
| S5IF-GOV19 | `GET /api/v1/governance/agents` | P2 | `agent.list` | `{milestone,items:[{agentType,stage,milestone,enabled,promptName,perPage,patchMode}]}`。S5 用它把阶段与 Agent 类型对齐（UI-WB-009 的进度与取消入口） | UI-WB-009 |
| S5IF-GOV20 | `POST /api/v1/governance/providers/{name}/test-connection` | P1 | 新增端口 `check_provider_health` ＋ 路由（契约 1.16.0 新增） | **形态与环境的 `test-connection` 同构**：无请求体；响应 `ProviderHealthView`＝`{ref,healthy,detail}`（`detail` 是传输层自己的句子，如 `provider answered HTTP 401`、`model endpoint unreachable: ConnectError`）。**语义三条**：① **只要探测跑完就是 200**——地址写错、密钥被拒、连不通、乃至 `{name}` 根本没登记，都是**答案**而不是错误（未登记 → `healthy=false` 且 `detail` 非空），只有无权限才 4xx；② **绝不回显密钥引用或值**（`ProviderHealthView` 里没有 `apiKeySecretRef`，AI-LLM-005）；③ 探测是 S3 对提供方 `GET /models` 的一次调用，**5 秒预算、不生成、不计费、不外发项目内容**。用 `POST` 而非 `GET`：这会产生一次对部署之外的访问，浏览器预取一个 GET 就等于在别人的页面加载里塞一个 5 秒 socket。授权 `governance.manage`；`allowed-actions` 里**没有** `provider.test_connection` 这个名字，S5 只能按治理管理权限决定按钮可见性（见 §15）。**【实现现状 2026-10-25】** 真实 S3 的 `check_provider_health` 对未登记名字抛 `NOT_FOUND`，S1 的适配器/路由未把它转成答案，因此真实链路当前回 404；S1 的测试替身回 200。验收 S5IF-AC23 目前只在替身下成立。目标语义仍是 200，修法是在 S1 适配器把未登记转成 `{healthy:false,detail}`（或 S3 直接返回答案） | UI-ADM-003、UI-SET-006、AI-LLM-005 |

补充要求（**1.19.0 已满足**）：`GET /api/v1/governance/models` 与 `providers` 的响应必须含 `endpoint` 与 `apiKeySecretRef`（只回引用，不回值）。现状：`ProviderView`＝`{id,name,providerClass,endpoint,enabled,adapter,apiKeySecretRef,vendor,suggestedModels}`，`ModelView` 同名字段齐备；模型自己没设地址/密钥时，S1 用所属提供方的值回填（`services/governance.py:110-124`），因此界面显示的是"实际会用的地址"。写侧不受回填影响：`PATCH` 只写被点名的列（GOV06），把回填值当覆盖发回去不会把 NULL 变成一行假覆盖。

---

## 7. 输入解析、列映射与问题解释（转发 S3）——S5IF-INP

> 分类：**A 类 1 条**（INP05）；**B 类 6 条端点 + 1 条字段**（INP01、INP03、INP06、INP07、INP08、INP09 与 INP02；能力出自 S1 §7.2 的 S3 行"解析输入／查询运行／取消运行"、S3 SRS AI-INP-*、AI-AG-014；INP08／INP09 由 0.4.0 补入）；**本组无 C 类**。INP04 与 INP01 是同一端点的 `options` 用法，不另计端点。
>
> **1.19.0 状态**：9 条全部在契约内（`/projects/{id}/inputs` 5 条路径 ＋ `/artifacts/{id}/explain` ＋ agent-run 两条）。S5 与 S5M 都已用 INP08／INP09 建了"LLM 日志"页（`S5/src/features/inputs/LlmLogView.tsx`、`S5M/src/features/inputs/`），这两个编号在契约里早于本文存在。

| # | 方法与路径 | 优先级 | 落地动作 | 说明 |
| --- | --- | --- | --- | --- |
| S5IF-INP01 | `POST /api/v1/projects/{projectId}/inputs/{artifactId}/parse` | P1 | **仅路由** | S1 的 `services/inputs.py:141-187` 已完整实现（转 S3 `parse_input`、写 `outputArtifactIds`、落 Question、带进度回报），0.2.0 时**没有任何路由，也不在 openapi.json 里**——这是全部缺口里成本最低的一条。**1.19.0 已注册**，`x-requirements` 同时挂 `S5IF-INP01` 与 `S5IF-INP04`。响应 `{status,nrrArtifactId,parserVersion,questions[],rejectedArtifacts[],stageId}`。幂等必需 |
| S5IF-INP02 | `GET /api/v1/projects/{projectId}/inputs`（字段补齐） | P1 | 字段 | 0.2.0 时返回 `{artifactId,filename,mediaType,sizeBytes,contentHash,dataClassification,createdAt}`（`services/inputs.py:124-138`），**没有解析状态**；**1.19.0 已补** `parseStatus`、`errorCode`、`errorMessage`、`parserVersion`、`parserType`、`usedModel`、`parseId`（字段来源 `S3 dto/result.py:61-75`；状态语义 `S3 enums.py:148-155`）。UI-REQS-002 要求逐个输入显示 `PARSED`/`NEEDS_REVIEW`/`FAILED`/`POLICY_DENIED` 与错误。扫描件不支持须显示对应提示（`SCANNED_DOCUMENT_UNSUPPORTED`，UI-REQS-002 / AI-INP-003a） |
| S5IF-INP03 | `GET /api/v1/projects/{projectId}/inputs/{artifactId}/column-mapping` | P1 | 路由＋上游 | UI-REQS-003 的"以表格展示源列与建议目标字段"。S3 有结构化提案但未导出：`TablePlan`（frozen dataclass，含 `category/headers/labelColumn/detailColumns/matches/isOptionList/recognisedBy` 与 `proposedMapping()`，`S3 parsing/columnmapping.py:218-254`）、`Fragment.notes["mappingReview"]`＝`[{sheet,question,proposal,headers,affectedKeys}]`、`["proposedColumnMapping"]`（`columnmapping.py:628-649`）。**目前只有渲染成 Question 文本的形式可见**（`mapping_review_questions`，`columnmapping.py:652-721`，其 `suggestions[].columnMapping` 是可用的过渡数据）。过渡：S5 从 `GET /workflows/{id}/questions` 的 suggestions 里取 `columnMapping`；正式方案请 S1 转发结构化提案（S5IF-UP07） |
| S5IF-INP04 | **同 INP01 端点**（带 `columnMapping` 确认并重新解析，不另计新端点） | P1 | 请求模型 | 复用 INP01，请求体加 `options`：`{columnMapping:{sourceColumn:TargetField},sheet?,delimiter?,hasHeader?,parser?,exportedAs?}`（`S3 dto/request.py:66-80`，AI-INP-006）。要求 INP01 的请求模型即包含 `options`，一条端点覆盖首次解析与改映射后重解析。幂等：同键重复必须返回首次结果，因此**每次用户确认须由 S5 生成新键**（UI-REQ-001） |
| S5IF-INP05 | `POST /api/v1/artifacts/{artifactId}/explain` | P1 | 仅路由 | SRS §8.1 已声明此路径；端口 `explain_issues`（`ports/protocols.py:284-292`）已备。请求 `{projectId,environmentPolicies}`（S1 负责按 D-07 算出有效策略后传入）。⚠️ 返回是 **`AGENT_OUTPUT` 产物 ID 或 `null`，不是说明正文**（`S3 api.py:212-216`、`runtime/explain.py:72-147`）；正文在产物里，形状 `{agentType,kind:"issueExplanation",stage,reportArtifactId,summary,decisions,questions,confidence,model,modelCalls,promptVersions,patch:null}`（`explain.py:502-526`，`patch` 恒为 null 是 AI-AG-006 的要求）。因此**必须成对交付**：INP05 的响应体为 `{explanationArtifactId:string\|null}`，S5 再用现有 `GET /artifacts/{id}/content` 读正文。仅接受 `VALIDATION_REPORT`／`DHIS2_IMPORT_REPORT`，否则 404；未启用阶段 → `AI_FEATURE_NOT_ENABLED` |
| S5IF-INP06 | `GET /api/v1/workflows/{workflowId}/agent-runs/{runId}` | P0 | 路由＋合成 | UI-WB-009（运行中与进度、可取消）、UI-Q-001。端口 `get_agent_run`（`:195-197`）已备。注意 S3 的 `get_agent_run` **不返回 questions 是符合 D-14 的设计**（Question 归 S1 所有，S3 侧注明"文本故意外泄"，`S3 runtime/get.py:3-8`），因此由 S1 用自己的 Question 存储与事件流合成响应，而不是要求 S3 改行为；但 `RUNNING` 被报成 `WAITING_INPUT`（`get.py:28`）需要 S1 用自身阶段状态纠正。响应需含 `{runId,status,summary,confidence,patchIds,agentOutputArtifactId,questions[],issues[],budgetStatus,denialReasons[],rejectedArtifacts[],progress}`。`status` 六值 `COMPLETED`/`WAITING_INPUT`/`FAILED`/`POLICY_DENIED`/`BUDGET_EXCEEDED`/`CANCELLED`（`S3 enums.py:110-119`）。`progress` 取自事件流最新 `agent.progress`（`{step,completedFraction,message}`，`S1 progress.py:60-68`）。UI-WB-009 的"POLICY_DENIED／BUDGET_EXCEEDED 时显示被拒绝产物与预算信息"依赖 `denialReasons[]`＋`rejectedArtifacts[]`＋`budgetStatus` 三者齐备 |
| S5IF-INP07 | `POST /api/v1/workflows/{workflowId}/agent-runs/{runId}/cancel` | P0 | 仅路由 | UI-WB-009"可取消"。端口 `cancel_agent_run`（`:190-192`）已备且已在 `METHOD_MAP`，0.2.0 时**无任何路由**；**1.19.0 已注册**。请求体可空，响应 `{runId,status:"CANCELLED"}`。此动作必须同时进入 `allowed-actions`（见 S5IF-FLD12） |
| S5IF-INP08 | `GET /api/v1/projects/{projectId}/inputs/{artifactId}/llm-log?limit=&offset=` | P1 | 路由 | UI-REQS-002（"这次解析模型实际问了什么"）、M3 的可解释要求。响应 `LlmLogView`＝`{items:[LlmExchangeSummaryView],total,limit,offset,parseRunId,parseStatus}`；每项 `{exchangeId,sequence,status,provider,providerClass,model,inputTokens,outputTokens,latencyMs,requestId,createdAt,completedAt,errorCode,errorMessage}`。**只列摘要，不含消息正文**——正文按需展开（INP09）。列表带 `parseStatus`／`parseRunId`，是为了让界面把"解析失败"与"模型这一轮出了什么"放在同一屏解释；解析进行中 S5 用轮询本端点当进度信号。无记录的输入返回空 `items` 与 200（这是"没调用过"，不是 NOT_GENERATED）。授权 `project.view` |
| S5IF-INP09 | `GET /api/v1/projects/{projectId}/inputs/{artifactId}/llm-log/{exchangeId}` | P1 | 路由 | 单次交换：`LlmExchangeDetailView`＝摘要字段 ＋ `inputArtifactId`、`expiresAt`、`request{agentHint,messages[],schemaName,jsonSchema,maxOutputTokens,temperature}`、`response{text,payload,finishReason,model,requestId,usage,truncated,storageTruncated}`。**`expiresAt` 是保留期**：过期的交换不返回正文（404 或明确的空正文），界面必须按"已过保留期"渲染而不是"没有这次调用"。`response.payload` 是模型的结构化输出原文——S5 只展示，不据此更新任何状态（唯一来源仍是 S1 的解析结果）。⚠️ 与 S5IF-G09 的边界：正文里若含上传材料的原文片段，那是**业务内容**而非凭证；凭证在 S1 的记录里已被抹掉，正文不含密钥 |

---

## 8. 现有端点的字段级补齐与聚合视图——S5IF-FLD

> 分类：**B 类 9 项**（FLD06、FLD07、FLD08、FLD10、FLD11、FLD12 —— 端点已存在且投影归 S1 自己所有，只需补字段或定型；FLD13、FLD14 由 0.4.0 补入）；**C 类 6 项**（FLD01、FLD02 需 S1 新建聚合查询；FLD03、FLD04、FLD05、FLD09 为 SRS 未规定的新端点）。判断依据：若 S5 能用现有端点拼出来且不违反 UI-BR-02，就不列在本章——下列条目都是**拼不出来、或拼一次要发几十次请求**的。
>
> **1.19.0 状态**：本章 14 项全部在契约内。FLD01 的聚合字段见 `StageView`，FLD03/FLD04/FLD05/FLD09/FLD13/FLD14 的路径均已注册；FLD05 的 `changeStats` 仍是空对象，原因在上游（第 9 章 UP12）。

### S5IF-FLD01　阶段视图缺 FR-UI-001 要求的聚合　【新增】（由 FR-UI-001、UI-WB-002 推导）　P0

`stage_view`（`services/workflow.py:54-68`）现有字段：`id`、`workflowId`、`deploymentRunId`、`stageType`、`status`、`attempt`、`inputBlueprintRef`、`outputArtifactIds`、`failureSourceStage`、`feedback`、`startedAt`、`completedAt`。

FR-UI-001／UI-WB-002 要求阶段导航中**每个阶段**显示七项，其中四项无数据可取：

| UI 要求 | 现状 | 要求新增 |
| --- | --- | --- |
| 阶段状态（含 `MANUAL_CHECK`） | ✅ `status` | — |
| 输入完成度 | ❌ | `inputCompleteness:{required:string[],missing:string[],ratio:number\|null}` |
| 产物数量 | ⚠️ 可由 `outputArtifactIds.length` 推 | 保留 `artifactCount` 以便排序 |
| 未决 Question 数 | ❌ | `openQuestionCount`、`highOpenQuestionCount`（UI-Q-002 与 CP-Q-006 的"HIGH 未答阻止批准"要在导航上显示） |
| BLOCKING 问题数 | ❌ | `blockingIssueCount` |
| 最近执行时间 | ⚠️ 只有阶段起止 | `lastRunAt`（取该阶段最后一次 `stage.started`／`agent.progress` 时间，S1 事件流已有） |
| 当前产物版本 | ❌ | `currentArtifactVersion`（该阶段产物所依据的 Blueprint 版本或草稿哈希，UI-WB-003 上下文栏同样需要） |

**为什么必须由服务端给**：S5 若自行聚合，需对 12 个阶段各发 3～4 次请求（约 40 次），且聚合即"从其他数据推断业务状态"，违反 UI-BR-02 与 FR-WF-006。

**【裁决 2026-09-29】采纳，由服务端计算**。各字段的数据来源都在 S1 内部：未决与 HIGH 未决 Question 数、最近执行时间、当前产物版本（即 `inputBlueprintRef` 所指的版本或草稿哈希）、BLOCKING 数（取最近一次校验结果的计数，这属 S1 自己的执行记录，不算冗余）。**整个工作流的阶段列表必须一次查询算完，禁止逐阶段查询。**

**`inputCompleteness` 需新定义**：任何 SRS 都没有定义过"输入完成度"，因此不能凭猜测实现。裁决为：由 **S1 为每种阶段维护一份前置清单**，完成度＝已满足项 ÷ 总项数，并列出缺失项。清单内容：

| 检查项 | 适用阶段 |
| --- | --- |
| 前置阶段是否完成 | 全部阶段 |
| 必需的上游产物是否存在 | 全部阶段 |
| 至少存在一个已解析的输入 | REQUIREMENTS |
| 能力档案有效 | Run 步骤（BIND_COMPILE 及其后） |

响应形状相应定为 `{satisfied:number, total:number, ratio:number|null, missing:[{key:string, label:string}]}`（`label` 需可本地化，S5 直接显示缺失项）。

### S5IF-FLD02　阶段拒绝反馈与问题来源须定型　【调整】UI-WB-007　P1
`feedback` 与 `inputBlueprintRef` 现为自由 JSON。请声明形状：`feedback:{kind:"GATE_REJECTED"|"PATCH_REJECTED"|"VALIDATION",reason:string,sourceStage:string\|null,gateId:string\|null,issues:[Issue],createdAt:datetime}\|null`；`inputBlueprintRef:{kind:"DRAFT"|"VERSION",id:string,hash:string}`。UI-WB-007 需要在阶段顶部原样显示拒绝理由并跳转来源阶段。**【裁决 2026-09-29】采纳并扩展枚举**：`kind` 在 `GATE_REJECTED|PATCH_REJECTED|VALIDATION` 之外，再增加 `POLICY_DENIED`、`BUDGET_EXCEEDED`、`EXECUTION_FAILED`（对应 CP-WF-030 与执行失败场景）。**枚举声明为可扩展**，S5 遇未知值时按通用形态（标题＋理由文本＋可跳转引用）渲染，不做白名单校验，避免 S1 增项即导致 S5 崩溃。

### S5IF-FLD03　跨项目 Gate 列表　【新增】CP-API-001（SRS §8.1 未列）　P0
Dashboard 的"全部待审批 Gate"（UI-PRJ-001）现在无法一次取得：Gate 只有 `GET /workflows/{id}/gates`。要求 `GET /api/v1/gates?status=WAITING&projectId=&type=&limit=&offset=`，响应为统一分页信封，元素＝`GateSummaryView`（`{id,workflowId,projectId,stageId,deploymentRunId,environmentKey,type,status,requiredApprovals,approvalCount,warningCount,unresolvedWarningCount,highOpenQuestionCount,createdAt,projectName}`）。`highOpenQuestionCount`／`unresolvedWarningCount` 是 UI-AC-04（有未处理 WARNING 则批准按钮不可用）在列表页的提示依据。

**【裁决 2026-09-29】采纳**。Gate 数据归 S1 所有，这只是一个简单查询，不涉及跨子系统调用，**纳入 S1 SRS §8.1 的 Gate 分组**（见第 11.4 节文档同步清单）。MVP 无权限控制，返回全部 Gate；M4 起按成员关系与审批角色过滤，实现"待我审批"视图（CP-IAM）。

### S5IF-FLD04　跨项目 Run／执行列表　【新增】　P1
Dashboard 的"最近执行"（UI-PRJ-001）无列表端点（只有按 workflow 的 `/deployment-runs` 与按 ID 的 `/executions/{id}`）。要求 `GET /api/v1/deployment-runs?status=&environmentKey=&outdated=&limit=&offset=`（跨项目），并增补 Run 视图的 `projectId`、`projectName`、`environmentClass`（现 `run_view` 无这三项，`services/workflow.py:89-103`）。

**【裁决 2026-09-29】采纳，附两条约束**：① 默认按 `updatedAt` **倒序**返回（"最近执行"的语义）；② `projectName` 取自 S1 自有数据，而 **`environmentClass` 归 S4 所有，禁止写入 S1 数据库**（D-14、CP-API-007），只能在请求时查询 S4，并按 00 文档的缓存约定最多做 **60 秒进程内缓存**。该端点同样纳入 §8.1（第 11.4 节）。

### S5IF-FLD05　部署控制台聚合视图　【新增】（源自 18.3 线框与 UI-DEP-002）　P0
`GET /deployment-runs/{runId}/summary` 一次返回线框 18.3 与 UI-DEP-002 列出的全部信息。现 `GET /deployment-runs/{id}` 只给 `{run,stages,gates}`（`services/runs.py:881-895`），控制台需要的版本／Profile／连接／漂移／Snapshot／统计都要 S5 另外发 4～6 次跨资源请求，其中漂移、Snapshot、环境类别**根本没有 REST 入口**（S4 转发未做）。

```jsonc
{
  "runId": "run_01H...", "projectId": "...", "projectName": "...",
  "workflowId": "...", "status": "WAITING_APPROVAL", "evaluatedAt": "...",
  "environmentKey": "TEST-01", "environmentClass": "TEST",
  "blueprintVersion": "1.4.0", "outdated": false,
  "dhis2Version": "2.41.4", "profileVersion": "Dhis241Profile",
  "capabilityFreshness": { "state": "VALID", "probedAt": "...", "artifactId": "bart_01H..." },
  "connection": { "ok": true, "result": "PASSED", "missingAuthorities": {}, "lastTestedAt": "..." },
  "drift": { "drifted": false, "checkedAt": "...", "runId": "...", "objects": [] },
  "snapshot": { "artifactId": "bart_01H...", "status": "APPROVED", "contentPurged": false },   // ArtifactSlot
  "validation": { "staticPassed": true, "dhis2ValidatePassed": true,
                  "blockingCount": 0, "warningCount": 3, "reportArtifactId": "..." },
  "approval": { "packageGate": "APPROVED", "deployGate": "WAITING",
                "requiredApprovals": 1, "approvals": 1 },
  "changeStats": { "create": 51, "update": 3, "delete": 0,
                   "externalReferences": 4 },                            // 见 UP12
  "diffReportArtifactId": "bart_01H...",
  "maintenanceActions": [ {"id":"...","label":"...","requiresConfirmation":true} ],
  "payload": { "hash": "sha256:...", "packageArtifactId": "bart_01H..." },
  "commitWindow": { "withinWindow": true, "windows": [], "timezone": "UTC",
                    "emergencyLocked": false },
  "verification": { "lastVerifiedAt": null, "postVerifyArtifactId": null,
                    "consistent": null },
  "execution": { "executionId": null, "status": null,
                 "reportArtifactId": null, "importReportArtifactId": null },
  "stages": [ {"id":"stg_1","stageType":"VALIDATE","status":"PASSED","attempt":1} ],
  "gates":  [ /* GateView，与 GET /gates/{id} 同形 */ ]
}
```

**1.19.0 的两处形状要点**（1.12.0、1.13.0 各改过一次，都会打到 S5 的类型生成）：

1. **`execution.reportArtifactId` 与 `execution.importReportArtifactId` 是 `ArtifactSlot` 对象**（`{artifactId,status,contentPurged}`），不是裸 id；`snapshot` 同形。原先声明成 `string`，而生产者一直返回对象，于是**只要真有执行报告，这个 GET 对任何身份都 500**（1.12.0 破坏性变更）。
2. **`contentPurged` 独立于 `status`**：`ArtifactStatus` 只有 `DRAFT/PROPOSED/APPROVED/SUPERSEDED`，清除是行上的布尔（FD-ART-010）。`status:"APPROVED"` 的槽位既可能能下载、也可能字节已清，**界面必须读 `contentPurged` 才决定给不给下载链接**，否则就是一次点下去 410 的点击。

**阶段级产物仍只在阶段详情里**：`summary.stages[]` 只有 `{id,stageType,status,attempt}`，**没有 `outputArtifactIds`**。BIND_COMPILE 编译出的包/图/清单 id 要从 `GET /stages/{stageId}`（`StageView.outputArtifactIds`，S5IF-FLD01）取，或从 `payload.packageArtifactId`／`diffReportArtifactId` 这两个 summary 已给的槽位取。S5 不要指望一次 summary 拿到每个阶段的产物清单。

**【裁决 2026-09-29】采纳，附一条硬约束**：**这个 GET 不得调用任何会访问 DHIS2 的函数**，否则 CP-NFR-03 的 P95 < 500 ms 无法保证，还会给目标实例带来额外负载（用户只是打开一个面板）。落到具体端口方法上：

| 数据 | 允许的取法 | 说明 |
| --- | --- | --- |
| `drift` | **只返回最近一次检查的结果与时间** | `check_drift` 访问 DHIS2，**不得在此调用** |
| `capabilityFreshness` | **只返回最近一次探测的档案索引**（`S4` 的 `capability_profile_index` 按 `(environmentKey, probedAt)` 取最新） | `check_capability_freshness` 访问 DHIS2，**不得在此调用** |
| `connection` | 最近一次连接测试结果 | 需 **S4 补一个只读查询**（S5IF-UP10） |
| `commitWindow`（含 `emergencyLocked`） | **可实时查询** `get_protection` | 该函数只读 S4 数据库（且 CP-WF-027 本就要求环境保护属性不缓存） |
| `snapshot`／`validation`／`payload`／`verification`／`execution` | S1 自有的 Run、Gate、产物记录与已产生的产物 ID | 无外部调用 |
| `changeStats` | 读已生成的 DIFF_REPORT／环境绑定编译产物 ID 对应的计数 | 由 S1 已登记的构建记录提供。**1.19.0 仍是空对象**：S2 未导出 DIFF_REPORT 的计数读取，`diffReportArtifactId` 已先给出（UP12）；S5 在四个数字全为 0 时显示"尚未汇总"，不要显示"0 处变更" |

需要"立即重新检查漂移"时，**另设 POST 动作**（建议 `POST /api/v1/deployment-runs/{runId}/drift-check`），纳入 `allowed-actions`（S5IF-FLD12），由用户显式触发并显示耗时。不新增任何持久化（CP-API-007、D-14）。

### S5IF-FLD06　Run 视图补 `lastVerifiedAt` 等　【调整】UI-DEP-001　P1
0.2.0 时 `run_view`（`services/workflow.py:89-103`）有 `status`、`outdated`、`blueprintVersion`、`validatePayloadHash`、`commitOperator`，但 UI-DEP-001 要求显示"最近 VERIFIED 时间"，缺 `lastVerifiedAt`、`environmentClass`、`projectId`、`postVerifyArtifactId`。**1.19.0 的 `RunView` 四个字段齐备**（另含 `projectName`、`executionId`、`startedAt`、`completedAt`、`validateOperator`），`environmentClass` 仍是实时取自 S4、不落 S1 库（S5IF-FLD04 的约束）。

### S5IF-FLD07　Gate 的 `warnings` 与 `confirmationItems` 定型　【调整】UI-GATE-002/005　P0
`gate_view`（`services/gates.py:61-88`）的 `warnings[]`、`confirmationItems[]`、`riskSummaryRef`、`blueprintRef` 均为自由对象，而 UI-GATE-002 要求逐条 WARNING 标记"接受/需修正"、UI-GATE-005 要求 DEPLOY_GATE 对备份、每个非默认导入参数、每个维护动作分别确认。请声明：

```jsonc
"warnings": [ {"issueId":"...","ruleId":"...","level":"WARNING","message":"...",
               "businessKey":"...","sourceStage":"MAPPING","remediation":"..."} ],
"confirmationItems": [ {"id":"backup-confirmed","label":"实例数据库备份已由运维确认",
                        "required":true,"kind":"BACKUP"|"IMPORT_PARAMETER"|"MAINTENANCE_ACTION",
                        "detail":{}} ],
"boundObjects": [ {"refType":"BLUEPRINT_DRAFT","refId":"...","contentHash":"sha256:..."} ]
```
`boundObjects` 的 `contentHash` 即 UI-GATE-003 批准时要回带的"页面加载时的绑定对象哈希列表"来源，`refType` 枚举为 `ARTIFACT`/`BLUEPRINT_DRAFT`/`BLUEPRINT_VERSION`/`ENVIRONMENT`/`EXECUTION`（`domain/enums.py:214-222`）——三者形状须保证 S5 能原样回填，否则 `APPROVAL_INVALIDATED` 会误报（`services/gates.py:290-311`）。同时 WARNING 的字段名请与 BP14 统一（S2 用 `level`、S3 用 `severity`）。

### S5IF-FLD08　Question 的整页问卷、`answerMode` 与 `suggestions` 定型　【调整】CP-Q-005、UI-Q-001　P0

**一页问卷一次作答（契约 1.19.0，CP-Q-005）。** 旧的"一题一答"（`POST /questions/{questionId}/answer`）保留给偶发作答的客户端，但整页复核必须走 **`POST /api/v1/questions/answers`**：请求 `{answers:[{questionId,suggestionIds[]?,customText?}]}`（1～200 项），响应 `QuestionnaireAnswerView`＝`{answers:[{questionId,status}],resumed,runId,status,questions:[QuestionView]}`。

规则（S5 必须按此实现，S1 已按此校验）：

| 规则 | 行为 | S5 的对应做法 |
| --- | --- | --- |
| **一次事务、最多一次 resume** | 先在一个事务里记下全部答案，再在事务外 resume 至多一次 | 整页一个"提交"按钮，不要逐题发请求——第二题会在第一题已经跑起来的 run 里到达 |
| **同一阶段、同一工作流** | 全部问题必须属于同一 `stageId`／`projectId`／`workflowId`，否则 422（`details.stageIds[]`） | 问卷按阶段分组渲染，一个阶段一张卡 |
| **一题至多一次** | 重复 `questionId` → 422 | 提交前按 `questionId` 去重 |
| **阻塞题全答完才 resume** | 仍有 `blocking` 未答 → `resumed:false`，答案照样落库 | `resumed:false` 不是失败，是"还差题"，别提示"提交失败" |
| **多轮本地 id** | 第二轮起 S3 用带 attempt 的本地 id（`Q2-1`），S1 按 `(agentRunId, sourceLocalId)` 去重不会把新问题丢掉 | 用 `id` 做列表 key 与去重，**不要**用 `source.localId`（跨轮会撞） |
| **幂等** | 同 `Idempotency-Key` 重试返回首次结果，不写第二份答案 | 每次"提交"生成新键；网络重试用同键（UI-REQ-001） |
| **响应带回剩余开放题** | `questions[]` 是 resume 之后该阶段仍开放的问题（整份 `QuestionView`） | 直接把它当下一轮问卷渲染，**不要**再发一次 `GET /questions`（会有竞态） |

**`QuestionView` 的字段**：`answerMode` ∈ `SINGLE`/`MULTIPLE`（缺省 `SINGLE`，历史问题按单选；多选时 `suggestionIds` 可给多个）、`answer`（`QuestionAnswerView`）、`source{kind,agentRunId,localId}`、`affects[]`、`severity`、`blocking`、`status`、`answeredBy`、`answeredAt`。

**`SuggestionView` 定型**：`{id,text,rationale,changesUid,regenerates[],recommended,columnMapping?}`——`recommended: true` 是模型标出的推荐项，S5 必须在建议行上显示"推荐"标记；`columnMapping` 只在列映射类建议上出现（该组件因此允许任意扩展键，是全局唯一保留 `additionalProperties:true` 的地方）。是否改变 UID 与需重新生成的产物仍由 `changesUid`／`regenerates` 表达（UI-Q-001）。

**`QuestionAnswerView`**：`{suggestionId,suggestionIds[],suggestionText,suggestionTexts[],customText,text,answeredAt}`。多选答案不再被压成一条字符串：`suggestionIds` 是完整集合，`text` 是实际 resume 给 Agent 的那句话（有自定义文字时是自定义文字，否则是所选建议）。

**单题端点**（保留）：`POST /questions/{questionId}/answer` 收 `{suggestionId?,suggestionIds?,customText?}`（两者可并存，服务端合并去重）；只答一个非阻塞题不会唤醒 run。

**S5／S5M 已按此实现**：桌面端 `QuestionCard.tsx` 一页渲染全部问题、按 `answerMode` 切换单选/多选、显示 `recommended` 标记，整页一次 `POST /questions/answers`；移动端 `QuestionDetailView.vue` 同样按 `answerMode` 组 `{suggestionIds,customText}` 并以 `payloadDigest` 作为行为意图（UI-BR-01）。

### S5IF-FLD09　产物下载（含导出与元数据包）　【新增】　P1
UI-DEP-002 的"下载元数据包"与 UI-PRJ-004 的"删除前先下载导出"需要带文件名的下载。现有 `GET /artifacts/{id}/content` 返回裸字节并带 `X-Data-Classification`（`rest/app.py:656-670`），但无 `Content-Disposition`、无文件名。

**【裁决 2026-09-29】调整后采纳**：**不新增 `/download` 端点**（同一资源不应有两个出口），改为在现有 `content` 端点上加 **`?download=true`**，返回 `Content-Disposition: attachment; filename="..."`。文件名规则由 S1 定义：

| 产物类型 | 文件名 |
| --- | --- |
| `BUSINESS_INPUT` | 上传时的原文件名（取自 `build_record.filename`） |
| `PROJECT_EXPORT` | `{项目名}-export-{UTC日期}.zip` |
| 其他类型 | `{类型}-{产物ID}.{扩展名}` |

另须：① `content` 端点声明进 OpenAPI（媒体类型、`X-Data-Classification`、`download` 参数、410 情形）；② 内容已清除（`contentPurged=true`）返回 410 `ARTIFACT_PURGED` 而不是 500，**该行为对不带 `download` 参数的请求同样适用**。

### S5IF-FLD10　系统设置的值类型声明　【调整】UI-ADM-004　P0
0.2.0 时 `GET /settings` 返回 `{items:[{key,value}]}`，`value` 类型为 `unknown`。UI-ADM-004 要求逐个定型，S5 才能生成表单（现为 8 个键，多出的 `stage_auto_continue` 是 1.19.0 时已存在的布尔开关）：`idempotency_retention_hours:int`、`event_retention_days:int`、`default_gate_reminder_hours:int`、`max_upload_bytes:int`、`allowed_upload_types:string[]`、`archived_project_retention_days:int`、`platform_notification_channels:{type,ref}[]`（**渠道密钥只能是 Secret 引用**，S5IF-G09）、`stage_auto_continue:string[]`（哪些阶段自动继续）。

**1.19.0 已落地**：① `key` 声明为枚举（键是数据，保留 `snake_case`，见 S5IF-G03 的例外）；② 同值另给一份有类型的 `SystemSettingsView`（`{idempotencyRetentionHours,eventRetentionDays,defaultGateReminderHours,maxUploadBytes,allowedUploadTypes,archivedProjectRetentionDays,platformNotificationChannels,stageAutoContinue}`），表单生成器读这一份；③ `GET /api/v1/upload-limits`（可带 `projectId`）返回 `{maxUploadBytes,allowedUploadTypes}`，供 UI-REQS-001 的浏览器端预检与 S1 校验用同一组数字——不要再口述 100 MB。

### S5IF-FLD11　`POST /stages/{id}/patches` 响应定型　【调整】UI-MAN-003　P1
0.2.0 时返回 `{stageId,patchStatus,draftHash,issues,questions,gateInvalidated}`（`services/steps.py:568-575`），其中 `issues` 是 snake_case 的 `Issue.model_dump()`。要求随 S5IF-G03 统一，并补 `assignedUids:{businessKey:uid}`（UI-MAN-003 要显示"本次 Patch 新分配的 UID"；S2 侧 `PatchOutcome.assigned_uids` 已有此数据，`S2 dto/patch.py:75-89`）。**1.19.0 已定型为 `PatchSubmitView`（含 `assignedUids`，`issues[]` 为 `IssueView`）**。`UID_POOL_EXHAUSTED` 为可重试（`S2 registry/pool.py:117,138`），S1 内部重试期间 S5 只显示等待，因此要求：重试耗尽后返回 409/422 且 `retryable=true`，不要伪装成 500；手动补池见 S5IF-BP18。

### S5IF-FLD12　`allowed-actions` 动作目录扩充　【调整】CP-WF-025/006、UI-WB-006　P0
新增端点对应的动作必须进入 `allowed-actions`（`services/workflow.py:628-726`，动作名 `domain/actions.py:44-72`），并在不可用时给出 `reasonCode`（`domain/actions.py:23-41`）。要求补充：`agent.cancel`、`input.parse`、`input.confirm_column_mapping`、`artifact.download`、`artifact.explain`、`environment.create|edit|disable|test_connection|emergency_lock`、`governance.model.register|enable`、`governance.prompt.activate`、`governance.policy.relax`、`registry_binding.set`、`uid_pool.replenish`。**按本轮裁决再加 `deployment_run.drift_check`**（S5IF-FLD05：实时重检漂移必须是显式动作，不得藏在 GET 里）。同时要求 `targetId` 的语义写入文档（现 stage/gate/run 用 ID、环境动作用 environmentKey、无目标时省略——S5 需据此把不可用原因挂到正确控件上）。

**【1.19.0 复核】**：上面列的动作除下表两条外都已进 `domain/actions.py`（并新增了 `artifact.purge`、`environment.probe_capabilities`、`governance.provider.register|enable`、`governance.regression.record`、`governance.evaluation.run`、`governance.budget.set`、`project.create`）。两条仍在目录之外，S5 必须知道：

| 缺的名字 | 现行做法 | S5 的处置 |
| --- | --- | --- |
| `environment.delete_objects` | 复用 `environment.manage` 授权，`allowed-actions` 无此名（理由见 S5IF-ENV13） | 按"有环境管理权"决定控件是否出现；不可用原因挂在 `ENVIRONMENT_LOCKED`／`ENVIRONMENT_DISABLED` 上即可 |
| `governance.provider.test_connection` | 复用 `governance.manage` 授权，探测本身不改变状态 | 按治理管理权决定；探测结果只影响提示，不影响任何按钮的可用性 |

### S5IF-FLD13　定向删除的声明：CR 的 `deletionCandidates`　【新增】CP-CR-005、FR-TST-002　P1

清理链是四段：**在 Change Request 里点名要作废的对象 → 编译收到这份候选 → 产生删除产物 → `delete-objects` 提交**（第四段见 S5IF-ENV13）。第二段曾整段断掉：`analyze()` 用影响集覆写行上的候选，而 S2 的 `ImpactSet` 没有这个字段，于是候选恒为 `[]`，`delete-objects` 对任何在册项目都拿不出可提交的载荷（1.14.0 修）。

- **请求体**：`POST /api/v1/projects/{projectId}/change-requests` 新增 `deletionCandidates: string[]`（缺省 `[]`），与 `reason`、`affectedBusinessKeys` 并列；`extra="forbid"` 之外无其他新字段。
- **当场校验**（S5 要在表单上就地提示，而不是等编译期静默剔除）：项目必须已有 Blueprint 版本；每个被点名的 `businessKey` 在当前版本 Registry 里处置必须是 `DEPRECATED`。任一不满足 → 422 `VALIDATION_ERROR`，`details.notDeprecated[]` 列出对不上的名字。
- **读回**：`ChangeRequestView.deletionCandidates[]`（创建与列表／详情同形），S5 用它显示"这次要作废哪些对象"。
- **为什么不能靠编译期兜底**：非 `DEPRECATED` 的名字会被 S2 在编译时静默剔除，界面显示"删除执行过"、对象却还在实例上——一句永远兑现不了的声明比拒绝更糟。
- **S5 的交互**：候选只能从 Registry 里 `disposition=DEPRECATED` 的条目中勾选（`GET /projects/{projectId}/registry`，S5IF-BP06），不提供自由输入；勾选后显示对象数与"不可撤销"提示。CR 创建是幂等动作（`Idempotency-Key`）。

### S5IF-FLD14　产物清除的回执与"已清除"语义　【新增】FD-ART-010　P1

`POST /api/v1/artifacts/{artifactId}/purge`：无请求体，响应 `ArtifactPurgeView`＝`{artifactId,contentPurged:true}`——**这是一张回执，不是一个状态页**。元数据、哈希、血缘全部保留，只有字节被清（FD-ART-010）；动作名 `artifact.purge` 已在 `allowed-actions` 里，权限 `artifact.purge`。

S5 必须按以下四条渲染：

1. **清除不可撤销**，确认对话框要写明"内容将被删除，元数据与哈希保留"，并要求显式二次确认；`Idempotency-Key` 必须带（重试得到同一张回执）。
2. **之后读内容一律 410 `ARTIFACT_PURGED`**：`GET /artifacts/{id}/content` 的**内联与 `?download=true` 两种请求都返回 410**（S5IF-FLD09），不是 500、不是 404。
3. **槽位看 `contentPurged` 决定给不给下载链接**：`ArtifactSlot.contentPurged`（Run summary 的 `snapshot`／`execution.*`）为 true 时链接要禁用并标注"内容已清除"，或干脆不渲染——不要给一个点下去必然 410 的按钮。
4. **410 是空态不是错误**：界面应显示"内容已按保留策略清除（元数据仍可查）"，并把 `X-Data-Classification`、哈希、血缘照常展示。

⚠️ 1.19.0 的 `x-requirements` 里 `POST /artifacts/{id}/purge` **只挂了 `CP-*`**，没有 `S5IF-*`（`rest/routes/catalog.py:244-252`）——请补挂 `S5IF-FLD14`，否则 S5 的覆盖检查会把它算成"未覆盖的 S5 端点"。

---

## 9. 上游实现进度（**非需求**，仅记录判定时点与影响）——S5IF-UP

本节按 **2026-09-29 的代码快照**记录（UP12 为 0.4.0 按契约 1.19.0 补记）：某条 S1 端点要转发的提供方能力目前还没写完。S2／S3／S4 正在按各自 SRS 增量实现，**最终会与 SRS 一致**，因此：

- 本节**不构成对 S2／S3／S4 的需求**，也不要求任何子系统为 S5 改变其 SRS 已定的设计；
- 本节只回答 S1 的一个排期问题：**"这条端点现在能不能真正接通"**；
- 其中标注**（待确认）**的条目，涉及"该能力是否会以公共 `api` 模块暴露"的设计选择，需架构组与提供方共同确认，本文不替提供方决定；未标注者为进度性事实，S1 可在提供方实现后直接转发。
- S1 若采用 `FACTORY_PROVIDER_MODE=fakes`（`adapters/registry.py:71-79`），这些端点在假实现下即可先行联调，不必等上游定稿——这是本文建议的推进方式。

| # | 观察到的实现进度 | 证据 | 对 S1 的影响 |
| --- | --- | --- | --- |
| S5IF-UP01 | 0.2.0 时 S4 的 `api.py` 是增量 1 占位（自述"完整 `factory_dhis2.api` 在增量 7 补全后替换本文件"），SRS §7 的环境管理、连接测试、能力探测、发现、VALIDATE、COMMIT、部署后验证、对账、到期凭证尚为规范；其数据模型、枚举与 2.41 Profile 数据已落库 | `S4/src/factory_dhis2/api.py`；`S4/src/factory_dhis2/models/`、`enums.py`、`profiles/data/2.41/` | 第 4 章端点的路由与投影**可以先写**（面向 `ExecutionPort` 接口），真实接通待 S4 增量完成；期间由 `FakeExecution` 供数 |
| S5IF-UP02 | CAPABILITY_PROFILE 与 DISCOVERY_RESULT 的**产物内容格式**按 DX-CAP-008、DX-DSC-009 须以契约包发布，承载模块 `contract/` 尚未创建（该格式是 S2/S3 的解析前提，属 S4 SRS 已认领的义务） | DX-CAP-008、DX-DSC-009；`S4 profiles/data/2.41/schemas.json` 目前仅 3 个对象 Schema | ENV09 的 `includeContent`、ENV11、BP11 的**字段定型**无法完成；S1 可先交付索引层（产物 ID、哈希、计数、时间） |
| S5IF-UP03 | **（待确认）** S2 的草稿内容读取目前是模块内私有：`draft_document`、`active_draft_id`、`active_draft_document` 未进 `api.__all__`；公共途径只有 `get_entries(EntrySelector(source="DRAFT"))` 按节按键取 | `S2/src/factory_blueprint/draft/service.py:372-381,1025-1049` 未进 `api.py:35-45` | BP05 若需"整份草稿"视图，须 S2 确认是否导出；按节按键的视图现在就能做 |
| S5IF-UP04 | S2 无"版本 vs 草稿"的 Diff（`diff_versions` 只比两个已批准版本，`diff/` 包全无 draft 引用）；`compare_registry`、`environment_diff` 存在但未导出 | `S2 diff/service.py:19-41`、`diff/object_diff.py:133-197` | BP12、UI-REV-001/003 的"当前与编译结果的 Diff" |
| S5IF-UP05 | **（待确认）** S2 `get_bindings` 构造 `BindingInfo` 时未传 `observed_last_updated` 与 `discovery_result_id`（这两个字段已声明但经公共 API 恒为 `None`，除非有意收窄）；`get_registry` 的 `environmentBindings` 每次最多返回一个环境 | `S2 readmodel.py:302-311` vs `dto/registry.py:69-71`；`registry/queries.py:72-74` | BP06、BP09；D-02 的观测基准判定需要 `observedHash`＋`discoveryResultId` |
| S5IF-UP06 | **（待确认）** 带 `category` 的 NRR 目前只存在于 S3 的 `NRR` 产物内，S2/S3 都无读取该产物的公共函数；S2 的 `requirements` 节因 `additionalProperties:false` 容纳不了 `category`，且 S3 明确记录了"入 S2 时丢弃 `category`"的投影 | `S3 parsing/base.py:43-58,148-165`；`S2 schemas/1/sections.json` | BP15（UI-REQS-004）——需确认由谁提供带类别视图 |
| S5IF-UP07 | S3 的结构化列映射提案未导出：`TablePlan` 是 frozen dataclass、`mappingReview` 只落在 `Fragment.notes` 与 NRR 产物里 | `S3 parsing/columnmapping.py:218-254,628-649` | INP03（UI-REQS-003） |
| S5IF-UP08 | S3 的 Governance 全部返回无模型的手工 dict；`list_prompts`／`get_prompt` 返回 ORM 行；`eval.run` 与 `similar.search` 恒抛；`usage_rollup` 未知维度抛裸 `ValueError`。（原列于此的 `set_budget`／`budget.set` 恒抛已随 M4 交付消除：S3 侧现为已声明模型的类型化函数，façade 的 `budget.set` 也不再拒绝） | `S3 governance/dispatcher.py`（action 表 `:453-475`）、`prompts/registry.py:220-266`、`api.py:367-371`、`cost/usage.py:171-175` | 整个第 6 章 |
| S5IF-UP09 | S4 的两个自定取值待确认：`DeletionPolicy` = `FORBID`/`SAFE_ONLY`（SRS 只列字段未枚举取值）；`version_constraint` 列可空但 DX-ENV-001 列为必需 | `S4 enums.py:88-98`（自述为【新增】偏离）、`models/environment.py:35` | §4.1 概念表、UI-ADM-001 文案 |
| S5IF-UP10 | **【裁决产生】S4 需新增两个只读查询**：① `search_discovery_objects(discovery_artifact_id, dhis2_type, query, limit, offset)`——只读产物内容、不访问 DHIS2；② "最近一次连接测试结果"的读取（`dx.connection_test` 表已有数据，DX-CON-004 已要求持久化） | 由 S5IF-BP11、S5IF-FLD05 的裁决派生 | BP11 的候选检索、FLD05 的 `connection` 字段。两者都**不新增外部 I/O**，故可安全用于聚合 GET |
| S5IF-UP11 | **【裁决产生】S2 需新增 `query_issues(report_artifact_ids, filters, paging)`**；S4 的导入报告问题按 S2 的 Issue 结构（级别字段名 `level`）写入，S3 把 `severity` 改名为 `level` | 由 S5IF-BP14 的裁决派生；现三家字段分歧见 `S2 dto/issue.py:35-73`、`S3 dto/common.py:105-138`、`S1 ports/dto.py:41-57` | BP14、FLD07、ENV08、INP06 中所有含问题的响应 |
| S5IF-UP12 | **0.4.0 补记**：S2 尚未导出 DIFF_REPORT 的计数读取，因此 `GET /deployment-runs/{runId}/summary` 的 `changeStats` 恒为 `{create:0,update:0,delete:0,externalReferences:0}`（形状先定、数据后到）。`diffReportArtifactId` 已给出，S5 可用 `GET /artifacts/{id}/content` 自查，但那违反 UI-BR-02 的"不从别处推断"且大包不宜 | 契约 `RunSummaryView.changeStats` 的描述即此事实；S2 `bp.build_record` 已有 DIFF_REPORT 产物 | FLD05 的变更统计面板；S5 在四个数全 0 时显示"尚未汇总"，不要显示"0 处变更" |

---

## 10. S5 侧的过渡策略（供 S1 参考，不需要 S1 实施）

1. 契约集中在 `S5/src/api/contract/`，每个模型标注来源（S1 代码 `file:line` 或本文 `S5IF-` 编号）与状态：`available`（S1 已提供）／`pending-s1`（本文条目）／`pending-upstream`（第 9 章条目）。
2. `S5/scripts/sync-contract.mjs` 拉取 `S1/docs/openapi.json`，输出漂移报告：路径级新增／删除（基线 147 条）、`operationId` 变化、`untyped-response` 计数、各 operation 的 `x-requirements` 覆盖情况（依赖 S5IF-G11）。`S5/contract/openapi.json` 与 `S5M/contract/openapi.json` 是它的随包副本，必须与 `S1/docs/openapi.json` 逐字相同（`install.sh` 在生成类型与副本不一致时中止构建）。S1 每交付一条，S5 侧对应资源自动从 `pending` 变 `available`。
3. 联调前 S5 用基于契约的 Mock（含 SSE 模拟）独立开发与测试（UI-NFR-05）。Mock 严格照本文与 S1 现状实现，包括 snake_case 泄漏点，以便 S1 修复大小写时 S5 能立刻发现。
4. 请求 S1 在 `rest/app.py` 新增路由时同步跑 `S1/scripts/export_openapi.py` 提交 `docs/openapi.json`，S5 以该文件为唯一事实来源（CP-API-001）。

---

## 11. C 类条目裁决记录（2026-09-29）

本章 15 项是本文提交时 SRS 未覆盖、S5 也无法自行达成的内容。**架构侧已给出结论：全部采纳，其中 6 项调整实现方式或归属。** 结论已同步写回各条目正文（§3、§5、§8 中标注 **【裁决 2026-09-29】** 的段落即为最终要求，实施以那些段落为准），本章保留裁决全貌与后续动作。

### 11.0 结论一览

| 编号 | 结论 | 关键调整 |
| --- | --- | --- |
| S5IF-FLD03 | 采纳 | Gate 归 S1 所有，只是一个查询；纳入 §8.1；M4 起过滤为"待我审批" |
| S5IF-FLD04 | 采纳＋约束 | `updatedAt` 倒序；`environmentClass` 属 S4，**禁止落 S1 库**，实时查＋最多 60 秒进程内缓存 |
| S5IF-FLD05 | 采纳＋硬约束 | **该 GET 不得调用任何访问 DHIS2 的函数**；漂移与能力新鲜度只返回最近一次结果；重检另设 POST 并入 `allowed-actions` |
| S5IF-FLD09 | **调整**后采纳 | 不加 `/download`，改 `content?download=true`；文件名三条规则；410 对两类请求一致 |
| S5IF-BP11 | 采纳，**归属改 S4** | S4 新增只读 `search_discovery_objects(...)`，不访问 DHIS2；S1 只转发投影；P1 |
| S5IF-BP14 | 采纳，**统一 Issue** | Issue 以 S2 BP-VAL-001 为唯一定义、字段名 `level`；S4/S3 遵循；S2 新增 `query_issues` |
| S5IF-FLD01 | 采纳，服务端计算 | 违反 UI-BR-02 的自主聚合被否；**"输入完成度"由 S1 定义前置清单**；整表一次查询算完 |
| S5IF-FLD02 | 采纳，扩展枚举 | 增 `POLICY_DENIED`/`BUDGET_EXCEEDED`/`EXECUTION_FAILED`；枚举**可扩展**，S5 对未知值走通用渲染 |
| S5IF-G03 | 采纳，**升为全局约定** | 写入 00 文档：字段 camelCase、枚举值 UPPER_SNAKE；系统设置键属数据，可保留但须声明为枚举 |
| S5IF-G04 | **调整**后采纳 | 信封 `{items,limit,total?,offset?,nextCursor?}`；审计／产物／事件类用游标（S0 本身是 keyset）；`total` 代价高时省略 |
| S5IF-G05 | 采纳 | 响应头名以 S1 实现为准统一后再声明（代码 `X-Idempotent-Replay` vs CTD `Idempotent-Replayed`） |
| S5IF-G06 | 采纳 | 统一 202 + `{stageId, stepRunId, accepted}`；**不暴露 `engine`**，部署形态属内部细节 |
| S5IF-G07 | 采纳＋归一规则 | 语义与 S1 码相同的提供方码一律归一；有独立含义且已登记于 S0 目录的允许透出（如 `AI_PROMPT_ACTIVATION_BLOCKED`）；`details` 永不含凭证 |
| S5IF-G08 | 采纳，**更进一步** | 直接删除请求体 `idempotencyKey`，头为唯一来源；commit 缺头返回 400 |
| S5IF-G11 | **调整**后采纳 | 语义化版本＋CHANGELOG 照做；追溯字段改为通用 `x-requirements`，S1 自身 `CP-*` 与测试组可复用 |

以下 11.1～11.3 为提交时的诉求与裁决点，保留备查。

### 11.1 新增端点（6）

| 编号 | 诉求 | 为什么现有规定不覆盖 | 建议方案 | 不采纳时 S5 的退路 |
| --- | --- | --- | --- | --- |
| S5IF-FLD03 | 跨项目待审批 Gate 列表 | S1 §8.1 的 Gate 分组只有 `GET /workflows/{id}/gates`；总体 18.1 只说 Dashboard 要显示"待审批 Gate"，未规定如何跨项目取 | `GET /gates?status=WAITING&projectId=` | 遍历全部项目逐个取 workflow 再取 gates：项目数 × 2 次请求，无法分页，Dashboard 首屏（UI-NFR-01 < 3 秒）不达标 |
| S5IF-FLD04 | 跨项目 Run／执行列表 | 同上，Run 只按 workflow 列 | `GET /deployment-runs?status=&environmentKey=&outdated=` | 同 FLD03 的退路 |
| S5IF-FLD05 | 部署控制台聚合视图 | 总体 18.3 规定了控制台**要显示什么**，未规定接口如何给；S1 §8.1 也无此端点 | `GET /deployment-runs/{id}/summary`（S1 组合自身数据 + S4 实时查询，不落库） | S5 发 6～8 次请求拼装，且其中漂移、Snapshot、环境类别**目前无 REST 入口**，拼装也不完整 |
| S5IF-FLD09 | 产物下载（带文件名） | §8.1 有 `GET /artifacts/{id}/content`，未规定下载语义与文件名；UI-DEP-002"下载元数据包"、UI-PRJ-004"删除前先导出"需要可保存的文件 | 在 `content` 上加 `Content-Disposition` 与文件名，或增设 `/download` | 用 `<a download>` 只能拿到 artifactId 作文件名；用户无法辨认，导出体验不可用 |
| S5IF-BP11 | 环境绑定候选对象的分页检索 | DX-DSC-* 规定发现结果是**产物**，`discover()` 只返回产物 ID；任何 SRS 都没有"发现对象列表"的分页接口 | S1 提供按类型／code／name 的分页检索（读该环境 DISCOVERY_RESULT 后投影） | S5 取整个产物自行筛选：5,000 对象规模（NFR-CAP-001、UI-REV-006）下不可接受；且依赖 UP02 的格式定义 |
| S5IF-BP14 | 跨产物校验问题列表 | UI-REV-005 要求按级别／来源阶段／规则／对象筛选，SRS 未规定问题清单的接口，现仅存在于 `VALIDATION_REPORT` 产物内 | S1 汇聚并服务端筛选分页 | S5 读产物自行解析与筛选：同样受 UP02/UP06 格式未定阻塞，且大列表无法分页 |

### 11.2 字段与定型（2）

| 编号 | 诉求 | 裁决点 |
| --- | --- | --- |
| S5IF-FLD01 | 阶段视图的六项聚合（输入完成度、未决 Question 数、HIGH 未决数、BLOCKING 数、最近执行时间、当前产物版本） | FR-UI-001 规定**界面**要显示这七项，但 `GET /workflows/{id}/stages` 未定义这些字段。裁决点：由 S1 在服务端算（本文建议，见 §FLD01 的成本分析），还是允许 S5 自行聚合（则与 UI-BR-02"不从其他数据推断业务状态"冲突，需明确豁免） |
| S5IF-FLD02 | `feedback`／`inputBlueprintRef` 的形状 | 二者已是 S1 自有投影但为自由对象。裁决点：定型为本文建议的 `GATE_REJECTED\|PATCH_REJECTED\|VALIDATION` 三形态，还是留给 S1 自定（则 S5 需按 errorCode 兜底渲染） |

### 11.3 契约治理（7）

| 编号 | 裁决点 | 若不采纳 |
| --- | --- | --- |
| S5IF-G03 | 是否统一 camelCase（S1 自身 `_Mirror` 已表达该意图，但 SRS 未规定 wire 命名） | S5 按端点逐个录制大小写，代码生成器不可用，契约层永久手工维护 |
| S5IF-G04 | 分页信封是否统一（CP-API-005 只要求"支持分页与排序"） | S5 为三种形状各写一套列表组件适配 |
| S5IF-G05 | 跨切请求头是否写进 OpenAPI | S5 手抄，且无法用生成器校验遗漏 |
| S5IF-G06 | `run`/`rerun` 是否只保留受理信封 | S5 需按部署形态分支处理，违反 UI-APP-006 |
| S5IF-G07 | 错误 `details` 是否按码定型 | UI-REQ-002 只能显示 message 与 correlationId，无法给出"缺哪个权限""时间窗是什么" |
| S5IF-G08 | 幂等键请求头与请求体并存时的优先级 | COMMIT 重试语义不确定，直接影响 AC-EXE-05 |
| S5IF-G11 | 是否接受 `x-s5if` 扩展字段与主版本变更约定 | S5 的漂移检查降级为人工比对（UI-IF-002 的"提前通知"无自动化支撑） |

> G01、G02、G09、G10 **不在本表**：它们分别可由 CP-API-001（"以 OpenAPI 3.1 描述…供 S5 使用"）、FR-SEC-007、CP-WF-025/026 直接推出，属 B 类既有义务而非新增。

### 11.4 裁决后需同步修订的文档

采纳上述结论后，以下文档需相应修订（本文不代写，仅登记待办）：

| 文档 | 修订内容 |
| --- | --- |
| **00 文档** | §6.x 新增"公共 REST 线上约定"：字段一律 camelCase、枚举值一律 UPPER_SNAKE、分页信封 `{items,limit,total?,offset?,nextCursor?}`、跨切请求头清单、错误码归一规则、`x-requirements` 追溯字段 |
| **S1 SRS** | §8.1 增补端点：跨项目 Gate 列表、跨项目 Run 列表、部署控制台聚合视图、漂移重检动作、校验问题列表、环境绑定候选对象（转发 S4）；`content` 端点增加 `download` 参数与文件名规则；阶段视图增补聚合字段并**给出"输入完成度"的定义**（前置清单）；§8.3 增加按码定义的 `details`；commit 端点以 `Idempotency-Key` 请求头为唯一幂等来源（删除请求体字段） |
| **S2 SRS** | 声明 Issue 为统一结构（级别字段名 `level`）；新增 `query_issues(report_artifact_ids, filters, paging)` |
| **S4 SRS** | 新增两个只读查询：`search_discovery_objects(discovery_artifact_id, dhis2_type, query, limit, offset)`、"最近一次连接测试结果"；导入报告问题按 S2 的 Issue 结构标准化（DX-EXE-027） |
| **S3 SRS** | 问题级别字段 `severity` 改为 `level`，与 S2 的 Issue 结构对齐 |

### 11.5 三个"待确认"项的回答（S5 立场）

这三项在 §9 标为待确认，问的是"该能力是否会以公共 `api` 暴露"。S5 的回答如下，**决定权仍在提供方**：

**UP03｜S2 是否需要导出整份草稿内容读取？——不需要，建议关闭此项。**
S5 对草稿的实际诉求只有两处：工作台顶部显示"草稿及其哈希"（`get_draft` 的 `BlueprintDraftInfo` 已够，S1 端口 `:92` 已声明），以及主工作区按节显示与编辑条目（走 S5IF-BP04 的 `get_entries(source="DRAFT")`）。整份文档一次性拉取对 UI 无收益，反而在 5,000 对象规模下浪费带宽。**因此不必为 S5 导出 `draft_document`／`active_draft_id`**；S2 若出于自身需要导出，S5 也不会使用。唯一残留需求：S1 需能**由 projectId 找到当前活动的 draftId**（S5 不持有业务状态，不能猜），请在 S1 侧解决。

**UP05｜S2 绑定查询缺观测基准字段——请 S2 补齐，理由充分而非为 S5 特批。**
`BindingInfo` 的 `observed_last_updated`、`observed_hash`、`discovery_result_id` **已在 DTO 中声明**（`S2 dto/registry.py:69-71`），但构造时未赋值（`S2 readmodel.py:302-311`），即"声明与实现不一致"，补两行即可。若 S2 认为有意收窄，S1 的退路是用 `registry_object_refs`＋逐环境 `get_registry` 拼装（代价：N 个环境 N 次调用）。影响面：`observed_hash`＋`discovery_result_id` 是 D-02 环境绑定基准判定的必需项（UI-DEP-003 要显示"依据哪次发现、当时哈希是什么"）；`observed_last_updated` 纯展示，**可降级为不显示**。

**UP06｜带类别的 NRR 由谁提供？——应由 S3 提供读取，S1 转发；不建议动 S2 的 schema。**
`category` 的 13 个取值是 **S3 解析器的产物字段**（`S3 parsing/base.py:43-58`），且 S3 明确记录了"写入 S2 时丢弃 `category`"的投影（`:148-165`）；S2 的 `requirements` 节是 `additionalProperties: false` 的封闭 Schema（BP-SCH-001），**为它加 `category` 等于推翻 S2 已定的 Schema 契约**，代价与争议都远大于收益。因此建议：① 由 **S3 提供 NRR 产物的读取／查询函数**（解析记录本就归 `ai` schema 所有，符合 D-14），S1 转发并投影；② S5 的 REQUIREMENTS 视图分两块显示——S3 的 NRR（含 `category`、来源位置、置信度）与 S2 草稿中已批准的 requirements 条目（无类别）；③ **过渡**：M1 先用 BP04 的条目视图（不按类别分组），类别分组随 S3 读取能力到位后开启。若架构组另有判断，S5 接受"按类别分组"降级为按 `businessKey` 前缀浏览。

---

## 12. 验收标准（S5IF-AC）

| # | 给定／当 | 则 |
| --- | --- | --- |
| S5IF-AC01 | 用 `openapi-typescript` 或 `openapi-zod-client` 处理 `docs/openapi.json`（147 条路径 / 172 个操作） | 生成的类型中，除 `SuggestionView`（列映射建议的扩展位）外不含任何 `additionalProperties:true` 的响应体；每个端点都有可引用的组件名（S5IF-G01） |
| S5IF-AC02 | 遍历全部操作 | 无 snake_case 字段泄漏；`_Mirror` 模型经 `by_alias=True` 导出（S5IF-G03） |
| S5IF-AC03 | `GET /api/v1/environments`（S1 以 `FACTORY_FAKE_ENVIRONMENTS` 启动） | 返回 §4.1 全部字段，含 `credentials[].expiresAt`，不含任何凭证明文或其掩码（S5IF-ENV01、S5IF-G09） |
| S5IF-AC04 | 对 PRODUCTION 环境用同一 Secret 引用配置 `discovery` 与 `import` | 422，错误码属 S1 目录且 `details` 指明冲突范围（DX-ENV-005、S5IF-G07） |
| S5IF-AC05 | 连接测试账户缺少元数据导入权限 | `checks[]` 八项齐备、`missingAuthorities.import` 非空、`ok=false`（S5IF-ENV08、DX-AC-01） |
| S5IF-AC06 | 紧急锁定打开时请求 COMMIT 的可用性 | `allowed-actions` 中 `execution.commit` 为不可用且 `reasonCode=ENVIRONMENT_LOCKED`，`targetId` 为环境键（S5IF-FLD12、DX-AC-05） |
| S5IF-AC07 | `GET .../blueprint/section-schemas/mapping` | 返回自包含 Draft 2020-12 文档，`$ref` 全部指向本文档内 `definitions`，可被 S5 的 Monaco 校验器直接使用（S5IF-BP02） |
| S5IF-AC08 | 未运行的阶段发起人工 Patch 并提交审批 | 阶段进入 `WAITING_APPROVAL` 并创建 Gate（现有能力，回归验证 CP-WF-029） |
| S5IF-AC09 | `GET /workflows/{id}/stages` | 每个阶段带 `inputCompleteness`、`openQuestionCount`、`highOpenQuestionCount`、`blockingIssueCount`、`lastRunAt`、`currentArtifactVersion`（S5IF-FLD01） |
| S5IF-AC10 | Dashboard 打开 | 单次 `GET /gates?status=WAITING` 与单次 `GET /deployment-runs` 即得全部待审批 Gate 与最近执行，无需遍历项目（S5IF-FLD03/004） |
| S5IF-AC11 | `GET /deployment-runs/{id}/summary` | 返回 S5IF-FLD05 全部键且满足 CP-NFR-03（P95 < 500 ms）；**该请求期间对 DHIS2 的调用次数为 0**（`check_drift`、`check_capability_freshness`、`test_connection` 均不得被调用，只返回最近一次结果）；重检漂移须经 `POST .../drift-check` 显式触发 |
| S5IF-AC12 | `POST /projects/{id}/inputs/{artifactId}/parse` 以同一 `Idempotency-Key` 连发两次 | 返回同一首次结果，不产生重复 Question（CP-Q-004） |
| S5IF-AC13 | 未回答 Question 的 `GET /questions` 与 `GET /workflows/{id}/questions` | `suggestions[]` 每项含 `id,text,rationale,changesUid,regenerates,recommended`；`QuestionView.answerMode` ∈ `SINGLE`/`MULTIPLE`（缺省 `SINGLE`）（S5IF-FLD08） |
| S5IF-AC14 | 任一 Gate 批准时提交过期的 `seenBoundHashes` | 409 `APPROVAL_INVALIDATED`，`details={gateId,mismatches[]}`（现有能力，S5 据此实现 UI-AC-05） |
| S5IF-AC15 | 逐条对照第 13 章追溯表 | 每个 `pending-s1` 项在 S1 交付后，其 `x-requirements` 数组中出现对应的 `CP-*` 与 `S5IF-*` 编号（S5IF-G11） |
| S5IF-AC16 | `GET /artifacts/{id}/content?download=true` | 返回 `Content-Disposition: attachment`，文件名按三条规则生成；不带该参数时为内联；内容已清除时**两种请求都**返回 410 `ARTIFACT_PURGED`（S5IF-FLD09） |
| S5IF-AC17 | commit 请求不带 `Idempotency-Key` 请求头，或请求体中仍出现 `idempotencyKey` | 前者返回 400；后者该字段被拒绝或忽略并有文档说明——请求体中不再有幂等键这一唯一来源（S5IF-G08） |
| S5IF-AC18 | 检查任意响应的全部字段名与枚举值 | 字段名一律 camelCase、枚举值一律 UPPER_SNAKE；系统设置的键允许 snake_case 但在 OpenAPI 中声明为枚举（S5IF-G03、G10） |
| S5IF-AC19 | `GET /audit?limit=…` 连取三页 | 使用 `nextCursor` 翻页而非 offset，且不返回 `total`（S5IF-G04） |
| S5IF-AC20 | `POST /stages/{id}/run` | 返回 202 与 `{stageId,stepRunId,accepted}`，**响应中不含 `engine` 字段**；进度与结果经 SSE 与 `GET /stages/{id}` 观察（S5IF-G06） |
| S5IF-AC21 | 一次 `POST /questions/answers` 提交同一阶段的 3 道题（含一道 `MULTIPLE` 选了两个建议） | 同一事务写入 3 条答案、**resume 至多一次**；`answers[]` 三条 `ANSWERED`，`suggestionIds` 保有两个 id；`questions[]` 返回 resume 后仍开放的问题；同 `Idempotency-Key` 重放不写第二条答案（S5IF-FLD08） |
| S5IF-AC22 | 把两阶段或重复 `questionId` 的答案放进同一次问卷提交 | 422，`details` 指出 `stageIds[]` 或重复的题号；数据库无任何部分写入（S5IF-FLD08） |
| S5IF-AC23 | 对未登记的提供方名 `POST /governance/providers/nope/test-connection` | **200**，`{ref:"nope",healthy:false,detail:"<非空原因>"}`；响应中不出现 `apiKeySecretRef` 或其值（S5IF-GOV20） |
| S5IF-AC24 | `PATCH /governance/models/{ref}` 只给 `unitPriceInputPer1k` | 200 返回整行 `ModelView`，`endpoint`／`apiKeySecretRef`／`capabilities` 保持原值；空对象体 → 422；含未声明字段 → 422；审计有 `before`／`after`（S5IF-GOV06） |
| S5IF-AC25 | 首个设计阶段未跑时 `GET /projects/{id}/blueprint/draft` | 404 `NOT_FOUND`，`details.reason=NOT_GENERATED`、`details.requiredStage=REQUIREMENTS`；**不是** 200 空体，也**不是** `NOT_EXIST`（S5IF-BP05、CP-UI-022） |
| S5IF-AC26 | 对已有执行报告的 Run 取 `GET /deployment-runs/{id}/summary` | 200；`execution.reportArtifactId` 与 `importReportArtifactId` 是 `{artifactId,status,contentPurged}` 对象（不是字符串）；`snapshot` 同形；`stages[]` 不含 `outputArtifactIds`（S5IF-FLD05） |
| S5IF-AC27 | `POST /environments/{key}/delete-objects` 不带 `Idempotency-Key` | 400 `IDEMPOTENCY_KEY_REQUIRED`（`details.header`）；请求体含 `idempotencyKey` → 422；带齐头与三个字段时返回 S4 的 `ObjectDeletionView`，拒绝也是 `FAILED`+`applied:false` 而不是 5xx（S5IF-ENV13、S5IF-G08） |
| S5IF-AC28 | 同一个 `businessKey` 既不是 `DEPRECATED` 又被写进 `deletionCandidates` | 创建 CR 时 422 `VALIDATION_ERROR`，`details.notDeprecated[]` 含该名字（S5IF-FLD13） |
| S5IF-AC29 | 清除产物内容后再取 `content`（内联与 `?download=true` 各一次） | 两次都是 410 `ARTIFACT_PURGED`；`POST /artifacts/{id}/purge` 返回 `{artifactId,contentPurged:true}` 回执；summary 的对应槽位 `contentPurged=true`（S5IF-FLD14） |

---

## 13. 追溯表（S5 需求 ↔ 本文条目）

| S5 需求 | 内容摘要 | 本文条目 | 阻塞级别 |
| --- | --- | --- | --- |
| UI-APP-007 | 客户端与 Zod 由 S1 OpenAPI 生成 | S5IF-G01、G02、G05、G11 | P0 |
| UI-REQ-002 | `errorCode` 本地化映射 | S5IF-G07 | P0 |
| UI-REQ-001/004 | 幂等键复用；COMMIT 不自动重提 | S5IF-G08 | P1 |
| UI-WB-002 / FR-UI-001 | 阶段导航七项 | S5IF-FLD01、FLD02 | P0 |
| UI-WB-003 | 上下文显示草稿及哈希 | S5IF-BP05 | P0 |
| UI-WB-006 / UI-BR-01 | 按钮可用性唯一来源 | S5IF-G10、FLD12 | P0 |
| UI-WB-009 | Agent 运行状态、进度、取消；策略拒绝与预算信息 | S5IF-INP06、INP07 | P0 |
| UI-REQS-001 | 上传预检与 S1 配置一致 | S5IF-FLD10 | P1 |
| UI-REQS-002 | 解析状态与错误；这次解析模型到底问了什么 | S5IF-INP01、INP02、INP08、INP09 | P1 |
| UI-REQS-003 | 列映射复核 | S5IF-INP03、INP04（依赖 UP07） | P1 |
| UI-REQS-004 | NRR 按类别展示与来源跳转 | S5IF-BP04、BP15（依赖 UP06） | P1 |
| UI-Q-001 | Question 结构化展示；整页问卷一次作答 | S5IF-FLD08 | P0 |
| UI-Q-002 | HIGH 未答阻止批准 | S5IF-FLD01、FLD03 | P0 |
| UI-MAN-002 | Schema 驱动表单与 Monaco 校验 | S5IF-BP01、BP02 | P0 |
| UI-MAN-003 | Patch 逐条问题与新分配 UID | S5IF-FLD11、BP08 | P1 |
| UI-GATE-002/005 | WARNING 逐条处理；DEPLOY_GATE 确认项 | S5IF-FLD07、ENV06 | P0 |
| UI-GATE-003 | 批准回带绑定对象哈希 | S5IF-FLD07 | P0 |
| UI-REV-001 | 产物预览／原文／Diff／追溯 | S5IF-BP03、BP12、FLD09 | P1 |
| UI-REV-002 | 对象依赖图（一次构建的产物） | S5IF-BP04（依赖 UP03/UP04） | P1 |
| UI-REV-003 | 元数据三视图与统计 | S5IF-BP06、BP07、BP14、ENV11 | P1 |
| UI-REV-005 | 校验问题筛选与解释 | S5IF-BP14、INP05 | P1 |
| UI-REV-006 | 5,000 对象规模可用性 | S5IF-G04、BP11（依赖 UP02） | P1 |
| UI-DEP-001 | 按环境的 Run 列表与 OUTDATED | S5IF-FLD04、FLD06 | P1 |
| UI-DEP-002 | 部署控制台全部信息 | S5IF-FLD05、ENV06、ENV08、ENV09 | P0 |
| UI-DEP-003 | 环境绑定人工设置 | S5IF-BP09、BP10、BP11 | P1 |
| UI-DEP-004/005 | COMMIT 二次确认；执行报告与 Snapshot Diff | S5IF-FLD05、G08、FLD09、FLD14 | P1 |
| UI-PRJ-001 | Dashboard 四块内容 | S5IF-FLD03、FLD04 | P0 |
| UI-PRJ-002 | 向导选目标环境与项目 `codePrefix` | S5IF-ENV01、ENV02、BP19 | P0 |
| UI-PRJ-004 | 项目设置（环境、通知、导出导入、作废清理） | S5IF-ENV01、FLD09、FLD10、FLD13、FLD14 | P1 |
| UI-ADM-001 | 环境管理全项 | S5IF-ENV01～ENV13 | P0 |
| UI-ADM-002 | 只接受 Secret 引用 | S5IF-G09、ENV03、GOV02 | P0 |
| UI-ADM-003 + UI-SET-006 | 模型／Prompt／策略／评测／用量；提供方测连接与内置目录 | S5IF-GOV01～GOV20（GOV07 兼目录） | P1 |
| UI-ADM-004 | 系统设置 | S5IF-FLD10 | P0 |
| UI-MAN-003 | Patch 后 UID 池耗尽时人工补池 | S5IF-BP18、FLD11 | P1 |
| UI-OPS-002 | 审计与 COMMIT 追溯 | 已满足（S5IF-G03 转为回归约束，大小写泄漏已修） | P1 |

---

## 14. 建议的实施顺序

排序原则：**先做不需要任何人决策的（A/B 类），并行的把要裁决的（C 类）提出来**，避免 S1 为等待裁决而停工，也避免 S1 先做出与裁决冲突的实现。

| 批次 | 内容 | 前置 | 状态与理由 |
| --- | --- | --- | --- |
| 第 0 批（**已完成 2026-09-29**） | 第 11 章 15 项 C 类条目的裁决 | 架构组 | 结论：全部采纳，6 项调整实现方式或归属；已写回各条目正文 |
| 第 1 批（成本最低） | **A 类 5 条**（BP02、BP10、ENV07、ENV12、INP05）＋ 20 条"只差路由"的 B 类：ENV01～ENV06、ENV08～ENV11、BP08、BP12、BP13、INP01、INP07 | 无（端口方法或服务函数已存在） | **已交付**（契约 1.0.0 起）。解锁 UI-MAN-002、UI-ADM-001/002、UI-WB-009、UI-DEP-003、UI-REQS-002 的主干；其中 `parse_input` 与 `cancel_agent_run` 属于"代码已写完只差注册" |
| 第 2 批 | B 类契约：S5IF-G01、G02（响应与请求类型化），随后 G09、G10 | 无 | **已交付**（1.0.0～1.3.0）。决定 S5 能否从"手抄契约"切回"生成契约"（UI-APP-007）；类型化越早，S5 越省工 |
| 第 3 批 | B 类其余：GOV01～GOV19（门面契约化）、BP01、BP03～BP07、BP09、BP15 的端口方法与路由；字段项 INP02、FLD06～FLD08、FLD10～FLD12 | 部分依赖第 9 章上游进度（UP01、UP03、UP05、UP06）；可用 `FACTORY_PROVIDER_MODE=fakes` 先行 | **已交付**。MVP（M2）视图所需 |
| 第 4 批 | 裁决通过的 C 类端点：FLD01～FLD05、FLD09、BP11、BP14 | 第 0 批裁决结论 | **已交付**（1.0.0～1.3.0）。需新建查询与聚合，工作量独立可并行估算 |
| 第 5 批（1.19.0 增量，**已交付**） | GOV11 读、GOV20（1.16.0）、GOV06 差量 PATCH（1.15.0）、BP18 补池（1.9.0）、BP19 `codePrefix`（1.10.0）、ENV13 定向删除（1.9.0）、FLD13 删除候选（1.14.0）、FLD14 清除回执（1.13.0）、INP08／INP09（日志）、FLD08 整页问卷（1.19.0）、FLD05 槽位与 `contentPurged`（1.12.0／1.13.0） | 无（均在契约内） | 本轮只做文档校准与验收；S5 侧的 `pending-s1` 清单可据此一次性清空，转做回归 |

**第 0～4 批均已交付**：S5 的 M1／M2 可以对接真实 S1。第 5 批是 0.4.0 反向校准补记的条目，代码已在契约中，剩余工作是 S1 补三处 `x-requirements` 标注（见 §15）与 S5 的回归。

---

## 12. 装机引导（S5IF-SET）

> 本章是 2026-09-29 裁决**之后**追加的一批，不计入 §0.4 的 A/B/C 统计。它满足的是新增的
> `UI-SET-001…005` / `UM-SET-001…005`（见 S5 SRS §3.17、S5M SRS §5.15），不是对既有差距的重述。

S5／S5M 的引导界面（`UI-SET-001`、`UM-SET-001`，见各自 SRS）要回答一件事：**这台机器配到哪一步了**。既有接口里没有任何一个能回答它：

| 界面需要知道 | 为什么现有端点给不出 |
| --- | --- |
| 三方提供方是真接上了还是跑在替身上 | 只在 `/healthz` 里，而 `/healthz` 不带 `/api/v1` 前缀、不在 nginx 的代理范围内（UI-APP-008 只同域代理 `/api/v1`） |
| 配置本身有没有写错（`FACTORY_HTTPS_ONLY` 与 `FACTORY_AUTH_MODE` 矛盾、OIDC 参数不齐、引导账号两个变量只写了一个） | `config.py` 的 `validate_runtime()` 早已产出这份人读清单，但只在启动时 fail-fast 用一次，之后再无人能读到 |
| 是否已登记环境／提供方／模型、是否已有项目 | 要 S5 自己发四个请求再数行 —— 这正是 UI-BR-02 禁止的"从别处推断业务状态" |
| 环境登记了但**从没做过连接测试**（校验阶段会拒绝它） | `GET /environments` 不含此事实，逐项 `GET .../test-connection` 是写操作，不能当读用 |

| # | 方法与路径 | 优先级 | 落地动作 | 来源 | 请求 | 响应 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S5IF-SET01 | `GET /api/v1/setup` | P0 | 新增（只读聚合，不引入新状态） | `UI-SET-001`～`004`、`UM-SET-001`～`004`、FR-UI-009 | — | `SetupView`（§12.1） | 读权与平台作用域的 `GET /allowed-actions` 一致＝`project.view`；无写入、无副作用、不触 DHIS2 |

### 12.1 `SetupView` 的字段与出处

| 字段 | 含义 | 数据出处 |
| --- | --- | --- |
| `providers`（`{S2,S3,S4: real\|fake}`） | 每个端口背后是真实现还是替身 | `adapters/registry.py` 的 `provider_modes()`，与 `/healthz` 同源 |
| `configuration.problems[]` | 配置问题清单，句子只写变量名、绝不写值 | `Settings.validate_runtime()` |
| `configuration.{providerMode,workflowEngine,migrationsMode,httpsOnly,authMode}` | 当前生效的运行形态 | `Settings` |
| `startup` | 迁移与登记事实（`appliedSchemas`、`skippedSchemas`、`providersMissing`、`registered`） | `bootstrap.StartupReport`，与 `/healthz` 同源 |
| `identity.{authMode,enforced,m4Enabled,users,pendingPasswordChange,bootstrapAdminDeclared}` | 身份体制与账号状态；`pendingPasswordChange` 是**当前调用者**的 | `cp.users` 计数 + 该用户行 |
| `environments.{total,inService,nonProductionInService,neverTested[],items[]}` | 环境是否**可用**而非是否存在；`neverTested` 就是校验阶段会拒绝的那批 | `execution.list_environments()` + `get_last_connection_test`（S5IF-FLD05 同一读法，S4 自有行，DX-CON-004） |
| `intelligence.{providers,providersEnabled,models,modelsEnabled}` | 模型侧登记情况；无 `governance.manage` 的调用者得到 `null`，**不是 0** | S5IF-GOV01、GOV04 |
| `projects.{total,active}` | 是否已经有项目可走 | `cp.projects` |
| `credentials.{storeAvailable,masterKeyConfigured,scheme,named,unresolvable[]}`（契约 1.7.0，两字段语义在 1.7.1 分开） | `masterKeyConfigured` = env 里那一行存在且非空白；`storeAvailable` = 它长到能用（≥32 字符），也就是 `db://` 真能读写；`named` = 库里有多少凭证；`unresolvable[]` = **哪些仍在被引用的凭证已经解不开**（`reason` = `missing`/`keyMismatch`/`corrupt`，附引用者清单）。把两者分开是因为一把 10 个字符的密钥会同时"存在"和"不能用"，报成"未配置"就把人指到 env 文件里错的那一行——设置是在的，只是太短，这句才是 `validate_runtime()` 说的话 | `cp.secrets` + 环境/提供方/模型行的引用扫描；无 `secret.manage` 的调用者得到 `null`，**不是 0** |

### 12.2 三条边界

1. **只回事实，不回判断**：哪一个按钮能点，仍然**只**来自 `allowed-actions`（S5IF-G10、S5IF-FLD12、UI-BR-01）。本节不得出现"下一步该做什么"的字段——那是呈现，属 S5（总体 SRS P-06）。
2. **权限不足的小节回 `null`，不回 0**：0 会被界面读成"什么都没登记"，而真实原因可能是"你不能看"。
3. **不新增 S1 自持状态**（CP-API-007、D-08）：所有字段都是对既有端口方法与 `cp` 表的读，不写审计、不建表。

### 12.3 随本节必须补齐的动作项（S5IF-G10 的既有要求，此前漏做）

`project.create` 是 `POST /api/v1/projects` 的守卫动作名，却从未出现在 `allowed-actions` 的任何作用域里，导致界面无法门控"新建项目"，只能让用户填完向导再吃 403。按 S5IF-G10"新增端点对应的动作必须一并进入 `allowed-actions`"补齐：`project.create` 进入**平台作用域**清单，无权限者收到 `reasonCode: PERMISSION_DENIED`。

---

## 12A. 凭证录入与提供方目录（S5IF-SEC、S5IF-GOV07）

> 2026-10-04 追加（契约 1.7.0），满足 `UI-SET-006`、`UI-ADM-002` 的调整与 `CP-SEC-001…004`。
> 它解决的是引导路径上最后一件必须离开浏览器的事：配一台环境或一个模型提供方，原先要先 SSH 进服务器
> 把令牌写进 `.deploy/env`，再回界面敲 `env://变量名`。
>
> **1.19.0 复核（无变化）**：① `PUT /secrets/{name}` 的 `x-requirements` 不含 `CP-API-003`、`XIdempotencyKey` 也不挂在该操作上——这条路径整段排除在回放表外（`rest/middleware.py:62` 的 `NON_IDEMPOTENT_PATH_FRAGMENTS`），可重复性来自 `PUT` 本身；② `SecretView` 只有 `{name,ref,algo,createdAt,createdBy,rotatedAt,updatedAt}`，**没有**任何密钥派生值（`key_fingerprint` 只留库里）；③ `apiKeySecretRef` 在 `POST /governance/providers` 的请求模型里可省（省略＝"这个地址不发 `Authorization`"）；④ `GET /governance/provider-catalogue` 的条目仍是 9 个字段、无 `status`／`reachable`。⑤ **编号提示**：`S5IF-GOV07` 同时挂在 `GET /governance/prompts` 与 `GET /governance/provider-catalogue` 两条读上（契约如此）；本文沿用同一编号，读的时候按路径区分，不要当成一条端点。

| # | 方法与路径 | 优先级 | 落地动作 | 来源 | 请求 | 响应 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S5IF-SEC01 | `PUT /api/v1/secrets/{name}` | P0 | 新增（写一次即加密落库） | `UI-ADM-002`、`UM-ADM-004`、CP-SEC-001 | `{material}` | `SecretView` | 创建与轮换同一句话，故用 `PUT`：一次丢了响应的重试无害。`name` 限 `^[a-z0-9][a-z0-9._-]{1,63}$`（名字会被列出，禁止放用户名）。主密钥缺失 → 503 `SECRET_UNAVAILABLE` + `details.setting`。**本端点不收 `Idempotency-Key`**（契约 1.7.1）：这条路径整段排除在回放表之外，头只会被接受、记录、然后忽略；可重复性来自 `PUT` 这个动作 |
| S5IF-SEC02 | `GET /api/v1/secrets` | P0 | 新增 | 同上 | — | `{items[SecretView], total, generatedAt}` | **只有名字与时间**；**没有** `GET /secrets/{name}`——那是一个 403/404 枚举探针，而本端点在同一权限下给出同样多的信息 |
| S5IF-SEC03 | `DELETE /api/v1/secrets/{name}` | P0 | 新增 | CP-SEC-003 | — | `{name, deleted}` | 仍被环境/提供方/模型引用时 409 `SECRET_IN_USE` + `details.referencedBy[]`；不存在 404 |
| S5IF-GOV07 | `GET /api/v1/governance/provider-catalogue` | P1 | 新增（只读静态目录） | `UI-SET-006`、AI-CAT-001 | — | `{items[], total}` | 每条 `{key, displayName, endpoint, wireProtocol, models[], keyRefSuggestion, docsUrl, local, note}`；读权同 `governance.manage` |

### 12A.1 目录为什么只列 OpenAI 兼容厂商

运行时只有一个线协议：`S3/.../llm/openai_compat.py` 的 `OpenAICompatProvider` —— 注册地址原样作为
base URL（只 `rstrip('/')`）、追加 `/chat/completions`、`Authorization: Bearer <解析后的密钥>`，健康检查
是 `GET /models`。`provider_for()` **不看** `adapter` 列，`RegisteredModel` 里也没有 adapter 字段。
因此：

- `https://api.anthropic.com/v1` 这类地址**不得**进目录——它没有 `/chat/completions`、要 `x-api-key`，
  存进去登记成功、第一次调用必失败；
- `adapter` 是装饰性字段，界面不得据它提供选择（S3 SRS 的 AI-LLM-005/AI-GOV-002 同步加注）；
- 目录条目**不代表已验证连通**，也不得出现 `status`/`reachable` 一类字段让界面画成绿灯；
  是否真通，只有 S5IF-ENV05 的连接测试与阶段实跑能回答。
- 地址必须写成这个客户端会用的完整形态（含 `/v1`；智谱是 `/api/paas/v4`），因为运行时不会补版本号。

### 12A.2 四条边界

1. **只有这一个端点接受值**。环境类与治理类端点继续只接受引用（S5IF-G09）；`PUT /secrets/{name}`
   是唯一让凭证值跨过 HTTP 边界的地方，进去即加密，响应只有引用。
2. **值不出现在任何读路径**：响应、审计 `before/after`、SSE、422 摘要、幂等回放表全部排除
   （CP-SEC-004）。`material` 字段因此**不加** pydantic 约束——约束失败会把原值写进错误摘要。
   422 那一层要读的是每个错误的 `input` 键（pydantic v2 的位置），不是 `ctx.input`，并且要连同
   dict / list 里的每个值一起抹掉：把凭证以 `{"token": "sk-…"}` 这种错类型提交时，摘要里剩下的
   是那串字符串而不是整个对象。响应里也**不含任何密钥派生的检查值**——行上的
   `key_fingerprint` 只留在库里：每次加密取新 salt，它标识的是一个 blob 而不是一把密钥，
   跨行无法比较，于是它答不出任何问题，只是白给离线试猜多一个靶子。
3. **不假装安全**：主密钥与密文在同一台主机上，所以这一层防的是业务库读取面（备份、副本、
   `pg_dump`、能 `SELECT` 的人），不防已拿下主机的人。需要后者的部署继续用 `env://` + 外部密钥管理。
4. **没配主密钥就是没有这个功能**：不注册后端、端点 503、`/setup.credentials.masterKeyConfigured=false`，
   服务照常启动。把可选功能缺失变成起不来，会让一台本来能跑的生产机因噎废食。

---

## 15. 与契约 1.19.0 的逐条核对

**核对基线**：`S1/docs/openapi.json`，`info.version` 1.19.0，**147 条路径 / 172 个操作 / 288 个组件**；`S5/contract/openapi.json` 与 `S5M/contract/openapi.json` 是它的随包副本，与它逐字相同（sha256 一致）。本文描述的全部端点都能在其中找到，反向也成立：带 `S5IF-*` 追溯的 **142 个操作**全部在本文有对应条目（含路径与语义）。

**路径参数命名**：本文沿用 S5 的 camelCase 写法（`{projectId}`、`{environmentKey}`、`{runId}`），契约里是 snake_case（`{project_id}`、`{environment_key}`、`{run_id}`）。核对时按"段数与字面段相同、参数名忽略"归一，两者一一对应；这也是 S5 `sync-contract.mjs` 的比对口径。

**三处 `x-requirements` 与实际不符（S1 待改，S5 侧的覆盖检查会误报）**：

| 端点 | 契约现在挂的 | 应挂 | 说明 |
| --- | --- | --- | --- |
| `POST /api/v1/projects/{projectId}/uid-pool/replenish` | `S5IF-BP09` | `S5IF-BP18` | BP09 是环境绑定读取（GET `.../environment-bindings`），与本条无关；同一 ID 挂在两个不相关的读/写上 |
| `POST /api/v1/environments/{environmentKey}/delete-objects` | `S5IF-G08`（仅幂等键） | 追加 `S5IF-ENV13` | 端点本身没有需求编号，只剩"幂等键"这一条追溯 |
| `POST /api/v1/artifacts/{artifactId}/purge` | 无 `S5IF-*` | 追加 `S5IF-FLD14` | 只有 `CP-*`；S5 的"覆盖检查"会把它列为未覆盖的 S5 端点 |

另有两条**编号复用**不是错误、但读文档时需按路径区分：`S5IF-GOV07` 同时给 `GET /governance/prompts` 与 `GET /governance/provider-catalogue`；`S5IF-FLD08` 同时给 4 条问题读与 1 条问卷写。

**未解决的取值冲突（需求层面，非文档笔误）**：项目 `codePrefix` 在 S1 的请求校验里是 `^[A-Za-z][A-Za-z0-9_]{0,15}$`（收小写），而 S2 的 `generationPolicy.codePrefix` Schema 是 `^[A-Z][A-Z0-9_]{0,15}$`（只收大写）。S5 表单目前按"只允许大写"实现；建议 S1 把 `_CODE_PREFIX` 收紧到 S2 的规则，否则小写前缀会通过 S1、在 S2 侧失败（见 S5IF-BP19）。

**复核命令**（在仓库根执行）：

```bash
# 1) 契约规模与三份副本一致性
python3 - <<'PY'
import json, hashlib
p='S1/docs/openapi.json'; d=json.load(open(p))
ops=[(m,pth) for pth,i in d['paths'].items() for m in i if m in ('get','post','put','patch','delete')]
print(d['info']['version'], len(d['paths']), len(ops), len(d['components']['schemas']))
for q in ('S5/contract/openapi.json','S5M/contract/openapi.json'):
    print(q, hashlib.sha256(open(q,'rb').read()).hexdigest()==hashlib.sha256(open(p,'rb').read()).hexdigest())
PY

# 2) 本文的端点 vs 契约路径（参数名归一；正文里常省略 /api/v1 前缀，故用子串比对）
python3 - <<'PY'
import json, re
d=json.load(open('S1/docs/openapi.json')); doc=open('requirements/S1_add_api_for_S5.md').read()
norm=lambda s: re.sub(r'\{[^}]+\}','{}', s.replace('/api/v1','').lower())
paths={norm(p) for p in d['paths']}
docn=norm(doc)
s5={norm(p) for p,i in d['paths'].items()
    if any(r.startswith('S5IF') for m,o in i.items() if m in ('get','post','put','patch','delete')
           for r in (o.get('x-requirements') or []))}
mentioned={norm(m) for m in re.findall(r'/api/v1/[A-Za-z0-9_\-{}/]+', doc)}
print('described but not in contract:', sorted(mentioned-paths))
print('S5 ops not described:', sorted(n for n in s5
      if not re.search(re.escape(n)+r'(?![/a-z0-9_\-])', docn)))
PY
```

两条命令的期望输出：两份副本各为 `True`；`described but not in contract: []`；`S5 ops not described: []`。**0.4.0 交付时两者均为空**（参数名归一之后）。

---

## 附录：本文引用的 S1 代码位置速查

| 关注点 | 位置 |
| --- | --- |
| 路由注册（172 个操作，15 个分组模块） | `S1/src/factory_control/rest/routes/__init__.py:35-57`、`rest/app.py:150-155`（原 `rest/app.py:70-132,147-911` 已按资源族拆分） |
| `X-Operator`／`Idempotency-Key`／`Accept-Language`／`X-Correlation-Id` 中间件 | `rest/middleware.py:37-49,79-88,104-155,261-281` |
| 错误信封与状态码表 | `rest/app.py:53-67`、`S1/src/factory_control/errors.py:64-105` |
| 错误码中英目录 | `S1/src/factory_control/i18n.py:12-82` |
| 阶段／项目／Run／Gate／Question 投影 | `services/workflow.py:54-103`、`services/project.py:249-296`、`services/gates.py:61-88`、`services/questions.py:32-53` |
| `allowed-actions` 与原因码 | `services/workflow.py:628-726`、`domain/actions.py:23-72` |
| SSE 帧与补发、`stream.reset` | `rest/app.py:889-917`、`events/sse.py:48-115`、`events/event_log.py:80-106,149-159` |
| 三方端口方法 | `ports/protocols.py`（Blueprint `:92-182`、Intelligence `:284-297`、Execution `:306-360`） |
| 转发适配器与方法表 | `adapters/registry.py:29-130` |
| S4／S2／S3 镜像 DTO | `ports/dto.py:237-329`（环境族）、`:29-57`（`_Mirror` 与 `Issue`） |
