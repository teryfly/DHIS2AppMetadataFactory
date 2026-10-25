# DHIS2 App Studio / Metadata Factory

# 软件需求规格说明书（SRS）

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | DHIS2 App Studio / Metadata Factory 软件需求规格说明书 |
| 文档版本 | V1.4.0（**对照实现反向校准**：S1 对外契约 **1.19.0 / 147 条路径 / 172 个操作**；Question 由逐题回答改为**整页问卷**（一次提交、最多恢复一次、`answerMode` 单选/多选与 `recommended`）；模型提供方增加连通性探测、模型改为差量 `PATCH` 编辑；环境增加定向删除；项目增加 `codePrefix` 与 UID 池补池；运行拓扑补 `damf-worker` 周期任务与 `/readyz`；`FACTORY_AUTH_MODE=off` 明确为兼容模式，生产必须启用认证；里程碑表补当前实现状态。上一版 V1.3.0（延后条款全部转本期交付；S1／S5／S5M 三份子系统 SRS 同批改版）） |
| 文档状态 | 正式发布 |
| 发布日期 | 2026-10-25 |
| DHIS2 兼容范围 | v2.40～v2.43（每个版本通过对应的版本能力 Profile 支持；MVP 首个 Profile 为 v2.41） |
| 实现语言 | 后端 Python 3.12+；前端 TypeScript（S5：React；S5-M：Vue 3） |
| 适用读者 | 产品、架构、后端、前端、测试、DHIS2 实施、运维与安全团队 |
| 配套文档 | 《00 总体架构与集成约定》V1.3.0；S0～S5M 子系统 SRS（各文档版本见 00 文档 0.4 文档清单） |

本文档与《00 总体架构与集成约定》（下称“00 文档”）及 S0～S5 子系统 SRS 共同构成本产品的需求规格集：本文档定义系统级需求；00 文档定义子系统划分、数据归属与集成约定；子系统 SRS 把本文档的需求分解到各子系统并细化为可开发的需求。三者保持一致；本规格集之外的任何草稿、讨论记录均不构成需求依据。

---

## 1. 引言

### 1.1 目的

本文档定义 DHIS2 App Studio / Metadata Factory（以下简称“系统”或“Factory”）的产品边界、功能需求、外部接口、非功能需求、数据模型、验收标准与发布规划，作为产品、架构、开发、测试和实施团队的共同依据。

### 1.2 产品范围

系统把业务需求与设计资料转化为一套互相一致、可验证、可部署、可交接的 DHIS2 应用交付物，覆盖以下链路：

1. 需求采集与规范化；
2. 领域建模与 Use Case 建模；
3. 目标 DHIS2 环境的元数据发现；
4. DHIS2 功能与元数据映射设计；
5. App Blueprint 构建与版本管理；
6. 元数据包、OpenAPI、UI Specification 的确定性编译；
7. 静态校验、跨产物一致性校验与 DHIS2 服务端 `VALIDATE`；
8. 受人工审批控制的 DHIS2 `COMMIT` 与部署后验证；
9. 面向前端团队的自足式开发包（Self-sufficient Frontend Development Package）。

### 1.3 需求级别约定

| 关键词 | 含义 |
| --- | --- |
| 必须（MUST） | 强制要求，未满足即不通过验收 |
| 禁止（MUST NOT） | 强制禁止的行为 |
| 应（SHOULD） | 强烈建议；偏离时须在设计文档中记录理由 |
| 可（MAY） | 可选能力 |

### 1.4 需求编号约定

| 前缀 | 领域 |
| --- | --- |
| FR-BP / FR-REG | App Blueprint / Metadata Registry |
| FR-COV | DHIS2 元数据覆盖与依赖 |
| FR-WF | 工作流与状态 |
| FR-REQ | 需求输入与规范化 |
| FR-DOM / FR-DSC / FR-MAP | 领域建模 / 环境发现 / 映射 |
| FR-PG / FR-API / FR-UII | 页面 / API Intent / UI Intent |
| FR-AG | Agent 运行时 |
| FR-CMP | Compiler |
| FR-VAL | 校验 |
| FR-ENV / FR-CAP | 环境 / 能力 Profile |
| FR-EXE / FR-SNP | 执行 / Snapshot |
| FR-GATE | Human Gate |
| FR-FE | 前端开发包 |
| FR-UI | Factory UI |
| FR-IF | 外部接口 |
| FR-POL | 数据分级与模型策略 |
| FR-SEC | 安全、权限与审计 |
| FR-DATA | 数据模型与数据管理 |
| FR-DEP | 部署 |
| FR-TST-* | 回归测试环境需求 |
| NFR-* | 非功能需求 |
| AC-* | 验收标准 |

子系统 SRS 使用各自的编号前缀：FD（S0 公共基础库）、CP（S1 Factory 控制平面）、BP（S2 Blueprint 与构建）、AI（S3 需求与智能）、DX（S4 DHIS2 集成与执行）、UI（S5 Factory UI）。子系统需求以来源标记追溯到本文档的需求编号。

### 1.5 权威来源顺序

当信息冲突时，按以下顺序判定：

1. 目标 DHIS2 实例的实际能力探测结果（Capability Profile）；
2. 对应版本的 DHIS2 官方 API / Schema 文档；
3. 项目已批准的 App Blueprint 版本；
4. 本 SRS；
5. 00 文档；
6. 子系统 SRS（对本 SRS 的细化，不得与之冲突）；
7. Agent 的推断。

本文档中的 JSON、YAML 示例仅用于说明结构，禁止作为最终 Schema 依据。

---

## 2. 总体描述

### 2.1 产品定位

系统是一个 Human-in-the-loop 的 DHIS2 应用工厂：LLM 负责理解与决策，App Blueprint 负责统一语义，确定性 Compiler 负责生成，Validator 负责拦截错误，Execution Service 负责受控地操作 DHIS2。系统不是开放式聊天工具。

### 2.2 产品目标

- 对于受支持的业务需求，在人工确认关键决策后，形成一套互相一致、可验证、可部署、可交接的完整交付物。
- 覆盖绝大多数常见 DHIS2 应用的元数据初始化场景，包括空白实例建设与已有实例增量建设。
- 以 App Blueprint 作为项目级单一语义事实源，驱动元数据、API、UI 规格与开发包的生成。
- 杜绝 UID 漂移、跨产物不一致、过时 API 使用和未经审批的变更。
- 支持阶段暂停、恢复、重跑、从失败节点继续，以及全链路审计。
- 使前端团队拿到开发包后，无需额外口头说明即可开始开发与联调。

### 2.3 非目标

- 禁止在未经人工审批的情况下修改已批准的业务架构或目标环境。
- 禁止覆盖或删除目标实例中不属于本项目管理范围（Managed Scope）的对象。
- 系统不承诺把 DHIS2 完整回滚到提交之前的状态。
- 系统不把 DHIS2 抽象为通用关系数据库，也不生成独立于 DHIS2 的自建业务后端。
- V1.0.0 的 MVP 里程碑不自动生成可运行的前端代码；前端代码生成器属于后续里程碑（M5）。
- 系统不保证任意 DHIS2 自定义扩展或第三方插件都可被自动化处理。
- 系统不执行 DHIS2 实例的数据库备份与恢复，这属于实例运维职责。

### 2.4 用户角色

| 角色 | 主要职责 | 优先级 |
| --- | --- | --- |
| Solution Architect | 需求确认、领域与映射设计、架构与设计审批 | 核心用户 |
| DHIS2 Implementer | 环境配置、元数据审核、VALIDATE、COMMIT 执行 | 核心用户 |
| Frontend Developer | 消费开发包，开发并联调自定义应用 | 交付物消费者 |
| Business Analyst | 提供与审核业务需求、Use Case | 协作用户 |
| Deployment Approver | 对生产类环境的提交进行审批 | 协作用户 |
| Platform Administrator | 管理环境、模型提供方、数据策略、用户与权限 | 运维用户 |

认证、角色与权限、生产环境多人审批与职责分离**均为本期交付**（见 21.1）。未启用认证时才以操作者名称标识并写入审计与审批记录；启用认证后一律以会话主体为准，授权判定由 `authorize()` 真值给出。

系统面向具备 DHIS2 基础知识的实施人员设计；面向非技术业务人员的引导式体验不属于 V1.0.0 范围。

### 2.5 交付模式

每个项目在创建时必须声明交付模式，交付模式决定需要执行的阶段与必需的交付物。

| 模式 | 链路 | 必需交付物 |
| --- | --- | --- |
| A：Metadata-only | 需求 → 元数据 → 使用 DHIS2 原生应用（Capture、Data Entry、Dashboard、Maintenance 等） | Blueprint、元数据包、校验与执行报告、配置说明 |
| B：Custom App | 需求 → 元数据 → API Intent → UI Intent → 开发包 → 自定义 DHIS2 Web App | 模式 A 全部交付物 + OpenAPI、UI Specification、Frontend Development Package |

项目可以从模式 A 升级为模式 B，升级必须产生新的 Blueprint 版本；不支持由模式 B 降级为模式 A。

### 2.6 项目类型

系统以 Brownfield 为优先场景，同时在架构上完整支持 Greenfield。

- **Greenfield**：目标实例中不存在本项目相关对象，所有托管对象均为新建。
- **Brownfield**：目标实例中已存在组织机构、用户、数据元、选项集、Program 等对象，项目需要新建、复用、更新或引用其中一部分。Brownfield 项目必须先完成目标环境的元数据发现（Discovery），再进入映射设计。

### 2.7 端到端用户旅程

```text
创建项目：选择交付模式、项目类型、目标环境
  → 上传或填写需求资料
  → 审核规范化需求                    [REQUIREMENT_GATE]
  → 审核领域模型与 Use Case          [DOMAIN_GATE]
  → 发现目标环境已有元数据与能力
  → 确认 DHIS2 映射与对象处置方式    [ARCHITECTURE_GATE]
  → 审核页面、API 与 UI 规格（模式 B）[DESIGN_GATE]
  → 基线编译与环境无关校验
  → 按目标环境逐个部署（先非生产、后生产），每个环境一个 Deployment Run：
      环境绑定编译 → 审核元数据与变更 Diff    [PACKAGE_GATE]
      → 执行 DHIS2 VALIDATE → 审批            [DEPLOY_GATE]
      → 发起 COMMIT → 部署后验证 →（可选）Fixture 导入
  → 下载前端开发包（模式 B）                  [HANDOFF_GATE]
```

### 2.8 运行环境

- Factory 服务端运行于 Linux 容器环境（Docker / Kubernetes）。
- Factory UI 运行于现代桌面浏览器（见 NFR-UX-002）。
- 目标 DHIS2 实例版本必须在 v2.40～v2.43 范围内，且系统已提供该版本的 Profile；v2.39 及以下版本不受支持；更新的版本以新增 Profile 的方式支持。
- 执行 DHIS2 操作的工作进程必须能够通过 HTTPS 访问目标实例。

### 2.9 设计与实现约束

- 后端必须使用 Python 3.12+；前端必须使用 TypeScript。
- 生产环境必须使用持久化工作流引擎（Temporal）、PostgreSQL、Secret Manager。
- 系统划分为 S0～S5 六个子系统，源码位于独立仓库、独立开发与发版；后端子系统共用一个 PostgreSQL 数据库（每个子系统独占一个 schema），彼此只通过公共 Python 库 API 调用，不使用消息中间件（见 00 文档）。
- 与 DHIS2 的全部交互必须经由官方 Web API，禁止直接访问 DHIS2 数据库。
- 所有模型调用必须经由统一的 `LLMProvider` 接口与 Policy Engine。

### 2.10 假设与依赖

| 编号 | 假设或依赖 | 不成立时的影响 |
| --- | --- | --- |
| ASM-01 | 每个项目至少有一个可访问的非生产 DHIS2 环境（DEV 或 TEST） | 无法执行 VALIDATE 与晋级前验证 |
| ASM-02 | 测试体系可经互联网访问 DHIS2 官方在线演示实例（v2.40、v2.41、v2.42、v2.43 各一个） | 契约测试与 Profile 回归无法执行 |
| ASM-03 | 组织至少提供一个 LOCAL_ONLY 或 PRIVATE_NETWORK 模型，或批准一个外部提供方 | Agent 阶段不可用，只能使用人工编写模式（FR-AG-011） |
| ASM-04 | 用于 DHIS2 操作的服务账户由目标实例管理员创建并授予所需权限 | 连接测试失败，相关阶段 BLOCKED |
| ASM-05 | 生产实例的数据库备份由实例运维负责 | DEPLOY_GATE 的备份确认项无法勾选 |

---

## 3. 核心设计原则

以下原则约束所有功能设计；具体需求与原则冲突时，以原则为准并修订具体需求。

| 编号 | 原则 | 要求 |
| --- | --- | --- |
| P-01 | Factory First | 系统是有明确阶段、产物与执行边界的应用工厂，而不是开放式聊天工具。 |
| P-02 | Blueprint First | 所有编译器与下游 Agent 只消费 App Blueprint；自然语言只作为输入与解释层。 |
| P-03 | Decide, Then Compile | LLM 只产出结构化决策与 Blueprint Patch；元数据 JSON、OpenAPI、UI Specification 等最终产物全部由确定性 Compiler 生成。 |
| P-04 | Stable Identity | 所有托管对象的 UID 由 Registry 分配与保存，一经分配不得重新生成。 |
| P-05 | No Hidden Mutation | 任何组件都不得原地修改已批准的产物；修改必须形成新版本并记录原因与决策。 |
| P-06 | Explain Before Execute | 每个会产生业务后果的步骤都必须说明：发现了什么、准备做什么、为什么、仍有哪些不确定性、用户可以改变什么、下一步产出什么。 |
| P-07 | Validate Before Commit | 同一份已批准的载荷依次经过静态校验、DHIS2 `VALIDATE`、人工审批，然后才能 `COMMIT`。 |
| P-08 | Human Gate Is State | 人工审批是持久化的领域状态，绑定产物哈希，而不是界面上的一次点击。 |
| P-09 | Environment Is Explicit | 每次 DHIS2 操作都必须显式绑定一个 Environment；不存在隐式默认环境。 |
| P-10 | Capability over Assumption | DHIS2 行为以目标实例的 Capability Profile 为准，禁止依赖 Agent 的常识推断。 |
| P-11 | Data Minimization | 发送给 LLM 的内容遵循数据分级策略，默认禁止外发敏感数据。 |
| P-12 | Handoff Is a Product | 前端开发包是核心交付物，与元数据包同等对待，有完整性门禁。 |

职责分工：LLM 负责理解与决策；Blueprint 负责统一语义；Registry 负责身份一致性；Compiler 负责确定性生成；Validator 负责拦截错误；Orchestrator 负责状态；Human Gate 负责高风险决策；Execution Service 负责 DHIS2 操作；Audit 负责全链路追溯；Factory UI 让用户始终知道当前状态、原因和下一步。

---

## 4. 系统架构

系统由四个逻辑层和一个贯穿全局的治理平面组成，在实现上划分为 S0～S5 六个子系统（4.5）。只有执行层可以访问 DHIS2；只有 Compiler 可以生成最终产物；Agent 只产出结构化决策与 Blueprint Patch。

### 4.1 分层线框图

```text
+--------------------------------------------------------------+   +----------------------+
| L1  Factory UI（浏览器）                              [S5]    |   | G  治理平面          |
|     工作台 | 阶段导航 | 预览与 Diff | 审批 | 部署控制台        |   |                      |
+------------------------------+-------------------------------+   |  Prompt Registry     |
                               | REST 命令/查询 + SSE 事件          |  Model Registry      |
+------------------------------v-------------------------------+   |  Policy Engine  [S3] |
| L2  应用层                                            [S1]    |   |  Secret 适配    [S0] |
|     Workflow Orchestrator | Human Gate Service                |<->|  Audit Service  [S0] |
|     Project | Question | Change Request | 通知                |   |  Artifact Service[S0]|
+------------------------------+-------------------------------+   |  可观测性            |
                               | 库 API 调用（在 Temporal 活动中）  |  成本治理       [S3] |
+------------------------------v-------------------------------+   |                      |
| L3  Blueprint 与智能层                                        |   |  （横向服务于        |
|   +----------------+   Patch   +----------------+   读取      |   |   L1 至 L4 全部层）  |
|   | Agent Runtime  | --------> | App Blueprint  | -------+    |   |                      |
|   | 结构化决策 [S3]|           | 版本化事实源   |        |    |   |                      |
|   +----------------+           | 与 Registry[S2]|        v    |   |                      |
|                                +----------------+ +------------+ |                      |
|                                                   | Compilers  | |                      |
|                                                   | Validators | |                      |
|                                                   |       [S2] | |                      |
|                                                   +------------+ |                      |
+------------------------------+-------------------------------+   |                      |
                               | 已批准的产物 + 执行请求           |                      |
+------------------------------v-------------------------------+   |                      |
| L4  执行层                                            [S4]    |   |                      |
|     Environment | Capability | Discovery | Drift | Snapshot   |   |                      |
|     Metadata / DataStore / Tracker Data / App Upload 执行器   |   |                      |
|     部署后验证                                                |   |                      |
+------------------------------+-------------------------------+   +----------------------+
                               | HTTPS（DHIS2 Web API，按 Environment 选择凭证）
+------------------------------v-------------------------------+
|  目标 DHIS2 实例（DEV / TEST / UAT / PRODUCTION）             |
+--------------------------------------------------------------+
```

### 4.2 调用关系（文字描述）

| 编号 | 调用方 → 被调用方 | 方式 | 内容 | 约束 |
| --- | --- | --- | --- | --- |
| C-01 | Factory UI → 应用层 | REST | 用户命令、查询、问题回答、审批 | UI 不访问数据库、不访问 DHIS2、不持有凭证 |
| C-02 | 应用层 → Factory UI | SSE | 阶段、Agent、校验、执行状态事件 | 事件只用于通知，状态以 REST 查询为准 |
| C-03 | Orchestrator → Agent Runtime | 库 API（在持久化工作流的活动中） | 带显式上下文的 Agent 运行请求 | 运行前必须经 Policy Engine 判定 |
| C-04 | Agent Runtime → 模型提供方 | `LLMProvider` | 受限上下文 | 仅允许 Policy Engine 批准的内容 |
| C-05 | Agent Runtime → Blueprint Service | 库 API | 决策与 Blueprint Patch；Question 随运行结果返回给 Orchestrator | Patch 进入工作草稿（PROPOSED），由 Gate 批准后生效 |
| C-06 | Orchestrator → Compilers | 库 API | 已批准的 Blueprint 版本 + Capability Profile + 构建上下文 | Compiler 不调用 LLM，不修改 DHIS2 |
| C-07 | Orchestrator → Validators | 库 API | 编译产物 | Validator 只报告，不修复 |
| C-08 | Orchestrator → 执行层 | 库 API | 已批准产物、期望载荷哈希、环境键 | 提交前后端重新执行 Gate 检查；执行层再校验载荷哈希 |
| C-09 | 执行层 → Secret Manager | Secret 引用 | 获取目标环境凭证 | 凭证不离开执行层进程内存 |
| C-10 | 执行层 → DHIS2 | HTTPS | 发现、VALIDATE、COMMIT、读取验证 | 每个请求绑定唯一环境 |
| C-11 | 全部组件 → Audit Service | 库 API（同一数据库事务） | 状态迁移、决策、审批、执行、模型调用 | 审计记录与业务状态在同一事务中写入；审计写入失败则业务操作失败 |

### 4.3 组件职责

| 组件 | 所属子系统 | 职责 | 禁止 |
| --- | --- | --- | --- |
| Factory UI | S5 | 展示阶段、产物、问题、Diff、审批与执行状态；发起用户命令 | 直接访问数据库或 DHIS2；持有 DHIS2 凭证；自行判断动作是否合法 |
| Workflow Orchestrator | S1 | 创建与推进工作流与 Deployment Run、检查 Gate、调度 Agent / Compiler / 执行、处理暂停恢复与重试、处理失效传播 | 生成业务内容 |
| Human Gate Service | S1 | 创建 Gate、绑定内容哈希、记录审批与意见、失效过期审批、判定环境保护规则、通知审批人 | 接受由客户端声明的审批结果 |
| Artifact Service | S0 | 存储产物内容与元数据、计算与回读校验 SHA-256、管理版本、分级与来源关系 | 原地修改产物内容 |
| Blueprint Service | S2 | 维护 Blueprint 版本与工作草稿、校验与应用 Patch、判定变更级别、管理 Registry 与 UID 分配 | 接受未通过 Schema 校验的 Patch |
| Agent Runtime | S3 | 构造受限上下文、调用模型、校验结构化输出、产出决策、Patch 与 Question | 直接写他方数据、调用 DHIS2、修改工作流状态、生成 UID |
| Compilers | S2 | 从 Blueprint 确定性生成元数据包、DataStore 变更集、OpenAPI、UI Specification、Fixture、开发包 | 调用 LLM；引入随机性 |
| Validation Service | S2（服务端校验属 S4） | 执行 Blueprint、产物静态、跨产物、能力兼容性校验 | 自动修复产物 |
| Environment Service | S4 | 管理环境档案与保护设置、连接测试、版本发现、Capability Profile、元数据发现 | 在未绑定环境时执行操作 |
| Execution Service | S4 | 执行计划、漂移检查、Snapshot、VALIDATE、COMMIT、异步任务轮询、部署后验证、环境提交锁 | 产生架构决策；绕过 Gate |
| Policy Engine | S3 | 判定数据能否发送给指定模型提供方 | 被 Agent 或 UI 绕过 |
| Audit Service | S0 | 以只追加、哈希链方式记录全部关键事件 | 删除或修改审计记录 |

### 4.4 架构边界

- 任何跨产物的关键事实（对象类型、UID、字段、关系、权限）都必须可追溯到某个 Blueprint 版本中的条目。
- Agent 输出只是候选；是否接受由应用层根据 Schema 校验、策略与 Gate 决定。
- DHIS2 Client 只负责协议与执行，不包含业务判断。
- 每类数据只有一个所有者子系统；跨子系统只保存对方的 ID（需冻结状态时同时保存内容哈希），禁止复制他方数据作为事实来源。

### 4.5 子系统划分

| 编号 | 子系统 | 范围 | 交付形式 |
| --- | --- | --- | --- |
| S0 | 公共基础库 | 数据库与事务、产物存储、审计哈希链、幂等、对象存储与 Secret 适配、错误模型与公共工具、进度与取消协议、迁移运行器、测试套件 | Python 库 |
| S1 | Factory 控制平面 | REST 与 SSE、项目、工作流与 Deployment Run 编排、Question、Gate、Change Request、通知、系统设置、运行时组装 | Python 库 + factory-api / factory-worker |
| S2 | Blueprint 与构建 | Blueprint 版本与草稿、Patch、变更级别与影响分析、Registry 与 UID、依赖图、全部 Compiler、静态与跨产物校验、开发包完整性检查 | Python 库 |
| S3 | 需求与智能 | 输入解析与 NRR、Agent Runtime、LLMProvider、Policy Engine 与脱敏、Prompt / Model Registry、评测、成本 | Python 库 |
| S4 | DHIS2 集成与执行 | Environment、Capability Profile、Discovery、执行器、漂移、Snapshot、服务端校验、部署后验证 | Python 库 |
| S5 | Factory UI | 全部用户界面 | 静态前端 |

子系统的数据归属、依赖规则、集成约定与调用目录见 00 文档。

---

## 5. App Blueprint

App Blueprint 是项目级聚合根（Aggregate Root）和唯一的语义事实源；Mapping Model 与 Metadata Registry 是它内部受版本控制的规范化子模型，而不是并列的事实源。

### 5.1 结构

```text
App Blueprint
├── project                 交付模式、项目类型、目标 DHIS2 版本约束、语言（这些项目语义只存于 Blueprint）
├── requirements            规范化需求（引用原始输入产物）
├── domain                  领域对象、关系、生命周期
├── useCases / actors       Use Case 与参与者
├── organizationModel       组织层级需求与 OrgUnit 引用
├── accessModel             角色、用户组、共享（Sharing）需求
├── architecture            架构决策记录（ADR）
├── mapping                 领域元素 → DHIS2 结构的映射模型
├── registry                DHIS2 对象身份与引用的规范化索引
├── pages                   页面模型（模式 B）
├── apiIntents              页面级 API Intent（模式 B）
├── uiIntents               页面级 UI Intent（模式 B）
├── rules                   校验规则、Program Rule、指标等业务规则意图
├── fixtures                合成演示数据规格
├── environmentConstraints  目标环境约束与能力要求
└── generationPolicy        编译选项、code 前缀与命名规范、UID 池参数、环境绑定匹配规则、默认语言与翻译语言、删除策略、决策与抽取置信度阈值
```

- **FR-BP-001** Blueprint 必须有正式 JSON Schema，并随 `blueprintSchemaVersion` 版本化。
- **FR-BP-002** Blueprint 禁止内嵌原始文档、图片、完整 Markdown 或完整元数据 JSON，这些内容必须通过产物引用（Artifact Reference）关联。
- **FR-BP-003** Blueprint 中的每个条目必须具有稳定的 `businessKey`，并记录来源（source artifact 与 decision id）。

### 5.2 标识、版本与工作草稿

每个 Blueprint 版本必须记录：`blueprintId`、`version`、`contentHash`、`parentVersion`、`status`、`sourceArtifacts[]`、`decisionIds[]`、`createdBy`、`createdAt`、`schemaVersion`、`changeLevel`。

每个设计阶段在一个**工作草稿**上工作：草稿以当前正式版本为基准，阶段内的 Patch 叠加在草稿上；阶段结束时草稿被**冻结**（物化内容、校验并得到草稿哈希），Gate 绑定该哈希；Gate 批准时草稿**提升**为新的正式版本。同一项目同一时刻最多一个打开的草稿，草稿只能修改所属阶段范围内的 Blueprint 分节。

| 变更级别 | 触发条件 | 版本号 | 影响 |
| --- | --- | --- | --- |
| Cosmetic | 描述、显示名、翻译、注释等不影响结构的修改 | 修订号 +1 | 下游产物重新编译；PACKAGE_GATE、DEPLOY_GATE、HANDOFF_GATE 的既有审批失效 |
| Minor | 新增兼容对象（新数据元、新页面、新规则），不改变既有对象的类型、UID、关系；交付模式由 A 升级为 B | 次版本号 +1 | 受影响的下游阶段重新进入待审批 |
| Major | 改变业务实体、DHIS2 对象类型、UID、关系、Disposition、Program 结构、权限模型或 API 语义；删除或停用条目 | 主版本号 +1 | 必须重新通过 ARCHITECTURE_GATE |

- **FR-BP-004** 任何对 Blueprint 的修改都必须以 Blueprint Patch 提交，经工作草稿与 Gate 批准后生成新版本；禁止原地修改。正式版本只在草稿提升、交付模式升级、项目导入时产生。
- **FR-BP-005** 变更级别与版本号由系统根据草稿与基准版本的差异自动判定，用户不得指定或降级。初始化产生版本 `0.1.0`。

### 5.3 Blueprint Patch

Patch 采用 JSON Patch（RFC 6902）语义加业务元数据：

```json
{
  "patchId": "patch-0142",
  "baseVersion": "1.4.0",
  "author": { "kind": "AGENT", "agentRunId": "run-881" },
  "decisionIds": ["MAP-017"],
  "operations": [
    { "op": "add", "path": "/mapping/entities/delivery", "value": { "...": "..." } }
  ],
  "rationale": "分娩记录作为 Pregnancy Program 的一个 Program Stage",
  "declaredImpact": { "affectedArtifacts": ["METADATA_PACKAGE", "API_CONTRACT:delivery-form"] }
}
```

- **FR-BP-006** Patch 的 `baseVersion` 必须等于工作草稿的基准版本，否则拒绝并以 Question 交由人工合并；Patch 只能触及草稿阶段范围内的分节，且禁止直接写入或修改 UID。
- **FR-BP-007** 来自 Agent 的 Patch 进入工作草稿后处于 `PROPOSED` 状态，只有所在阶段 Gate 批准、草稿提升为正式版本后才生效。
- **FR-BP-008** 用户可以直接提交人工编写的 Patch（`author.kind = USER`），与 Agent Patch 走相同的校验与审批流程。
- **FR-BP-009** Blueprint 必须支持导出为独立的项目包（Blueprint 全部版本、Registry 与被引用的产物，含重新编译所需的 Capability Profile 与发现结果），并支持在另一 Factory 部署中导入；导入时产物获得新 ID，引用按映射改写，重新编译输出哈希必须一致。

### 5.4 Mapping Model

每条映射必须包含：领域元素的 `businessKey`；选定的 DHIS2 表达（对象类型与结构位置）；备选方案及放弃理由；风险与置信度；关联的架构决策 ID 与审批记录。

### 5.5 Metadata Registry

Registry 是 Blueprint 中对 DHIS2 对象身份与引用的规范化索引，是所有 UID 的唯一来源。条目示例：

```yaml
- businessKey: pregnancy.delivery
  dhis2Type: programStage
  uid: pSdLvRy0001
  code: MC_PS_DELIVERY
  disposition: CREATE
  managed: true
  controlledFields: []
  status: APPROVED
  sourceEnvironment: null
  observedLastUpdated: null
  observedHash: null
  decisionId: MAP-017
  environmentBindings: {}      # 其他环境中的 UID 与观测基准，见 FR-REG-009
```

#### 5.5.1 对象处置语义（Disposition）

| Disposition | 含义 | 是否进入导入载荷 | 是否属于 Managed Scope |
| --- | --- | --- | --- |
| CREATE | 本项目新建的对象 | 是 | 是 |
| UPDATE | 目标环境已存在、本项目负责修改的对象 | 是（仅修改受控字段） | 是 |
| REUSE | 目标环境已存在、本项目直接使用且不修改的对象 | 否 | 否，但作为依赖被校验 |
| REFERENCE | 仅以 UID 引用、由他人维护的对象（如 OrgUnit、既有用户组、default CategoryCombo） | 否 | 否 |
| EXTERNAL | 不在任何 DHIS2 环境中、由外部系统提供的依赖（如外部身份源、互操作接口） | 否 | 否 |
| DEPRECATED | 本项目曾管理、现已停止使用的对象 | 仅在批准的变更请求中 | 是 |

Managed Scope 按“项目 × 环境”确定：某项目在某环境中 Disposition 为 CREATE、UPDATE、DEPRECATED 的对象集合。同一环境中同一 UID 不得被两个项目同时声明为 CREATE 或 UPDATE（所有权冲突为 BLOCKING）。

- **FR-REG-001** UPDATE 条目必须声明受控字段清单（`controlledFields`）；编译器只输出这些字段的变更，其余字段保持目标环境原值。
- **FR-REG-002** REUSE、REFERENCE、UPDATE 条目必须记录发现时的 `sourceEnvironment`、`observedLastUpdated` 与 `observedHash`，用于漂移检测。
- **FR-REG-003** 同一业务概念禁止出现两个 UID；同一 UID 禁止对应两个业务概念。
- **FR-REG-004** 已批准条目的 UID 禁止重新生成；停用对象进入 DEPRECATED 状态，其 UID 永不复用；已分配但所属 Patch 被拒绝或丢弃的 UID 置为作废，同样永不复用。

#### 5.5.2 UID 与 code 策略

- **FR-REG-005** UID 必须符合 DHIS2 规则：11 个字符，首字符为字母，其余为字母或数字。
- **FR-REG-006** CREATE 对象的 UID 在条目首次进入工作草稿时分配一次，此后持久化保存；重复编译禁止产生新 UID。CREATE 与 UPDATE 对象的 UID 在全部目标环境中一致。
- **FR-REG-007** UID 由目标 DHIS2 实例官方的 UID 生成接口产生（经执行层以只读方式调用），批量取得后存入项目 UID 池，分配时从池中取出。分配时检查本 Factory 全部项目中无相同 UID；冻结草稿与环境绑定编译时检查项目各目标环境中不存在同 UID 的其他对象。
- **FR-REG-008** 每个托管对象必须分配稳定的 `code`，按 `generationPolicy` 中的命名规范生成，作为人工识别与跨环境对照的辅助标识；code 在目标环境同类型对象中必须唯一。
- **FR-REG-009** Registry 条目按环境记录环境绑定（UID 与观测基准）。设计基准环境的绑定随映射批准；其他环境的 REUSE / REFERENCE 绑定在该环境的环境绑定编译中按 code（其次按设计基准环境 UID）自动解析，UPDATE 必须按 UID 匹配；无法解析时为 BLOCKING，可由用户人工设置绑定。环境绑定不属于 Blueprint 版本内容，设置绑定不产生新版本。

---

## 6. DHIS2 元数据覆盖范围与依赖模型

产品架构覆盖完整的 DHIS2 元数据领域；实现按三个层级（Tier）交付，Tier 1 为 MVP 范围。

### 6.1 覆盖层级

| 层级 | 对象范围 | 交付里程碑 |
| --- | --- | --- |
| Tier 1 | TrackedEntityType、TrackedEntityAttribute、Program（Tracker 与 Event）、ProgramStage、ProgramStageDataElement、ProgramTrackedEntityAttribute、ProgramSection、ProgramStageSection、DataElement、OptionSet、Option、Category、CategoryOption、CategoryCombo、ProgramRule、ProgramRuleVariable、ProgramRuleAction、ProgramIndicator、ValidationRule、RelationshipType、自定义 Attribute、Sharing、OrganisationUnit 引用、UserRole、UserGroup | M2 |
| Tier 2 | DataSet、DataSetElement、Section、Indicator、IndicatorType、IndicatorGroup、DataElementGroup、CategoryOptionGroup、CategoryOptionGroupSet、DataApprovalWorkflow、DataApprovalLevel、Predictor、PredictorGroup | M5 |
| Tier 3 | Dashboard、Visualization、Map、EventVisualization、ProgramNotificationTemplate、DataStore 条目、应用配置、插件相关配置、SQL View | M5 |

- **FR-COV-001** 每个元数据对象类型在系统中必须以“类型描述器（Type Descriptor）”形式注册，包含：DHIS2 逻辑资源名（由 Capability Profile 解析为实际端点）、适用版本、依赖字段、可受控字段、编译模板、校验规则、所属 Tier。新增对象类型只需新增描述器，不修改核心流程。
- **FR-COV-002** 编译器遇到未注册或未启用的对象类型时，必须以 BLOCKING 问题中止，禁止静默忽略。

### 6.2 必须显式处理的隐式依赖

- **FR-COV-003** 每个 DataElement 都必须引用 CategoryCombo；未指定时编译器必须显式引用目标环境的 default CategoryCombo（通过发现获得其 UID，Disposition 为 REFERENCE），禁止省略该字段。
- **FR-COV-004** Program 必须显式声明 TrackedEntityType（Tracker Program）或声明为 Event Program，以及 CategoryCombo、访问级别（accessLevel）与共享设置。
- **FR-COV-005** TrackedEntityAttribute 必须区分挂载在 TrackedEntityType 上还是挂载在 Program 上，两者的访问控制含义不同。
- **FR-COV-006** 所有托管对象的共享（Sharing）必须由 `accessModel` 编译生成，使用目标版本支持的共享对象格式；禁止依赖导入后的默认共享。

### 6.3 组织机构与用户

- **FR-COV-007** OrganisationUnit 默认以 REFERENCE 方式处理：系统通过发现读取目标环境的组织层级，映射只引用既有节点。创建 OrgUnit 仅在 Greenfield 项目中且经 ARCHITECTURE_GATE 显式批准时允许。
- **FR-COV-008** 系统可以创建或更新 UserRole 与 UserGroup；系统禁止通过元数据载荷创建带密码的用户账号。演示或测试用户的创建属于 Fixture 流程，必须使用一次性生成、存入 Secret Manager 的凭证，且禁止用于 PRODUCTION 类环境。

### 6.4 多语言与自定义属性

- **FR-COV-009** Blueprint 中对象的名称、简称、描述、表单名等可显示字段必须支持多语言；`generationPolicy` 声明默认语言与翻译语言，编译器按目标版本支持的格式输出对象翻译。
- **FR-COV-010** 缺失翻译语言的可显示字段必须在静态校验中报告 WARNING。
- **FR-COV-011** 自定义 Attribute 的取值（attributeValues）必须通过 Registry 引用 Attribute 定义，编译器校验 Attribute 是否适用于该对象类型。

### 6.5 依赖图

编译器为每个元数据包构建依赖图，节点为 Registry 条目，边为字段级引用（例如 `programStage.program → program`）。依赖图的用途：

1. 引用完整性校验：每条边的目标都必须在 Registry 中存在，且 Disposition 与类型正确；
2. 变更影响分析：计算一次修改影响的下游对象、页面和 API Intent；
3. Diff 与审核展示：在 UI 中按依赖关系展示对象；
4. 编译诊断与解释：定位错误来源；
5. 删除安全检查：识别仍被引用的对象。

- **FR-COV-012** 依赖图不用于决定元数据导入顺序。导入顺序由 DHIS2 服务端在单次导入中处理，系统必须把完整的元数据包作为一次逻辑导入提交。

---

## 7. 项目生命周期与工作流

系统只有一套状态模型：工作流由环境无关的**设计主线**、每个目标环境一个的 **Deployment Run** 与模式 B 的 **HANDOFF** 组成；每个阶段有自己的状态，工作流、Run 与项目状态由阶段状态推导得出。

### 7.1 结构（文字描述）

```text
设计主线（每个项目一条，环境无关）
[1] REQUIREMENTS      需求规范化    结束条件：REQUIREMENT_GATE 通过
[2] DOMAIN            领域建模      结束条件：DOMAIN_GATE 通过
[3] DISCOVERY         环境发现      结束条件：对设计基准环境的发现完成且无阻塞问题
                                    （Greenfield 只读取版本、能力与 default 对象）
[4] MAPPING           DHIS2 映射    结束条件：ARCHITECTURE_GATE 通过
[5] PAGE_DESIGN       页面设计      结束条件：DESIGN_GATE 通过（仅模式 B；模式 A 为 SKIPPED）
[6] BASELINE_COMPILE  基线编译      结束条件：环境无关编译成功且环境无关校验无 BLOCKING

Deployment Run（每个目标环境一个，按项目登记的晋级顺序，先非生产后生产）
[R1] BIND_COMPILE      环境绑定编译  目标环境发现 + 解析环境绑定 + 编译该环境的元数据包
[R2] STATIC_VALIDATION 静态校验      结束条件：无 BLOCKING 且 PACKAGE_GATE 通过
[R3] DHIS2_VALIDATE    服务端校验    执行计划、漂移检查、Snapshot、VALIDATE 无错误
[R4] COMMIT            提交          前置条件：DEPLOY_GATE 通过且用户发起提交；结束条件：提交成功
[R5] POST_VERIFY       部署后验证    结束条件：实际状态与预期一致
[R6] FIXTURE_IMPORT    演示数据导入  可选；禁止 PRODUCTION 类环境

交付
[7] HANDOFF           开发包交付    结束条件：完整性检查通过且 HANDOFF_GATE 通过（仅模式 B；
                                    前提：至少一个非生产环境的 Run 已 VERIFIED）

回退规则：任一阶段发现问题 → 定位问题来源阶段 → 修正并产生新 Blueprint 版本
         → 从来源阶段重新推进；受影响的已通过阶段变为 INVALIDATED。
```

“设计基准环境”是项目目标环境中被指定用于 DISCOVERY 与映射的一个环境；每个 Deployment Run 在 BIND_COMPILE 中对其目标环境重新发现。

### 7.2 阶段输入与输出

| 序号 | 阶段 | 主要输入 | 主要输出 |
| --- | --- | --- | --- |
| 1 | REQUIREMENTS | 原始输入产物 | 规范化需求（NRR）、问题清单 |
| 2 | DOMAIN | NRR | 领域模型、Use Case、参与者 |
| 3 | DISCOVERY | 设计基准环境 | Capability Profile、既有元数据索引 |
| 4 | MAPPING | 领域模型、既有元数据索引 | Mapping Model、Registry、架构决策、Fixture 规格（模式 A） |
| 5 | PAGE_DESIGN | Blueprint、页面草图 | 页面模型、API Intent、UI Intent、Fixture 规格（模式 B） |
| 6 | BASELINE_COMPILE | 已批准 Blueprint 版本、设计基准环境 Capability Profile | OpenAPI、UI Specification（模式 B）、元数据基线、校验报告 |
| R1 | BIND_COMPILE | 已批准 Blueprint 版本、目标环境发现结果与 Capability Profile | 元数据包（含 Manifest）、Fixture 包、变更 Diff |
| R2 | STATIC_VALIDATION | 环境绑定编译产物 | 校验报告 |
| R3 | DHIS2_VALIDATE | 已批准载荷、目标环境 | 执行计划、漂移检查结果、Snapshot、ImportReport |
| R4 | COMMIT | 同一载荷 | 执行记录、ImportReport |
| R5 | POST_VERIFY | Manifest、目标环境 | 部署后验证报告 |
| R6 | FIXTURE_IMPORT | Fixture 包、目标环境 | 执行报告 |
| 7 | HANDOFF | 全部产物与部署证据 | Frontend Development Package、完整性检查报告 |

### 7.3 阶段状态

| 状态 | 含义 |
| --- | --- |
| LOCKED | 前置阶段未完成 |
| READY | 可以执行 |
| RUNNING | Agent、Compiler 或执行器正在工作 |
| WAITING_INPUT | 存在未回答的阻塞 Question |
| WAITING_APPROVAL | 产物已就绪，等待 Gate 审批 |
| PASSED | 阶段完成 |
| FAILED | 执行失败，需要重试或修正 |
| BLOCKED | 存在 BLOCKING 校验问题、策略拒绝或预算超限 |
| INVALIDATED | 曾经通过或进行中，但上游 Blueprint 版本已变更 |
| SKIPPED | 当前交付模式或环境不需要该阶段 |
| MANUAL_CHECK | 操作结果无法由系统确认，等待人工核实 |

允许的状态迁移以表格形式定义（见 S1 SRS 附录 A），其他迁移一律拒绝。

### 7.4 工作流、Run 与项目状态

- 工作流状态由阶段状态按优先级推导：`CANCELLED`、`PAUSED`、`COMPLETED`、`FAILED`（含 MANUAL_CHECK）、`BLOCKED`、`WAITING_USER`、`ACTIVE`。
- Deployment Run 状态：`CREATED`、`IN_PROGRESS`、`WAITING_APPROVAL`、`COMMITTED`、`VERIFIED`、`FAILED`、`CANCELLED`、`MANUAL_CHECK`；另有 `OUTDATED` 标记，表示其所基于的 Blueprint 版本已不是当前版本（历史记录不变）。
- 项目里程碑按当前 Blueprint 版本推导：`DRAFT` → `DESIGN_APPROVED`（ARCHITECTURE_GATE 通过，模式 B 另需 DESIGN_GATE）→ `PACKAGE_APPROVED`（任一 Run 的 PACKAGE_GATE 通过）→ `DHIS2_VALIDATED` → `DEPLOYED` → `VERIFIED` → `HANDED_OFF`。模式 A 以 `VERIFIED` 为终点。
- 项目可被归档（`ARCHIVED`），归档后只读。

### 7.5 状态迁移规则

- **FR-WF-001** 所有状态迁移只能由 Orchestrator 执行，迁移前必须校验前置状态、必需产物、有效审批，并在同一事务中写入审计事件。
- **FR-WF-002** 同一工作流同一时刻只能有一个阶段处于 RUNNING（包括各 Deployment Run 的步骤）。
- **FR-WF-003** Blueprint 产生新版本时，Orchestrator 必须根据变更级别与影响分析，把受影响的阶段置为 INVALIDATED 并使对应审批失效；进行中且未进入 COMMIT 的 Run 取消；已完成的 Run 标记 OUTDATED；Major 变更使 MAPPING 及其后全部阶段失效。
- **FR-WF-004** 用户可以暂停、恢复、取消工作流，或重跑任意已完成阶段；重跑必须产生新的产物版本并丢弃该阶段草稿中的未批准修改。暂停时正在运行的任务继续至完成但不自动推进；正在执行的 COMMIT 不可取消。
- **FR-WF-005** 校验失败或 DHIS2 VALIDATE 失败时，系统必须指出问题来源阶段；用户修正并产生新 Blueprint 版本后，从该阶段重新推进。
- **FR-WF-006** 工作流状态必须持久化；禁止从聊天记录或前端临时状态推断系统状态。
- **FR-WF-010** 用户可为某目标环境创建 Deployment Run，前提：设计主线全部 PASSED 或 SKIPPED，且该环境没有进行中的 Run；Run 绑定当前 Blueprint 版本。
- **FR-WF-011** 长任务与等待人工的流程由持久化工作流引擎承载；进程崩溃后从最后一个持久化步骤继续，重试使用同一幂等键；不可逆操作（COMMIT、Fixture 导入）恢复时必须先查询执行状态，结果无法确认时阶段进入 MANUAL_CHECK。
- **FR-WF-012** 每个 Run 步骤开始前必须检查目标环境 Capability Profile 是否过期或版本已变化；若是，Run 回到 BIND_COMPILE。

### 7.6 变更请求（Change Request）

已批准或已部署的项目发生需求变更时，必须创建 Change Request。Change Request 的状态为：DRAFT → ANALYZED → APPROVED → IN_PROGRESS → COMPLETED，另可 CANCELLED 或 REJECTED；同一项目同一时刻最多一个 IN_PROGRESS 的 Change Request。

- **FR-WF-007** Change Request 记录变更原因、拟影响的 Blueprint 条目和发起人；系统在执行前计算影响范围（受影响阶段、对象、页面与已部署环境），形成影响报告供审批。
- **FR-WF-008** Change Request 从受影响的最早阶段开始重走主线，未受影响的阶段保留原状态；映射冻结后对映射与 Registry 的修改只能通过已批准的 Change Request。
- **FR-WF-009** 对已部署环境的增量变更必须以“当前环境状态 vs 新编译结果”的 Diff 为审批依据。

---

## 8. 需求输入与规范化

所有输入无论格式，都必须先转换为规范化需求表示（Normalized Requirement Representation，NRR）；映射相关 Agent 只消费 NRR，从不直接消费原始文件。

### 8.1 支持的输入

| 类别 | 格式 | 转换方式 | 是否需人工复核 |
| --- | --- | --- | --- |
| 文本图 | Mermaid、PlantUML | 确定性解析器 | 仅复核业务语义 |
| 结构化清单 | Excel、CSV（字段清单、选项清单、组织清单） | 确定性解析器 + 列映射配置 | 复核列映射 |
| 文档 | Markdown、Word、PDF（仅含文本层；MVP 不支持扫描件）、自然语言描述 | 确定性文本提取 + LLM 抽取 | 必须 |
| 图片 | PNG、JPG、截图、UI 草图 | 视觉模型抽取 | 必须 |
| 绘图工具文件 | draw.io、Visio | 先导出为 XML、SVG 或 PNG，再按对应方式处理 | 按导出格式 |
| 既有 DHIS2 元数据 | 元数据导出 JSON、环境发现结果 | 确定性解析器 | 复核复用意图 |
| 既有前端代码 / API 文档 | 源代码、OpenAPI | LLM 辅助抽取 | 必须 |

- **FR-REQ-001** 每个输入必须作为 `BUSINESS_INPUT` 产物保存，并记录 `sourceType`、`sourceUri`、`contentHash`、`uploadedBy`、`createdAt`、`dataClassification`（上传时由用户声明，PHI/PII 需显式声明）；解析器类型与版本记录在解析结果中。Excel / CSV 未提供列映射时，系统给出建议映射并以 Question 请用户确认后再解析。
- **FR-REQ-002** 每个输入在进入任何 LLM 前，必须完成数据分级（见第 20 章），并由 Policy Engine 判定是否可以发送给所选模型。
- **FR-REQ-003** 确定性解析器能处理的格式禁止交给 LLM 解析。
- **FR-REQ-004** 上传时必须进行文件类型校验与大小上限检查（见 NFR-CAP-003）；恶意内容扫描为本期交付项。

### 8.2 规范化需求表示（NRR）

NRR 是有 JSON Schema 的结构化文档，每条记录都必须带有来源引用（输入产物 ID + 位置）与抽取置信度。NRR 至少包含：

- 业务目标（Business Goals）；
- 参与者与角色（Actors）；
- 业务实体与属性（Entities）；
- Use Case；
- 业务流程与状态（Workflows / States）；
- 业务规则（Business Rules）；
- 数据需求（Data Requirements）；
- 组织需求（Organization Requirements）；
- 权限与安全需求（Access Requirements）；
- 报表与分析需求（Reporting Requirements）；
- 通知需求（Notification Requirements）；
- 多语言需求（Language Requirements）；
- 未决问题（Unknowns / Questions）。

- **FR-REQ-005** 来自视觉模型或 LLM 抽取、置信度低于项目阈值的条目，必须以 Question 形式呈现，不得直接进入 NRR 的已确认部分。
- **FR-REQ-006** 多个输入对同一事实给出冲突描述时，系统必须生成冲突 Question，列出各来源的位置。
- **FR-REQ-007** 存在 HIGH 级别未决问题时，REQUIREMENT_GATE 禁止通过；该规则同样适用于其他设计阶段的 Gate。

### 8.3 可测试性

- **FR-REQ-008** 每个确定性解析器必须有“固定输入 → 固定 NRR 片段”的回归测试。
- **FR-REQ-009** LLM 抽取必须有评测集（输入 + 期望 NRR），用于提示词与模型变更前后的对比。

---

## 9. 领域建模、环境发现与 DHIS2 映射

领域模型描述业务本身，映射模型描述业务如何落到 DHIS2；两者分开建模、分开审批，映射只能基于已通过 DOMAIN_GATE 的领域模型进行。

### 9.1 领域对象

每个领域实体至少包含：`businessKey`、显示名、描述、生命周期（状态与迁移）、属性（名称、类型、必填、取值范围、敏感度）、关系、所属 Use Case、来源引用。

每个关系必须显式标注：基数、归属（ownership）、生命周期依赖、可选性、是否跨业务流程、候选 DHIS2 表达。

- **FR-DOM-001** 属性的敏感度（例如个人身份信息、健康信息）必须在领域模型中标注，并传递到映射与 Fixture 生成，用于 Sharing 设计、数据分级与合成数据生成。
- **FR-DOM-002** 系统禁止基于未通过 DOMAIN_GATE 的领域模型生成映射或元数据。

### 9.2 环境发现（Discovery）

- **FR-DSC-001** Brownfield 项目必须在映射前对设计基准环境执行元数据发现，产出“既有元数据索引”，包含对象类型、UID、code、name、lastUpdated、内容哈希（排除 lastUpdated、href、access 等易变字段后计算）、关键引用关系，以及范围内对象的当前完整内容。
- **FR-DSC-002** 发现范围必须可配置（按对象类型、按 code 前缀、按 Program、按组织层级深度，或一组明确的 UID / code），避免对大型实例进行全量拉取。
- **FR-DSC-003** 映射阶段必须对每个候选新建对象检查既有元数据索引中的相似对象（code 精确匹配、name 规范化后的编辑距离、选项集的 code 集合 Jaccard 相似度，阈值可配置），并以 Suggestion 形式提示复用。
- **FR-DSC-004** 发现结果是带时间戳的产物；在 DHIS2_VALIDATE 前，系统必须重新检查 REUSE、REFERENCE、UPDATE 条目是否发生漂移。
- **FR-DSC-005** 发现操作只能使用只读权限，禁止对目标环境产生任何写入。

### 9.3 映射规则

Mapping Agent 按以下设计检查规则提出候选方案。这些规则用于生成与评估候选，而不是替用户做出业务结论。

1. 可长期识别且有生命周期的业务主体，评估 TrackedEntityType；
2. 有明确登记与结束周期的业务过程，评估 Tracker Program 与 Enrollment；
3. 在某个时间点发生的业务事实，评估 Program Stage 或 Event Program；
4. 按组织和周期汇总的数据，评估 DataSet 与聚合 DataElement（Tier 2）；
5. 简单属性，评估 TrackedEntityAttribute 或 DataElement；
6. 枚举值，评估 OptionSet；
7. 分类维度，评估 Category 与 CategoryCombo；
8. 条件显示、校验与计算，评估 ProgramRule 与 ProgramIndicator；
9. 实体之间的关系，评估 RelationshipType；
10. 无法以标准对象表达的查询或配置，最后才评估 SQL View 或 DataStore。

### 9.4 映射决策记录

- **FR-MAP-001** 每个存在多种合理表达的领域元素，都必须生成一条映射决策，包含：决策 ID、领域元素、选定表达、至少一个备选方案、取舍理由、风险、置信度、影响的下游产物。
- **FR-MAP-002** 映射阶段必须为每个 Registry 条目确定 Disposition；UPDATE 必须列出受控字段并说明修改既有对象的影响范围。
- **FR-MAP-003** 置信度低于阈值或影响级别为 HIGH 的决策，必须以 Question 形式交由用户确认，禁止由 Agent 自行采纳；决策中必须记录确认记录（Question 与回答人），缺失时冻结草稿报告 BLOCKING。
- **FR-MAP-004** ARCHITECTURE_GATE 通过后，Mapping Model 与 Registry 随 Blueprint 版本一起冻结；此后下游阶段发现冲突时，只能提出 Question 或 Change Request，禁止修改映射（环境绑定的人工设置除外，见 FR-REG-009）。

---

## 10. 页面、API Intent 与 UI Intent

本章仅适用于模式 B。自定义应用直接调用 DHIS2 Web API，不存在自建业务后端；因此 API Intent 描述的是“某页面需要调用的 DHIS2 API 子集及其参数与返回字段”，由 OpenAPI Compiler 编译为 OpenAPI 文档。

### 10.1 页面模型

```json
{
  "pageKey": "patient-detail",
  "name": "Patient Detail",
  "purpose": "查看并编辑孕妇档案与随访记录",
  "routes": ["/patients/:trackedEntity"],
  "useCases": ["UC-PATIENT-DETAIL"],
  "actors": ["midwife"],
  "apiIntentRefs": ["api-patient-detail"],
  "uiIntentRef": "ui-patient-detail",
  "registryRefs": ["pregnancy", "pregnancy.delivery", "person"],
  "navigation": { "from": ["patient-search"], "to": ["visit-form"] }
}
```

- **FR-PG-001** 每个页面必须关联至少一个 Use Case；每个参与该 Use Case 的参与者必须映射到 DHIS2 权限模型中的角色或用户组。
- **FR-PG-002** 页面引用的所有 DHIS2 对象必须通过 `registryRefs` 解析，禁止在页面、API Intent 或 UI Intent 中出现未登记的 UID。

### 10.2 API Intent

API Intent 是结构化数据，由 API Agent 按页面产出，经 DESIGN_GATE 批准后进入 Blueprint。每个 API 调用至少描述：

| 字段 | 说明 |
| --- | --- |
| operationKey | 页面内唯一的操作标识 |
| purpose | 该调用在页面中的用途 |
| resource | DHIS2 资源逻辑名（由 Capability Profile 解析为具体端点） |
| method | HTTP 方法 |
| pathParams / queryParams | 参数及其来源（路由、用户输入、上一调用结果、Registry 常量） |
| fields | 返回字段选择（对应 DHIS2 `fields` 参数） |
| filters / order / paging | 过滤、排序与分页设置 |
| requestBodyShape | 写操作的载荷结构（引用 Registry 条目） |
| dependsOn | 调用间依赖与顺序 |
| preconditions / postconditions | 前置与后置条件 |
| errorHandling | 预期错误与页面处理方式 |
| permissions | 调用所需的 DHIS2 权限 |

示例（片段）：

```yaml
operationKey: loadPatient
resource: tracker.trackedEntities.get
method: GET
pathParams:
  trackedEntity: { from: route.trackedEntity }
queryParams:
  program: { registry: pregnancy }
fields: [trackedEntity, attributes[attribute,value], enrollments[enrollment,status,events[event,programStage,occurredAt,dataValues]]]
dependsOn: []
```

- **FR-API-001** `resource` 必须使用系统资源目录中的逻辑名，由当前 Capability Profile 解析为实际端点与参数名；API Intent 本身不写死版本相关的路径。
- **FR-API-002** 对于 Tracker 数据，在全部受支持版本（v2.40 及以上）上，必须解析为新 Tracker API（`/api/tracker` 及其 `trackedEntities`、`enrollments`、`events`、`relationships` 子资源）。旧 Tracker 端点（`/api/trackedEntityInstances`、`/api/enrollments`、`/api/events`、`/api/relationships`）禁止出现在任何新生成的契约中；该规则没有例外审批路径，因为旧端点在 v2.42 被移除。
- **FR-API-003** 校验器必须同时检查参数层面：使用目标版本 Profile 规定的参数与字段命名（v2.41 起例如 `trackedEntity` 而非 `trackedEntityInstance`、`orgUnits` 而非 `ou`、`occurredAt` 而非 `eventDate`、`paging` 而非 `skipPaging`），多 UID 参数使用逗号分隔。
- **FR-API-004** 一次 API Agent 运行只处理一个页面。多个页面共享的数据能力必须抽取为可复用的 Schema 组件，由编译器在 OpenAPI 中以 `$ref` 引用，禁止复制。
- **FR-API-005** 编译出的 OpenAPI 必须包含：以 `baseUrl` 为变量的 `servers` 定义（指向目标实例的 `/api`）；目标版本支持的认证方式的 `securitySchemes`（个人访问令牌 `Authorization: ApiToken <token>`、Basic 认证、App Platform 会话 Cookie）；每个操作的 `x-registry-refs` 扩展字段，列出其依赖的 Registry 条目。

### 10.3 UI Intent

UI Intent 描述页面的结构与行为，由 UI Agent 产出并经 DESIGN_GATE 批准，UI Spec Compiler 将其编译为 UI Specification。每个页面至少包含：

- 路由、页面目的、布局与区域；
- 组件清单：名称、用途、属性（Props）、数据来源（operationKey 或 Registry 条目）、用户事件、校验、可见状态、无障碍说明；
- 页面状态：`INITIAL`、`LOADING`、`READY`、`EMPTY`、`SUBMITTING`、`SUCCESS`、`VALIDATION_ERROR`、`API_ERROR`、`PERMISSION_DENIED`、`NETWORK_ERROR`；
- 交互与导航；
- 字段级映射：每个表单字段对应的 Registry 条目、值类型、选项集、必填与校验规则；
- 需要在前端复现或依赖服务端执行的 ProgramRule 说明；
- 翻译键（i18n keys）；
- 测试场景与页面级验收标准。

- **FR-UII-001** UI Intent 应优先使用 DHIS2 设计系统（`@dhis2/ui`）中的组件名称，便于开发包直接对应到实现。
- **FR-UII-002** 每个组件的数据来源必须能解析到同一页面 API Intent 中的某个 operationKey 或 Registry 条目，否则判定为跨产物不一致。

---

## 11. Agent 运行时

Agent 是按阶段划分的受限推理单元：输入是显式构造的上下文，输出是通过 Schema 校验的结构化决策、Blueprint Patch 与 Question，永远不是最终产物文件。

### 11.1 Agent 清单

| Agent | 所属阶段 | 输出 | 禁止 |
| --- | --- | --- | --- |
| Requirements Agent | REQUIREMENTS | NRR 条目、冲突与未决 Question | 做 DHIS2 映射决策 |
| Domain Agent | DOMAIN | 领域对象、关系、Use Case 的 Patch | 引用 DHIS2 对象类型 |
| Mapping Agent | MAPPING | 映射决策、Registry 条目与 Disposition、架构决策记录 | 生成 UID；生成元数据 JSON |
| API Agent | PAGE_DESIGN | 单页面 API Intent | 修改映射或 Registry；处理其他页面 |
| UI Agent | PAGE_DESIGN | 单页面 UI Intent | 引入 API Intent 中不存在的数据来源 |
| Metadata Design Agent | MAPPING；校验问题解释 | 规则表达式草稿、引用解析建议、校验与导入问题的解释与修复建议 | 输出元数据 JSON；绕过校验；映射冻结后产生 Patch |
| Fixture Agent | MAPPING（模式 A）、PAGE_DESIGN（模式 B） | 合成数据规格（分布、数量、边界情况） | 使用真实个人数据 |
| Review Assistant（本期） | 任意阶段 | 对当前产物的解释与问答 | 修改任何产物或状态 |

UID 由 Blueprint Service 分配；元数据 JSON、OpenAPI、UI Specification 由 Compiler 生成；任何 Agent 都不承担这两项职责。Agent 的禁止项由 Agent Runtime 在输出校验时强制检查，违反时视同 Schema 校验失败。

### 11.2 输入契约

每次 Agent 运行必须显式声明并记录：系统提示词版本与任务提示词版本；模型提供方与模型名称；输入产物 ID 与哈希；Blueprint 版本；当前 Capability Profile（如适用）；本次调用经 Policy Engine 判定的数据分级许可。

上下文按以下顺序显式构造：系统规则 → 阶段规则 → 已批准的相关 Blueprint 条目 → 当前输入产物 → 相关 Registry 条目 → 当前未决 Question。

- **FR-AG-001** 上下文加载器必须按页面、businessKey、决策 ID、Registry 条目进行最小化检索，禁止把整个项目所有文件放入上下文。
- **FR-AG-002** Agent 判断架构事实时，必须以结构化条目（映射决策、Registry 条目、审批记录）为依据，自然语言摘要只用于解释。

### 11.3 输出契约

Agent 输出必须是符合其任务输出 Schema 的 JSON，顶层结构为：

```json
{
  "summary": "可审计的决策摘要",
  "decisions": [],
  "patch": { "baseVersion": "1.4.0", "operations": [] },
  "questions": [
    {
      "id": "Q1",
      "text": "分娩记录是否纳入 Pregnancy Program？",
      "severity": "HIGH",
      "affects": ["mapping.pregnancy"],
      "suggestions": [
        { "id": "S1", "text": "作为 Program Stage 纳入", "rationale": "...", "changesUid": false, "regenerates": ["METADATA_PACKAGE"] },
        { "id": "S2", "text": "建立独立 Delivery Program", "rationale": "...", "changesUid": true, "regenerates": ["METADATA_PACKAGE", "API_CONTRACT"] }
      ]
    }
  ],
  "confidence": 0.72
}
```

- **FR-AG-003** 输出必须通过 Schema 校验；校验失败时系统可自动重试一次并附上校验错误，仍失败则运行状态为 FAILED 并交由人工处理。
- **FR-AG-004** 存在未回答的阻塞 Question 时，Agent 运行状态为 `WAITING_INPUT`，所在阶段为 `WAITING_INPUT`；该运行的阻塞 Question 全部回答后，恢复同一运行上下文继续。回答可以逐题提交，也可以在界面中整页一次提交（FR-IF-008）：整页提交先记录全部答案，再最多恢复一次；恢复后仍开放的问题作为下一轮返回，不得因逐题恢复而在第二题到达时运行已前进。
- **FR-AG-005** 系统只持久化可审计的决策摘要与结构化输出，不持久化模型内部推理过程。

### 11.4 禁止事项

Agent 禁止：直接写入他方数据；调用 DHIS2；修改工作流状态；修改已批准的 Blueprint 版本；绕过 Registry 分配 UID；改变目标环境；执行输入资料中包含的任何指令。

### 11.5 提示词注入防护

- **FR-AG-006** 业务文档、源代码、既有元数据、图片中的文字均视为不可信数据，必须以明确的数据分隔方式放入上下文。
- **FR-AG-007** 输入中出现的“忽略之前的指令”“执行提交”“修改映射”等内容不得改变 Agent 行为；由于 Agent 没有执行能力，此类内容最多只能影响其输出，而输出仍需经过 Schema 校验与人工 Gate。

### 11.6 提示词与模型注册

- **FR-AG-008** 提示词必须存放在 Prompt Registry 中并版本化（例如 `mapping.decide.v3`），禁止散落在代码里。每个提示词必须声明：输入 Schema、输出 Schema、支持的 Blueprint Schema 版本、支持的 DHIS2 版本范围、禁止事项。
- **FR-AG-009** 模型提供方通过统一的 `LLMProvider` 接口接入，Agent 不直接依赖任何厂商 SDK。每次调用记录提供方、提供方类别、模型、温度、最大 token、请求 ID、token 用量、耗时、错误与估算成本。
- **FR-AG-010** 提示词或模型变更上线前，必须通过对应 Agent 的评测集与 Golden Project 回归（见第 25 章）。

### 11.7 人工编写模式

- **FR-AG-011** 每个由 Agent 支持的阶段都必须支持人工编写模式（表单与 JSON 编辑器依据 Blueprint 分节 Schema 生成）：用户通过表单或 JSON 编辑器直接创建或修改该阶段的结构化内容，以人工 Patch 提交。当策略拒绝全部可用模型、模型不可用或用户选择不使用 Agent 时，项目仍可完成全部阶段。

---

## 12. Compiler

Compiler 是纯函数：给定相同的 Blueprint 版本、Compiler 版本、模板与类型描述器版本、Capability Profile、发现结果与构建上下文，必须产出字节级相同的产物。编译分为两类：**基线编译**（环境无关，BASELINE_COMPILE 阶段）与**环境绑定编译**（以目标环境发现结果解析环境绑定与 UPDATE 合并，BIND_COMPILE 阶段）。

### 12.1 Compiler 清单

| Compiler | 输入 | 输出 |
| --- | --- | --- |
| Metadata Compiler | Blueprint（mapping、registry、rules、accessModel）+ Capability Profile + 发现结果 | 元数据包（单一逻辑导入载荷）、依赖图、变更摘要、来源追溯 |
| DataStore Compiler | Blueprint 中的应用配置条目 | DataStore 变更集（namespace、key、value） |
| OpenAPI Compiler | pages、apiIntents、registry + Capability Profile | OpenAPI 3.1 文档、API 参考 Markdown |
| UI Spec Compiler | pages、uiIntents、apiIntents、registry | UI Specification JSON 与页面 Markdown |
| Fixture Compiler | fixtures 规格、registry | 合成 Tracker 数据载荷 |
| Package Compiler | 以上全部 + 部署证据（执行报告、部署后验证报告） | Frontend Development Package、Manifest |

### 12.2 确定性要求

- **FR-CMP-001** Compiler 禁止调用 LLM、禁止读取当前时间或随机数；产物中需要的时间戳与随机种子来自输入的构建上下文。
- **FR-CMP-002** 对象排序、字段顺序、文件路径、命名与引用形式必须稳定。系统使用两种确定性 JSON 序列化：规范化形式（键排序、紧凑、UTF-8）用于对数据结构计算哈希；文件形式（键排序、2 空格缩进、UTF-8、LF、末尾换行）用于写入 JSON 文件；产物的内容哈希为其文件字节的哈希。
- **FR-CMP-003** 每个产物必须附带构建记录：Blueprint 版本与哈希、Compiler 版本、模板与类型描述器版本、Capability Profile 与发现结果（产物 ID 与哈希）、构建上下文、输出哈希。
- **FR-CMP-004** 系统必须提供复现功能：以同一构建记录重新编译，输出哈希必须一致，否则视为缺陷。

### 12.3 元数据包

- **FR-CMP-005** Metadata Compiler 必须输出一个完整的逻辑导入载荷，包含本次全部 CREATE、UPDATE 对象；禁止把元数据拆成需要分别校验、分别导入的多个文件。可以同时输出按对象类型拆分的只读视图，但执行只使用完整载荷。
- **FR-CMP-006** DHIS2 载荷中禁止出现系统自有的控制字段（例如 `_comment`、`_import_order`、`workflowId`、哈希）；这些信息全部放入 `metadata_manifest.json`。
- **FR-CMP-007** UPDATE 对象只变更受控字段；编译器必须基于发现得到的当前对象内容合并生成完整对象，防止导入时覆盖非受控字段。
- **FR-CMP-008** 编译器必须输出变更摘要：每种对象类型的新建、更新、不变数量，以及对 Managed Scope 之外对象的引用清单。
- **FR-CMP-009** 删除不属于默认编译结果。只有在已批准的 Change Request 中显式列出、并通过删除安全检查（依赖图中无引用、目标环境无关联数据）的 DEPRECATED 对象，才会进入单独的删除候选集。

### 12.4 元数据包结构

```text
metadata-package/
├── metadata.json              完整逻辑导入载荷（执行使用）
├── metadata_manifest.json     构建记录、对象清单、哈希、Disposition、来源追溯、该环境的 Managed Scope、
│                              观测基准、UID ↔ businessKey 映射、受控字段、随环境变化的对象、表达式校验安排、删除候选
├── dependency-graph.json      依赖图
├── change-summary.json        变更摘要
├── datastore-changes.json     DataStore 变更集（如有）
├── views/                     按对象类型拆分的只读视图
└── README.md                  人类可读说明
```

### 12.5 表达式处理

- **FR-CMP-010** ProgramRule 条件、ProgramIndicator 表达式、ValidationRule 表达式由 Metadata Design Agent 起草或由用户编写，经人工确认后写入 Blueprint；表达式中以 businessKey 引用对象，编译器只负责把引用替换为 UID。
- **FR-CMP-011** 表达式校验分两部分：静态校验阶段以基于 DHIS2 官方表达式语法的本地解析器做语法与引用校验（强制，失败为 BLOCKING）；服务端校验只对引用对象已全部存在于目标环境的表达式在 DHIS2_VALIDATE 阶段通过实例的表达式校验接口执行，其余表达式在部署后验证阶段执行，失败只报告、不修正。Manifest 标明每个表达式的校验安排。

---

## 13. 校验体系

校验分为四层：Blueprint 校验、产物静态校验、跨产物一致性校验、DHIS2 服务端校验。前三层在本地确定性执行，第四层由目标实例执行。校验器只报告问题，不自动修复产物。

### 13.1 问题分级

| 级别 | 含义 | 对流程的影响 |
| --- | --- | --- |
| BLOCKING | 必然导致错误或违反强制规则 | 阶段进入 BLOCKED，禁止进入下一阶段 |
| WARNING | 可能导致问题或偏离最佳实践 | 必须在 Gate 中逐条标记“接受”或“需修正” |
| INFO | 提示信息 | 不影响流程 |

每个问题必须包含：规则 ID、级别、对象（businessKey 与 UID）、产物与位置、说明、来源阶段、建议处理方式。

### 13.2 Blueprint 校验

- Blueprint 符合 JSON Schema；
- businessKey 唯一且引用可解析；
- 每个 Registry 条目具有合法 Disposition，UPDATE 条目声明受控字段；
- UID 格式合法（11 位、首字符字母、仅字母数字）且在 Registry 内唯一；
- code 在项目内同类型对象中唯一；
- 每个领域元素都有映射，或显式标记为不映射。

### 13.3 产物静态校验

- **身份**：载荷中的每个 UID 与 Registry 一致；无未登记对象；无 UID 漂移。
- **引用**：每个引用都可解析，目标对象类型正确（例如 ProgramStage 指向 Program，DataElement 指向 CategoryCombo，属性指向 OptionSet）；REUSE、REFERENCE 目标存在于最近一次发现结果中。
- **Schema**：必填字段、类型、枚举、嵌套结构、禁止字段，依据目标版本的 Schema（由 Capability Profile 提供）。
- **架构**：载荷与已批准 Blueprint 版本哈希一致；不存在未经映射决策的对象；Tracker 与 Event 结构与映射一致。
- **范围**：载荷不修改 Managed Scope 之外的对象；UPDATE 只包含受控字段变更。
- **多语言**：翻译语言覆盖情况（缺失为 WARNING）。
- **表达式**：本地语法与引用校验（见 FR-CMP-011）。
- **所有权**：同一环境同一 UID 不得被两个项目托管；UID 与 code 在目标环境中无冲突。
- **OpenAPI**：文档通过 OpenAPI 3.1 校验；端点与参数符合当前 Capability Profile；不含已弃用或已移除的端点与参数名。

### 13.4 跨产物一致性校验

| 维度 | 检查内容 |
| --- | --- |
| 需求 ↔ 领域 | 每个已确认需求都被至少一个领域元素或 Use Case 覆盖 |
| 领域 ↔ 映射 | 每个领域元素有映射结论 |
| 映射 ↔ Registry | 每个映射落到 Registry 条目 |
| Registry ↔ 元数据包 | 每个 CREATE、UPDATE 条目出现在载荷中，且无额外对象 |
| 页面 ↔ API Intent | 每个页面的数据需求都有 operationKey 支撑 |
| API Intent ↔ UI Intent | 每个组件的数据来源可解析 |
| API Intent ↔ Registry | 所有参数、字段引用的对象在 Registry 中处于已批准状态 |
| 页面 ↔ 权限模型 | 页面参与者对应的角色具备其全部 operationKey 所需权限 |
| Fixture ↔ 元数据 | 合成数据只引用载荷或既有环境中存在的对象，值符合值类型与选项集 |
| Capability ↔ 执行计划 | 执行计划中的每个操作被目标环境支持，且服务账户具备权限（由执行层在生成执行计划时检查） |

示例：“UI 需要患者搜索，但 API Intent 中不存在对应 operationKey”→ BLOCKING；“API Intent 引用的 Program 在 Registry 中处于 PROPOSED 状态”→ BLOCKING；“载荷新建的 ProgramStage 引用的 Program 不在 Managed Scope 内且未登记为 REFERENCE”→ BLOCKING。

### 13.5 DHIS2 服务端校验

- **FR-VAL-001** 服务端校验必须把与 COMMIT 完全相同的载荷以 `importMode=VALIDATE` 提交，并使用与 COMMIT 相同的导入参数（由 Capability Profile 定义，见 FR-EXE-005）。
- **FR-VAL-002** DHIS2 返回的 ImportReport 必须完整保存为产物，并标准化为统一问题格式，映射回 businessKey 与来源阶段。
- **FR-VAL-003** 服务端校验失败时，禁止任何形式的自动提交；阶段进入 FAILED，并提示问题来源阶段。
- **FR-VAL-004** 服务端校验结果只对“该载荷哈希 + 该环境 + 该时刻的环境状态”有效。DEPLOY_GATE 审批时与 COMMIT 请求时都必须执行漂移检查；检测到漂移时 DEPLOY_GATE 失效，必须重新执行服务端校验。

---

## 14. DHIS2 环境、版本与能力 Profile

系统的兼容范围为 v2.40～v2.43，v2.41 是 MVP 首个提供的 Profile，而不是产品锁定版本；所有与版本相关的行为都封装在版本能力 Profile 中，支持新版本只需新增 Profile。

### 14.1 Environment Profile

```json
{
  "environmentKey": "TEST-01",
  "baseUrl": "https://dhis-test.example.org",
  "environmentClass": "TEST",
  "versionConstraint": "2.41.x",
  "authMode": "PAT",
  "secretRefs": {
    "discovery": "vault://dhis2/test-01/readonly",
    "import": "vault://dhis2/test-01/import"
  },
  "tlsRequired": true,
  "llmDataPolicy": "PRIVATE_NETWORK",
  "timeoutSeconds": 60
}
```

- **FR-ENV-001** 环境类别（environmentClass）取值为 `DEV`、`TEST`、`UAT`、`PRODUCTION`；PRODUCTION 类环境必须额外配置：允许提交时间窗（按环境时区的“星期 + 时间段”列表）、删除策略、紧急锁定开关（开启时拒绝一切写类执行）；所需审批人数与操作员角色为本期交付。环境是全局对象，可被多个项目登记为目标环境；项目的 Managed Scope 由 Registry 决定，不在环境上配置（见 5.5.1）。项目的有效 LLM 数据策略取组织策略与项目全部目标环境策略中最严格者。
- **FR-ENV-002** Environment Profile 中禁止存放凭证本身，只能存放 Secret 引用。
- **FR-ENV-003** 系统禁止存在默认环境；每个 DHIS2 操作的请求都必须携带 environmentKey。

### 14.2 连接测试

连接测试只能使用只读请求，必须检查：地址可达；TLS 有效；认证成功；通过 `/api/system/info` 识别版本；版本满足约束且存在对应版本 Profile；元数据读取、元数据导入、Tracker 读写（如项目需要）、DataStore 读写（如项目需要）、应用上传（如项目需要）的权限；服务账户的用户角色与组织范围（只读检查）。

### 14.3 版本能力 Profile

| 组成 | 内容 |
| --- | --- |
| 资源目录 | 逻辑资源名 → 实际端点、方法、参数名、分页方式 |
| Schema 包 | 该版本的元数据对象 Schema（可从实例 `/api/schemas` 同步并版本化） |
| 导入参数 | 元数据导入与 Tracker 导入支持的参数与默认值、异步任务查询方式 |
| 弃用与移除清单 | 已弃用、已移除的端点、参数、字段 |
| 类型描述器覆盖 | 某些对象类型在该版本中的字段差异 |
| 校验规则集 | 该版本特有的校验规则 |
| 表达式语法版本 | 供本地表达式解析器选择语法 |

- **FR-CAP-001** MVP 必须提供 `Dhis241Profile`；M4 提供 `Dhis240Profile`、`Dhis242Profile`、`Dhis243Profile`。新增 Profile 不得修改 Compiler、Validator、Executor 的核心代码。连接的实例版本不在已提供 Profile 的范围内时，连接测试必须报告“版本不受支持”，且该环境不得用于部署。
- **FR-CAP-002** 连接目标环境后，系统必须把“版本 Profile + 实例实际探测结果”合成为该环境的 Capability Profile，并作为带版本的产物持久化；Agent 与 Compiler 只读取已持久化的 Capability Profile，禁止运行时猜测。
- **FR-CAP-003** 当目标实例版本与项目当前使用的 Profile 不同时，系统必须以目标环境的 Profile 重新编译并重新校验，差异在 Diff 中展示。
- **FR-CAP-004** 每个 Profile 都必须有回归测试：用 Golden Project 在对应版本的 DHIS2 官方在线演示实例上完成 VALIDATE 与 COMMIT（实例清单与测试规则见 25.4）。不使用容器化 DHIS2 实例。
- **FR-CAP-005** 目标实例升级（版本变化）后，系统必须把该环境的 Capability Profile 标记为过期，下一次操作前强制重新探测。

### 14.4 环境晋级（Promotion）

- **FR-ENV-004** 同一 Blueprint 版本可以依次部署到 DEV、TEST、UAT、PRODUCTION。与环境无关的部分（对象定义、UID、规则）在各环境之间保持一致。
- **FR-ENV-005** 与环境相关的引用（OrgUnit、既有用户组、default CategoryCombo 等 REFERENCE 条目）通过每个环境的发现结果解析，编译产物因此按环境区分；Manifest 必须标明哪些对象随环境变化。
- **FR-ENV-006** 向 PRODUCTION 类环境部署前，同一 Blueprint 版本必须已在至少一个非生产环境完成 COMMIT 与部署后验证（Run 为 VERIFIED）；平台管理员对具体项目的豁免为本期交付，豁免必须记录审计。
- **FR-ENV-007** 项目登记目标环境时必须指定晋级顺序与唯一的设计基准环境，且至少包含一个非 PRODUCTION 类环境；DISCOVERY 通过后更换设计基准环境，将使 DISCOVERY 及其后设计阶段失效。

---

## 15. 执行管线、Snapshot 与 Diff

执行服务把已批准的产物提交到指定环境；不同类型的 DHIS2 操作使用不同的执行器，互不假装处于同一事务。

### 15.1 执行器

| 执行器 | DHIS2 接口 | 是否支持服务端 VALIDATE | 用途 | 里程碑 |
| --- | --- | --- | --- | --- |
| Metadata Executor | 元数据导入 API | 是（`importMode=VALIDATE`） | 元数据包的校验与提交 | M2 |
| Tracker Data Executor | 新 Tracker 导入 API | 是（`importMode=VALIDATE`） | 合成 Fixture 数据导入 | M3 |
| DataStore Executor | DataStore API | 否；使用系统自定义校验与读后比对 | 应用配置条目 | M5 |
| App Upload Executor | 应用管理 API | 否 | 上传或更新自定义应用包 | M5 |

- **FR-EXE-001** 每个执行器都必须实现统一接口：`plan`、`validate`（如支持）、`commit`、`verify`，并返回标准化执行结果。
- **FR-EXE-002** 一次部署如包含多个执行器，执行顺序固定为 Metadata → DataStore → Tracker Data → App Upload；前一步失败时后续步骤不得执行，执行报告必须说明哪些步骤已生效。
- **FR-EXE-003** Tracker Data Executor 默认只允许合成数据，且禁止对 PRODUCTION 类环境执行。在 Deployment Run 中，部署后验证位于元数据提交之后、Fixture 导入之前。

### 15.2 标准执行流程

```text
PLAN          生成执行计划：环境、执行器、载荷哈希、导入参数、预期变更、维护动作、表达式校验安排
  ↓
DRIFT CHECK   检查 REUSE / REFERENCE / UPDATE 对象自发现以来是否变化
  ↓
SNAPSHOT      导出 Managed Scope 与受影响对象的当前状态
  ↓
VALIDATE      以相同参数提交相同载荷，importMode=VALIDATE
  ↓
DEPLOY_GATE   人工审批（绑定载荷哈希、环境、Snapshot、校验结果、执行计划；审批时再做漂移检查）
  ↓
COMMIT        用户发起提交 → 后端复核 → 获取环境提交锁 → 漂移检查 → 提交同一载荷，importMode=COMMIT
  ↓
POST_VERIFY   读取目标环境，与 Registry 和载荷逐对象比对
  ↓
MAINTENANCE   （可选）执行已在 DEPLOY_GATE 中列明的维护动作
  ↓
REPORT        生成执行报告
```

- **FR-EXE-004** COMMIT 使用的载荷哈希必须与 VALIDATE 和 DEPLOY_GATE 审批时的哈希完全一致，否则后端拒绝执行；执行层对调用方给出的期望哈希再做一次校验。DEPLOY_GATE 批准后不自动提交，COMMIT 由用户显式发起。
- **FR-EXE-005** 元数据导入参数由 Capability Profile 定义，默认值为：以 UID 作为标识符、创建并更新策略、全部成功或全部失败的原子模式。项目选择其他参数（如非原子模式）必须在 DEPLOY_GATE 中显式确认。
- **FR-EXE-006** COMMIT 请求必须在后端重新执行完整 Gate 检查，不能因为客户端调用了接口就执行。
- **FR-EXE-007** 提交后的维护动作默认关闭；如项目需要，必须在执行计划中列出并随 DEPLOY_GATE 一并审批。MVP 支持的维护动作仅有“清理应用缓存”。

### 15.3 异步任务

- **FR-EXE-008** 载荷对象数超过阈值（默认 500，可按环境配置）时使用异步导入。对异步导入，执行服务必须：提交任务；立即持久化任务 ID 与查询地址；轮询任务状态；持久化最终 ImportReport；更新阶段状态。
- **FR-EXE-009** 进程在等待异步任务期间崩溃后恢复时，必须根据已持久化的任务 ID 继续查询结果，禁止重新提交。提交请求发出后既无响应又无任务 ID 时，执行进入 MANUAL_CHECK：系统提供对账（比对当前对象、提交前 Snapshot 与载荷），由人工核实结果。
- **FR-EXE-012** 同一环境同一时刻只允许一个提交类执行（跨项目生效）；执行处于 MANUAL_CHECK 时环境提交锁保持，直至人工核实后释放；锁不因超时自动释放。

### 15.4 Factory Snapshot

Snapshot 是系统自己保存的目标环境状态记录，不是 DHIS2 原生事务快照，也不是数据库备份。

- **FR-SNP-001** Snapshot 必须包含：Managed Scope 内全部对象及本次载荷涉及的全部对象的当前 JSON、每个对象的 UID、`lastUpdated`、内容哈希、Managed Scope 定义、环境 ID、时间戳、DHIS2 版本。
- **FR-SNP-002** Snapshot 用于提交前后对比、审计、漂移检测，以及生成修正性载荷。
- **FR-SNP-003** 对 PRODUCTION 类环境，DEPLOY_GATE 必须包含“实例数据库备份已由运维确认”检查项，由审批人手动确认；系统本身不执行数据库备份。

### 15.5 Diff

系统必须支持以下对比，均以对象为单位展示新建、更新（字段级）、不变与仅引用：

- 目标环境当前状态 vs 本次编译结果；
- 提交前 Snapshot vs 提交后状态；
- Blueprint 版本 N vs N+1；
- Registry 版本 N vs N+1；
- 同一 Blueprint 版本在两个环境中的部署状态。

其中 Blueprint 与 Registry 的版本对比由 Blueprint 与构建子系统提供；环境当前状态与编译结果的对比在环境绑定编译时生成；提交前后对比与两环境对比由执行层提供。

### 15.6 恢复策略

系统禁止承诺把 DHIS2 完整回滚到提交之前。系统提供三种明确区分的恢复策略，每种都需要单独审批：

| 策略 | 做法 | 适用范围与限制 |
| --- | --- | --- |
| 修正性提交 | 修正 Blueprint，重新编译并按正常流程提交 | 首选方式 |
| 回退到上一版本载荷 | 以上一次成功部署的 Blueprint 版本重新编译并提交 | 只能恢复被 UPDATE 的字段；无法撤销已新建对象上产生的数据 |
| 定向删除 | 通过 Change Request 删除本次新建、且无关联数据的对象 | 仅限 Managed Scope 内且通过删除安全检查的对象 |

数据库级恢复不属于系统职责，由实例运维按其备份策略执行。

### 15.7 部署后验证

- **FR-EXE-010** 提交完成后，系统必须从目标环境读回全部 CREATE、UPDATE 对象，逐字段比对受控字段，并检查引用、共享设置与关键权限是否生效。
- **FR-EXE-011** 部署后验证失败时阶段状态为 FAILED，执行报告必须列出差异；系统不自动修正。

---

## 16. Human Gate 与审批

系统共有 7 种 Gate。Gate 在所属阶段（或 Deployment Run 步骤）每次运行结束时创建一次，审批对象是整个阶段的产物集合，而不是逐个文件或逐个对象；这样既保留人工控制，又避免审批疲劳。设计阶段的 Gate 绑定冻结后的 Blueprint 工作草稿哈希，批准时草稿提升为正式版本；PACKAGE_GATE 与 DEPLOY_GATE 属于某个 Deployment Run。

### 16.1 Gate 定义

| Gate | 位置 | 审批对象 | 默认审批角色 | 适用模式 |
| --- | --- | --- | --- | --- |
| REQUIREMENT_GATE | REQUIREMENTS 结束 | 规范化需求与已解决 Question | Solution Architect（可加 Business Analyst） | A、B |
| DOMAIN_GATE | DOMAIN 结束 | 领域模型、Use Case | Solution Architect | A、B |
| ARCHITECTURE_GATE | MAPPING 结束 | 映射模型、Registry、Disposition、架构决策 | Solution Architect | A、B |
| DESIGN_GATE | PAGE_DESIGN 结束 | 全部页面的 API Intent 与 UI Intent | Solution Architect | B |
| PACKAGE_GATE | Run 的 STATIC_VALIDATION 结束 | 该环境的编译产物、变更摘要、Diff、WARNING 清单 | DHIS2 Implementer | A、B |
| DEPLOY_GATE | Run 的 COMMIT 之前 | 载荷哈希、目标环境、Snapshot、服务端校验结果、执行计划、风险摘要、维护动作 | 按环境类别配置（见 16.3） | A、B |
| HANDOFF_GATE | HANDOFF 结束 | 开发包及完整性检查结果 | Solution Architect | B |

### 16.2 Gate 数据

```json
{
  "gateId": "gate-0923",
  "type": "DEPLOY_GATE",
  "workflowId": "wf-0112",
  "environmentKey": "TEST-01",
  "blueprintVersion": "1.4.0",
  "deploymentRunId": "run-0031",
  "boundRefs": [ { "refType": "ARTIFACT", "refId": "art-7781", "contentHash": "sha256:..." } ],
  "openWarnings": [],
  "riskSummary": { "create": 51, "update": 3, "delete": 0, "unmanagedReferences": 4 },
  "requiredApprovals": 1,
  "approvals": [],
  "status": "WAITING"
}
```

Gate 状态：`WAITING`、`APPROVED`、`REJECTED`、`INVALIDATED`。

### 16.3 审批规则

- **FR-GATE-001** 审批必须绑定内容哈希（产物、冻结的草稿或载荷）。任何被绑定对象发生变化，Gate 自动变为 `INVALIDATED`，需要重新审批；审批请求必须携带审批人所见的绑定哈希，与当前值不一致时拒绝。
- **FR-GATE-002** 审批状态只能由后端根据审批操作写入；前端的点击本身不构成审批。
- **FR-GATE-003** 拒绝审批时必须填写理由。审批人必须具备该 Gate 要求的角色；未启用认证时记录审批人的操作者名称。
- **FR-GATE-004** Gate 中全部 WARNING 必须逐条标记为“接受”或“需修正”后，才能批准；存在“需修正”时只能拒绝。
- **FR-GATE-005** DEPLOY_GATE 的审批要求按环境类别配置：

| 环境类别 | 所需审批人数 | 职责分离 | 附加条件 |
| --- | --- | --- | --- |
| DEV | 1（可为执行者本人） | 否 | 无 |
| TEST、UAT | 1 | 否 | 无 |
| PRODUCTION | 至少 2（未启用认证时为 1） | 执行者不得作为审批人之一 | 在允许提交的时间窗内；紧急锁定未开启；同一 Blueprint 版本已在非生产环境验证；审批人确认运维已完成数据库备份（以上条件在 MVP 即执行） |

- **FR-GATE-006** Gate 等待期间全部状态必须持久化；系统重启、用户离线不会丢失或改变 Gate 状态。
- **FR-GATE-007** Gate 被拒绝时，所在阶段回到 READY，拒绝理由作为该阶段的待处理反馈显示；重新提交审批时绑定内容必须与被拒绝时不同。
- **FR-GATE-008** Gate 创建时，系统必须产生通知（站内通知，以及可选的邮件或 Webhook）；等待超过项目配置的时长（默认 24 小时）后发送提醒，最多 3 次。
- **FR-GATE-009** 设计 Gate 批准时，审批记录、草稿提升为新版本、阶段推进、失效传播与审计必须在同一事务中完成，任一失败则整体回滚。

### 16.4 可解释性

每个 Gate 页面必须回答：发现了什么；准备做什么；为什么（关联的决策与理由）；还有什么不确定（未决 WARNING、低置信度决策）；用户可以改变什么；批准后会发生什么、影响哪个环境、产出哪些产物。

---

## 17. Frontend Development Package

开发包是自足式交付物：前端开发者拿到它并获得一个已初始化 DHIS2 环境的访问权限后，无需再向架构师、产品经理或 Agent 询问页面业务含义、API 参数、返回结构、元数据 UID 和联调方式。

### 17.1 目录结构

```text
frontend-package/
├── README.md                  入口：目的、结构、快速开始、已知限制
├── PROJECT-CONTEXT.md         业务背景、参与者、Use Case 摘要
├── ARCHITECTURE.md            DHIS2 映射说明：Program、Stage、属性、关系如何对应业务
├── DEVELOPMENT-GUIDE.md       基于 DHIS2 App Platform 的开发、运行、构建、部署步骤
├── PERMISSIONS.md             角色、用户组、共享设置与各页面所需权限
├── ACCEPTANCE-CRITERIA.md     页面级与端到端验收标准
├── api/
│   ├── openapi.yaml           OpenAPI 3.1 文档
│   ├── api-reference.md       按页面组织的 API 说明
│   └── examples/              每个 operationKey 的请求与响应示例
├── pages/
│   └── <pageKey>.md           页面规格：布局、组件、状态、交互、字段映射、规则
├── ui/
│   └── ui-spec.json           机器可读 UI Specification
├── i18n/
│   └── <locale>.json          翻译键与默认文案
├── metadata/
│   ├── registry.json          businessKey ↔ UID ↔ code 对照（按环境）
│   ├── field-mapping.json     表单字段 ↔ 属性/数据元 ↔ 值类型 ↔ 选项集
│   ├── option-sets.json       选项集及选项
│   ├── program-rules.md       规则说明：条件、动作、前端需注意的行为
│   └── manifest.json          元数据包构建记录与各环境部署状态（来自部署证据）
├── fixtures/
│   ├── demo-data.json         合成演示数据（Tracker 格式）
│   └── demo-users.md          演示账号说明（凭证另行安全分发）
├── dhis2/
│   ├── environment.example.json  环境配置示例（不含凭证）
│   └── debugging.md           联调与排错指南
└── traceability/
    └── source-map.json        页面 → Use Case → Blueprint → Registry → 元数据 的追溯链
```

### 17.2 内容要求

- **FR-FE-001** 开发包由 Package Compiler 从同一个 Blueprint 版本确定性生成；开发包中的每个 UID、字段、参数都必须可追溯到该版本的 Registry 条目。
- **FR-FE-002** `registry.json` 必须按环境给出 UID；对随环境变化的 REFERENCE 条目（如 OrgUnit）必须明确标出。
- **FR-FE-003** README 必须说明：项目目的、目标 DHIS2 版本、应用结构、本地启动、连接 DHIS2 的配置方法、运行测试的方法、页面清单、API 文档入口、元数据初始化状态（环境、部署时间、Blueprint 版本）、演示数据说明、已知限制。
- **FR-FE-004** 开发指南必须基于 DHIS2 官方应用生态：App Platform、App Runtime（数据访问与认证）与 `@dhis2/ui` 组件库。
- **FR-FE-005** 开发包禁止包含任何凭证；演示账号的凭证通过 Secret Manager 或其他安全渠道单独分发。
- **FR-FE-006** `debugging.md` 必须说明本地开发连接远程 DHIS2 实例所需的实例侧配置（例如将本地开发地址加入实例的 CORS 白名单）、支持的认证方式与常见错误的排查方法。

### 17.3 完整性检查

开发包在 HANDOFF_GATE 前必须通过自动化完整性检查：

- [ ] 每个页面都有页面规格、API Intent、UI Intent；
- [ ] 每个 operationKey 都有请求与响应示例；
- [ ] 每个表单字段都有字段映射；
- [ ] 页面依赖的全部元数据在至少一个非生产目标环境中已部署且通过部署后验证（依据部署证据）；
- [ ] OpenAPI 文档通过校验且不含弃用端点；
- [ ] 每个翻译语言的翻译键完整；
- [ ] 合成演示数据已在 DEV 或 TEST 环境导入成功（如项目要求）；
- [ ] 追溯链完整，无悬空引用；
- [ ] 未包含凭证或真实个人数据。

页面只有在全部检查通过后，才能标记为 `FRONTEND_READY`。

### 17.4 前端代码生成（M5）

M5 里程碑可基于 Blueprint、OpenAPI、UI Specification 与 Registry 生成可运行的 DHIS2 Web App 起步工程（使用 App Platform、App Runtime 与 `@dhis2/ui`）。M2 至 M4 不要求该能力，但开发包的数据结构必须足以支撑它。

---

## 18. Factory UI

Factory UI 让用户在任何时刻都能看清：当前项目与阶段、已有产物、已批准的结论、未决的不确定性、需要确认的事项、下一步可执行的动作、动作会影响的 DHIS2 环境，以及最终会得到的交付物。

### 18.1 信息架构

```text
Factory UI
├── Dashboard                 全部项目、待审批 Gate、最近执行、通知
├── Projects
│   ├── 新建项目向导
│   └── Project Workspace
│       ├── Overview          交付模式、项目类型、阶段进度、里程碑、按环境的 Run 状态
│       ├── Requirements      输入产物、解析状态、NRR、Question
│       ├── Domain            领域模型图、Use Case
│       ├── Discovery         既有元数据索引、复用建议、Capability Profile
│       ├── Mapping           映射图、决策记录、Registry
│       ├── Pages             页面、API Intent、UI Intent（模式 B）
│       ├── Build             基线编译产物与环境无关校验结果
│       ├── Deployments       按环境的 Run 列表与部署控制台（环境绑定编译、校验、PACKAGE_GATE、
│       │                     VALIDATE、DEPLOY_GATE、COMMIT、部署后验证、环境绑定人工设置）
│       ├── Handoff           开发包与完整性检查（模式 B）
│       ├── Change Requests
│       ├── Audit
│       └── Project Settings  目标环境、设计基准环境、晋级顺序、通知渠道
├── Environments              环境、连接测试、Capability Profile、紧急锁定、凭证到期
├── Governance                模型、Prompt 版本、数据策略、评测结果、用量与预算
├── Notifications             通知中心
└── Settings                  系统设置（用户与角色为本期交付）
```

MVP 不含登录：界面要求用户填写操作者名称，所有改变状态的请求携带该名称。Factory UI 与后端经同一反向代理同域部署。

### 18.2 工作台线框图

```text
+----------------+---------------------------------------------+-----------------------+
| 阶段导航       | 主工作区                                    | 检查器                |
|                |                                             |                       |
| [✓] 需求       |  上下文：项目 maternal-care | 阶段 MAPPING  | 选中对象：            |
| [✓] 领域       |          Blueprint 1.4.0 | 环境 TEST-01     |   pregnancy.delivery  |
| [✓] 环境发现   | ------------------------------------------- | 类型：programStage    |
| [●] 映射       |  视图切换：[表格] [映射图] [Registry] [Diff]| Disposition：CREATE   |
| [ ] 页面设计   |                                             | UID：pSdLvRy0001      |
| [ ] 基线编译   |  （表单 / 表格 / 图 / Diff / 预览）         | 状态：PROPOSED        |
| Deployments    |                                             | 来源：NRR#E-12        |
|   TEST-01 ●    | ------------------------------------------- | 决策：MAP-017         |
|   PROD-01 ○    |  问题（2） | 建议 | 人工编写                | 依赖：pregnancy       |
|                |                                             | 版本历史              |
| [ ] 交付       |  [提交审批]（可用性由后端 allowed-actions 决定）|                   |
+----------------+---------------------------------------------+-----------------------+
```

### 18.3 部署控制台线框图

```text
+-----------------------------------------------------------------------------+
| 环境：TEST-01（TEST）    DHIS2：2.41.x    Profile：Dhis241Profile           |
| 连接：OK    权限：OK    漂移检查：PASSED    Snapshot：CREATED               |
| Run：run-0031（Blueprint 1.4.0）  静态校验：PASSED  VALIDATE：PASSED  审批：1/1 |
| 新建：51    更新：3    删除：0    Managed Scope 外引用：4                   |
| 维护动作：无                                                                 |
|                                                                             |
| [返回]   [下载元数据包]   [COMMIT]                                          |
+-----------------------------------------------------------------------------+
```

### 18.4 功能要求

| 编号 | 能力 | 要求 |
| --- | --- | --- |
| FR-UI-001 | 阶段导航 | 每个阶段显示：阶段状态（与 7.3 一致）、输入完成度、产物数量、未决 Question 数、BLOCKING 问题数、最近执行时间、当前产物版本 |
| FR-UI-002 | 上下文与阶段内对话 | 界面始终显示上下文（项目、阶段、Blueprint 版本或草稿、目标环境）；阶段内对话（本期）限定在当前阶段，对话产生的修改只能以 Patch 形式进入当前阶段的工作草稿 |
| FR-UI-003 | Question 展示与整页问卷 | 结构化展示问题、影响对象、来源 Agent、严重程度、各建议的理由、是否改变 UID、需重新生成的产物与**推荐标记**；同一阶段的问题组成一份问卷，按 `answerMode` 支持单选（`SINGLE`）或多选（`MULTIPLE`），支持选择建议或自定义回答；**整页一次提交**（`POST /questions/answers`），服务端先记录全部答案再最多恢复一次运行，响应带回仍开放的问题以就地渲染下一轮；阻塞题未答完不得提交 |
| FR-UI-004 | 产物审核 | 每个重要产物提供预览、原始内容、Diff、依赖、来源与决策追溯、版本、审批状态；按媒体类型决定就地预览或下载，非文本内容不得强行按文本渲染 |
| FR-UI-005 | 图审核 | 领域图与映射图支持节点搜索、按对象类型与业务域过滤、高亮引用关系、显示孤立节点与未解析引用、跳转到 Registry 条目 |
| FR-UI-006 | API 审核 | 按页面列出 operationKey、端点、参数、字段、分页与过滤、示例、权限、错误处理，并集成交互式 OpenAPI 参考 |
| FR-UI-007 | 元数据审核 | 提供按文件、按对象类型、按 Registry 三种视图，以及依赖图、当前 vs 编译 Diff、新建/更新/删除统计、校验问题、能力兼容警告 |
| FR-UI-008 | 部署控制台 | 显示 18.3 中的全部信息；COMMIT 前二次确认；网络失败后显示“结果待确认”，不自动重提；运行摘要给出 snapshot/execution 产物槽位与 `contentPurged` 事实 |
| FR-UI-009 | 状态权威 | 所有按钮（包括 COMMIT）的可用性由后端返回的允许动作决定，前端禁止自行判断 |
| FR-UI-010 | 实时更新 | 阶段、Agent、校验、执行状态通过服务端事件实时刷新；断线重连后按事件序号补齐 |
| FR-UI-011 | 人工编写 | 每个 Agent 阶段提供结构化表单与 JSON 编辑器，支持 FR-AG-011；需求条目可在界面中逐条新增、修改与删除，写入当前阶段工作草稿的 `requirements` 段（与解析产出的条目是同一份数据）；阶段关闭后写入口收起，只能走变更请求 |
| FR-UI-012 | 通知中心 | 显示待审批 Gate、被拒绝的提交、执行完成与失败等通知；提供跨项目“待审批”收件箱 |

### 18.5 设计语言与界面语言

- Factory UI 应采用 DHIS2 设计系统（`@dhis2/ui`），使工厂界面与最终 DHIS2 应用的设计语言一致。
- Factory UI 界面语言至少支持简体中文与英文（见 NFR-UX-003）。

---

## 19. 外部接口需求

### 19.1 用户界面

Factory UI 的界面要求见第 18 章；可用性与无障碍要求见 22.6。

### 19.2 Factory 服务 API

Factory UI 通过 REST 发出命令与查询，通过 SSE 接收状态事件。API 以 OpenAPI 3.1 描述，路径前缀为 `/api/v1`，由 S1 Factory 控制平面提供；完整端点清单见 S1 SRS 第 8 章。主要分组如下：

| 分组 | 主要端点 |
| --- | --- |
| 项目 | `POST /projects` · `GET /projects` · `GET/PATCH /projects/{id}` · `POST /projects/{id}/archive` · `DELETE /projects/{id}` · `GET /projects/{id}/export` · `POST /projects/import` · `POST /projects/{id}/upgrade-mode` · `GET/PUT /projects/{id}/target-environments` · `GET/PUT /projects/{id}/settings` |
| 输入产物 | `POST /projects/{id}/inputs` · `GET /projects/{id}/inputs` · `POST /projects/{id}/inputs/{artifactId}/parse` · `GET /projects/{id}/inputs/{artifactId}/column-mapping` · `GET /projects/{id}/inputs/{artifactId}/llm-log[/{exchangeId}]` |
| 工作流 | `GET /projects/{id}/workflow` · `GET /workflows/{id}` · `POST /workflows/{id}/pause` · `/resume` · `/cancel` · `GET /workflows/{id}/allowed-actions` |
| 阶段 | `GET /workflows/{id}/stages` · `GET /stages/{id}` · `POST /stages/{id}/run` · `/rerun` · `/submit-for-approval` · `/resolve-manual-check` · `POST /stages/{id}/patches` · `GET/POST/DELETE /stages/{id}/chat` |
| Deployment Run | `POST /workflows/{id}/deployment-runs` · `GET /workflows/{id}/deployment-runs` · `GET /deployment-runs/{id}` · `GET /deployment-runs/{id}/summary` · `POST /deployment-runs/{id}/commit` · `POST /deployment-runs/{id}/cancel` · `POST /deployment-runs/{id}/drift-check` · `PUT /deployment-runs/{id}/environment-bindings/{businessKey}` |
| 执行 | `GET /executions/{id}` · `GET /executions/{id}/report` · `POST /executions/{id}/reconcile` |
| Question | `GET /workflows/{id}/questions` · `GET /questions/{id}` · `POST /questions/{id}/answer` · `POST /questions/answers`（整页问卷）· `POST /stages/{id}/questions` |
| Gate | `GET /workflows/{id}/gates` · `GET /gates/{id}` · `GET /gates/{id}/explanation` · `POST /gates/{id}/approve` · `POST /gates/{id}/reject` |
| Blueprint 与 Registry | 版本、版本 Diff、分节 Schema、Registry、Managed Scope、UID 池与补池、环境绑定的查询 |
| 产物 | `GET /workflows/{id}/artifacts` · `GET /artifacts/{id}` · `/content` · `/diff?against=` · `/lineage` · `POST /artifacts/{id}/reproduce` · `/explain` · `/purge` · `GET /projects/{id}/package-files` · `GET /projects/{id}/frontend-package/files[/{path}]` |
| 环境 | 环境管理、连接测试、Capability Profile、发现结果、Snapshot、紧急锁定、即将到期凭证、定向删除（`POST /environments/{key}/delete-objects`） |
| 变更请求 | `POST /projects/{id}/change-requests` · `GET /change-requests/{id}` · `/impact` · `/approve` · `/reject` · `/start` · `/complete` · `/cancel` |
| Governance | 提供方目录与登记/差量更新/连通性探测、模型登记/差量编辑、Prompt 版本、数据策略、预算读写、用量、评测 |
| 身份与权限 | `POST /auth/login` · `POST /auth/logout` · `GET /auth/session` · `GET /auth/oidc/request` · `POST /auth/oidc/exchange` · `GET/POST /users` · `PATCH /users/{id}` · `POST /users/{id}/password` · `GET/PUT/DELETE /users/{id}/roles` · `GET /roles` · 项目成员、受限产物授权、生产豁免 |
| 凭证与装机 | `GET/PUT/DELETE /secrets[/{name}]` · `GET /setup` · `GET /upload-limits` |
| 审计 | `GET /projects/{id}/audit` · `GET /audit` · `POST /audit/verify` · `GET /audit/commit-trace/{executionId}` |
| 通知与设置 | `GET /notifications` · `POST /notifications/{id}/read` · `GET/PUT /settings` · `GET /allowed-actions` |
| 事件流 | `GET /workflows/{id}/events`（SSE） |

当前契约为 **1.19.0 / 147 条路径 / 172 个操作**；`GET /healthz`、`GET /readyz` 是不进 OpenAPI 的基础设施探针。

- **FR-IF-001** 所有执行类与创建类请求必须支持 `Idempotency-Key` 请求头；同一键的重复请求返回首次结果，不重复执行；同键不同请求体返回 422；同键请求仍在处理时返回 409（可重试）。`PUT /secrets/{name}` 是唯一例外：它不进回放表，可重复性来自 `PUT` 本身。
- **FR-IF-002** `authMode=off` 时，改变状态的请求必须携带 `X-Operator`；`local|oidc|both` 时身份取自会话，写请求另需 `X-CSRF-Token`。执行类请求必须能确定 environmentKey；后端依据工作流与 Gate 状态判断动作是否合法，非法请求返回 409 与原因。
- **FR-IF-003** `allowed-actions` 返回当前状态下可执行的动作列表及不可用动作的原因码，是 UI 按钮可用性的唯一依据。
- **FR-IF-004** 列表类接口必须支持分页与排序；所有时间使用 ISO 8601 UTC 格式。
- **FR-IF-008** 同一阶段的多道 Question 允许一次提交（`POST /questions/answers`）：先记录全部答案，再最多恢复一次 Agent 运行；阻塞题未答完时答案仍落库但不恢复。

#### 19.2.1 错误模型

```json
{
  "errorCode": "GATE_NOT_APPROVED",
  "message": "DEPLOY_GATE 尚未获得所需审批",
  "stage": "COMMIT",
  "artifactId": "art-7781",
  "retryable": false,
  "correlationId": "c-5f2a..."
}
```

错误类别（子系统可在此基础上登记更细的错误码，见各子系统 SRS）：`LLM_ERROR`、`LLM_TIMEOUT`、`POLICY_DENIED`、`SCHEMA_ERROR`、`ARTIFACT_ERROR`、`REGISTRY_ERROR`、`VALIDATION_ERROR`、`GATE_NOT_APPROVED`、`APPROVAL_INVALIDATED`、`DHIS2_AUTH_ERROR`、`DHIS2_PERMISSION_ERROR`、`DHIS2_NOT_FOUND`、`DHIS2_IMPORT_ERROR`、`DHIS2_TIMEOUT`、`DRIFT_DETECTED`、`CONCURRENCY_ERROR`、`INTERNAL_ERROR`。

#### 19.2.2 重试规则

- 允许自动重试（指数退避、有次数上限）：LLM 超时、HTTP 429、502、503、504、网络中断。
- 禁止自动重试、必须人工处理：Schema 错误、UID 错误、架构冲突、审批拒绝、DHIS2 校验错误、策略拒绝。
- COMMIT 请求在网络失败后，必须先查询该执行的已持久化状态与目标环境实际状态，确认未生效后才允许重新提交。

#### 19.2.3 实时事件

```json
{
  "eventId": "evt-9012",
  "eventType": "stage.waiting_approval",
  "workflowId": "wf-0112",
  "stageId": "stg-0441",
  "occurredAt": "2026-09-27T10:00:00Z",
  "sequence": 42,
  "payload": {}
}
```

事件类型：`workflow.started`、`workflow.paused`、`workflow.resumed`、`workflow.completed`、`workflow.failed`、`stage.started`、`stage.waiting_input`、`stage.waiting_approval`、`stage.passed`、`stage.failed`、`stage.blocked`、`stage.invalidated`、`agent.started`、`agent.completed`、`question.created`、`question.answered`、`gate.created`、`gate.approved`、`gate.rejected`、`gate.invalidated`、`artifact.created`、`artifact.superseded`、`validation.completed`、`execution.started`、`execution.progress`、`execution.completed`、`execution.failed`、`agent.progress`、`stage.manual_check`、`deployment_run.created`、`deployment_run.completed`、`deployment_run.failed`、`deployment_run.cancelled`、`deployment_run.outdated`、`change_request.updated`、`stream.reset`。

- **FR-IF-005** `sequence` 在工作流内单调递增且与提交顺序一致；客户端重连时携带最后收到的序号，服务端补发缺失事件；请求的序号早于保留期（默认 7 天）时发送 `stream.reset`，客户端全量刷新。事件只用于通知，状态以 REST 查询结果为准。多实例间的事件扇出使用 PostgreSQL LISTEN/NOTIFY。

### 19.3 DHIS2 Web API

| 用途 | 接口（逻辑名，由 Capability Profile 解析） | 所需权限范围 |
| --- | --- | --- |
| 版本与系统信息 | system.info | 发现 |
| Schema 同步 | schemas | 发现 |
| 元数据读取 | metadata.read、各对象类型资源 | 发现 |
| UID 生成与存在性查询 | system.id、identifiableObjects | 发现 |
| 元数据导入（VALIDATE / COMMIT） | metadata.import | 元数据导入 |
| 异步任务查询 | system.tasks | 元数据导入 |
| 表达式校验 | programRules.condition.validate、programIndicators.expression.validate、validationRules.expression.validate | 发现 |
| Tracker 数据导入与读取 | tracker.import、tracker.trackedEntities、tracker.enrollments、tracker.events、tracker.relationships | Tracker |
| DataStore | dataStore | DataStore |
| 应用管理 | apps | 应用管理 |
| 维护动作 | maintenance | 管理 |

- **FR-IF-006** 所有 DHIS2 请求必须经由执行层的 DHIS2 Client 发出，统一处理认证、超时（默认 60 秒，可按环境配置）、重试、日志脱敏与 correlationId 传递。

### 19.4 模型提供方接口

- **FR-IF-007** `LLMProvider` 接口至少包含：结构化生成（按 JSON Schema 约束输出）、视觉输入（图片）、token 计数、健康检查。提供方适配器必须声明其提供方类别（见 20.2）与能力（是否支持结构化输出、视觉、最大上下文）。默认提供 OpenAI 兼容接口适配器，模型登记项可配置接口地址、API Key（Secret 引用）与模型名称；本地或私有部署的模型经其 OpenAI 兼容接口接入。健康检查通过 S1 的 `POST /governance/providers/{name}/test-connection` 暴露：探测跑完即返回结论（地址错、密钥被拒、超时是可区分的答案，不是 5xx），不生成内容、不计费、不回显密钥值或引用。

### 19.5 其他外部系统

| 系统 | 接口 | 用途 |
| --- | --- | --- |
| 身份提供方 | OIDC | 用户认证与组同步（本期） |
| Secret Manager | Vault API 或云厂商 API | 读取 DHIS2 凭证、模型 API 密钥；写入演示用户凭证（本期） |
| 对象存储 | S3 兼容 API | 产物内容存储 |
| 邮件 / Webhook | SMTP、HTTPS Webhook（HMAC 签名） | Gate 通知与提醒（可选） |
| 可观测性后端 | OTLP | 日志、指标、链路追踪 |

---

## 20. 数据分级与 LLM 使用策略

默认禁止把数据发送给外部 LLM；每一次模型调用都由 Policy Engine 根据数据分级、模型提供方类别和项目有效策略（组织策略与项目全部目标环境策略中最严格者）逐次判定。

### 20.1 数据分级

| 级别 | 示例 | 默认可发送的提供方类别 |
| --- | --- | --- |
| PUBLIC | 公开的 DHIS2 文档、通用术语 | 任意已启用的提供方 |
| INTERNAL | 业务流程描述、页面草图、元数据结构（不含实例数据） | LOCAL_ONLY、PRIVATE_NETWORK、APPROVED_EXTERNAL |
| CONFIDENTIAL | 客户未公开的项目文档、目标实例的元数据导出 | LOCAL_ONLY、PRIVATE_NETWORK |
| RESTRICTED | 实例安全配置、用户清单、组织内部人员信息 | LOCAL_ONLY |
| PHI/PII | 真实个人健康信息、可识别个人身份的数据 | 禁止发送给任何 LLM |

### 20.2 提供方类别

| 类别 | 含义 |
| --- | --- |
| LOCAL_ONLY | 与 Factory 同一受控基础设施内运行的本地模型 |
| PRIVATE_NETWORK | 部署在组织私有网络或专属租户中的模型服务 |
| APPROVED_EXTERNAL | 已签订数据处理协议、经组织批准的外部模型服务 |
| UNRESTRICTED | 其他外部服务，仅允许处理 PUBLIC 数据 |

### 20.3 策略规则

- **FR-POL-001** 每个产物在创建时必须标注数据分级；由多个产物派生的产物取其来源中的最高级别，除非经过批准的脱敏处理。
- **FR-POL-002** 系统默认策略为：拒绝外部提供方。组织或环境可以显式放宽策略，放宽操作必须记录审计（本期起限定平台管理员执行）。PHI/PII 在任何策略下都不进入模型上下文。
- **FR-POL-003** Agent 运行前，Policy Engine 必须检查上下文中所有内容的分级与目标提供方类别；任一内容不允许时，运行被拒绝并返回 `POLICY_DENIED`，列出被拒绝的产物；禁止静默截断后继续发送。
- **FR-POL-004**（本期）系统必须提供可选的脱敏处理：对 CONFIDENTIAL 内容中的名称、地址、标识符进行替换后降级使用；脱敏映射仅保存在后端，不进入模型上下文。
- **FR-POL-005** 每次模型调用必须在审计中记录：提供方、类别、发送内容涉及的产物 ID 与分级、策略判定结果。

### 20.4 Fixture 数据

- **FR-POL-006** Fixture 默认只允许合成数据，由 Fixture Compiler 根据 Registry 中的值类型、选项集和 Fixture 规格生成。
- **FR-POL-007** 使用真实数据（包括脱敏后的真实数据）作为 Fixture 必须经过单独审批，只能导入 DEV 或 TEST 类环境，且禁止进入任何 LLM 上下文。
- **FR-POL-008** 合成数据生成必须可复现：使用记录在构建记录中的种子，同一输入产生同一数据集。

---

## 21. 安全、权限与审计

系统的安全边界有三条：凭证只在执行层解析；所有高风险动作由后端基于状态（并加上角色）判定；所有操作可审计。

### 21.1 身份与访问控制

认证、角色与权限为本期交付，不设"先不实现"的阶段。`FACTORY_AUTH_MODE=off` 是兼容/评估模式：此时改变状态的请求必须携带操作者名称（`X-Operator`）并写入审计与审批记录，授权钩子恒允许且不设访问保护；生产部署必须使用 `local|oidc|both`，以会话主体为准并执行真实授权。以下要求在生产模式下生效：

- **FR-SEC-001** Factory 必须支持组织单点登录（OIDC）与本地账号两种认证方式；生产部署的所有 API 需要认证（`authMode=off` 仅供本地演示与评估）。
- **FR-SEC-002** 系统采用基于角色的访问控制，内置角色与 2.4 节用户角色一致，权限粒度至少到“项目 × 动作”（查看、编辑、审批某类 Gate、对某类环境执行）。
- **FR-SEC-003** 产物访问受项目成员关系控制；PHI/PII 与 RESTRICTED 级别产物的读取必须单独授权并记录审计。
- **FR-SEC-004** V1.0.0 为单组织部署：一个 Factory 部署服务一个组织，项目之间通过成员关系隔离；多租户不在范围内。
- **FR-SEC-005** 会话空闲超时默认 30 分钟（可配置）；所有改变状态的请求必须具备 CSRF 防护。Factory 在任何里程碑都只通过 HTTPS 提供服务。

### 21.2 DHIS2 凭证

- **FR-SEC-006**【调整】（契约 1.7.0，S1 CP-SEC-001）DHIS2 的个人访问令牌、密码、OAuth 客户端密钥与模型 API 密钥**禁止以明文存入业务数据库**：业务库中的凭证字段必须是经认证加密的密文（AES-256-GCM，密钥由部署主密钥经 HKDF 派生、每记录独立 salt，密文绑定其引用名），主密钥只由部署环境变量提供、不入库、不回显、不写日志。生产部署仍**优先**使用 Vault、云 Secret Manager 或由外部密钥管理支撑的 Kubernetes Secret（引用格式 `vault://` 等保持兼容）。未配置主密钥时凭证录入端点返回 503 且不启用该后端，服务照常启动——把可选功能缺失变成起不来，会让一台本来能跑的机器因噎废食。
  **本条的保护范围必须如实理解**：加密防的是业务库的读取面（备份、副本、`pg_dump`、能 `SELECT` 的人），不防已经取得该主机与部署环境变量的人；需要后者仍须使用外部密钥管理。理由：1.2.0 之前唯一的落地方式是先登录服务器把值写进环境文件，再回界面敲引用名，这是"配置→运行"路上唯一要求离开浏览器的一步。
- **FR-SEC-007**【调整】（契约 1.7.0，S5IF-SEC01）浏览器**不接收、不缓存、不显示、不记录**凭证的值：值只在录入的那一瞬间经 HTTPS 请求体单向进入服务端，服务端立即加密并只返回引用（`db://名字`）；界面不得回显值、掩码、长度或前缀，录入控件必须是 `type="password"` 且 `autocomplete="new-password"`，提交后不得驻留在任何前端状态或本地存储里。执行层继续通过"Secret 引用 → Secret 后端 → DHIS2 Client"链路使用凭证。理由：原条把"不接收"与"不发送"混为一谈，使得唯一可行的落地路径是让人 SSH 进服务器；单向发送、永不回读既保住"浏览器不持有凭证"的实质，也让配置可以在界面里完成。
- **FR-SEC-008**【调整】（契约 1.7.0）凭证禁止出现在产物、提示词、Agent 上下文、日志与审计中；日志与审计必须对 Authorization 头、Cookie、令牌、密码、客户端密钥进行脱敏。【新增】凭证值同样禁止出现在**幂等响应回放表**与**校验失败的响应体**里：422 的错误摘要若含客户端提交的原值，等于把密钥抄进客户端日志与代理日志，因此对承载凭证的请求体字段，服务端必须在渲染前抹掉其输入值（逐字段错误只说字段与原因）。
- **FR-SEC-009** 系统应优先使用个人访问令牌，支持登记令牌到期日、到期提醒（默认提前 14 天）与轮换。【说明】轮换的落地形态是"同名再写一次即替换"（`PUT /api/v1/secrets/{name}`，`rotatedAt` 记录换值时刻），一个引用的多版本保留与到期策略尚未实现。

### 21.3 DHIS2 最小权限

系统按操作区分所需权限范围：发现（只读元数据与系统信息）、元数据导入、Tracker 数据导入、DataStore 读写、应用管理、管理（维护动作）。

- **FR-SEC-010** 每个环境可以为不同范围配置不同的服务账户；系统默认不要求超级用户权限，连接测试必须报告账户实际具备和缺少的权限。
- **FR-SEC-011** 对 PRODUCTION 类环境，发现操作必须使用只读账户，提交操作使用单独的导入账户。

### 21.4 审计

以下事件必须写入审计日志：项目创建、配置变更、归档、导出与导入；输入上传；阶段开始与结束；Agent 运行开始与结束（含提供方与策略判定）；Question 创建与回答；Blueprint Patch 提交、批准、拒绝；产物创建与替代；Gate 创建、批准、拒绝、失效；环境配置变更与连接测试；发现；Snapshot；VALIDATE 与 COMMIT 的开始与完成；维护动作；部署后验证；策略放宽；凭证引用变更；紧急锁定变更；人工核实结果；用户与角色变更（本期）。审计事件类型以“子系统.对象.动作”命名，由产生方子系统登记。

- **FR-SEC-012** 审计记录只追加，禁止修改与删除；每条记录包含操作者、时间、关联对象 ID、前后状态、correlationId。
- **FR-SEC-013** 审计记录必须以哈希链方式串联，并提供完整性校验功能以检测篡改（报告第一处断裂的位置与原因）。审计记录在业务事务中写入，事务提交后 10 秒内完成链接；未链接的记录同样禁止修改与删除；系统每日自动执行全链校验。审计写入前检测凭证特征并脱敏。
- **FR-SEC-014** 任一 COMMIT 必须可追溯到：环境、操作者、审批人、载荷产物与哈希、Blueprint 版本、Gate 记录、执行记录、ImportReport。

---

## 22. 非功能需求

以下指标针对 Factory 自身；LLM 与 DHIS2 的外部调用延迟不计入核心 API 指标，但必须单独监控。

### 22.1 可靠性与可用性

- **NFR-REL-001** 服务重启不丢失任何工作流、阶段、Gate、执行状态。
- **NFR-REL-002** 长时间运行与等待人工的流程由持久化工作流引擎承载；进程崩溃后从最后一个持久化步骤恢复。
- **NFR-REL-003** 恢复时不得重复执行已完成的不可逆操作（COMMIT、DataStore 写入、Tracker 导入）；无法确认结果的操作进入人工检查状态。
- **NFR-REL-004** 产物内容在写入对象存储并回读校验哈希后，才可被标记为已创建；产物内容不可变，修改即产生新产物。
- **NFR-REL-005** 审计记录与业务状态在同一数据库事务中写入；审计写入失败时，对应的业务操作必须失败。
- **NFR-REL-006** 生产部署的 Factory 服务月可用性不低于 99.5%（不含计划维护窗口）。
- **NFR-REL-007** Factory 自身数据（PostgreSQL、对象存储、Temporal）的恢复点目标（RPO）不超过 15 分钟，恢复时间目标（RTO）不超过 4 小时；备份恢复流程每季度至少演练一次。

### 22.2 并发与幂等

- **NFR-CON-001** 同一工作流同一时刻只允许一个阶段运行；同一项目同一时刻最多一个工作草稿，基于同一基准版本只能有一个草稿被提升为正式版本。
- **NFR-CON-002** 对同一环境的提交类执行（COMMIT、Fixture 导入、维护动作、删除）必须获得环境级独占锁（跨项目）；并发请求中只有一个获得锁，其余返回 `CONCURRENCY_ERROR`。
- **NFR-CON-003** 产物创建、Agent 运行、校验、VALIDATE、COMMIT 请求必须幂等。

### 22.3 性能

| 编号 | 指标 | 目标 |
| --- | --- | --- |
| NFR-PERF-001 | 核心 API P95 响应时间（不含外部调用） | < 500 ms |
| NFR-PERF-002 | 工作流与产物元数据查询 P95 | < 300 ms |
| NFR-PERF-003 | 1,000 个对象规模的元数据包编译 | < 30 s |
| NFR-PERF-004 | 1,000 个对象规模的静态与跨产物校验 | < 30 s |
| NFR-PERF-005 | 事件从产生到推送给客户端 P95 | < 2 s |

- **NFR-PERF-006** Agent 与编译任务在后台工作进程中执行，不阻塞 API 请求。
- **NFR-PERF-007** 大文件上传与下载采用流式处理与对象存储。

### 22.4 容量

| 编号 | 指标 | 目标 |
| --- | --- | --- |
| NFR-CAP-001 | 单项目元数据对象数 | 至少 5,000 |
| NFR-CAP-002 | 单项目页面数（模式 B） | 至少 100 |
| NFR-CAP-003 | 单个输入文件大小 | 上传上限 100 MB（可配置）；产物存储本身不设默认上限 |
| NFR-CAP-004 | 单部署并发活跃项目数 | 至少 50 |
| NFR-CAP-005 | 单项目 Blueprint 版本数 | 不设上限，历史版本全部可查询 |

### 22.5 可观测性与成本

- **NFR-OBS-001** 所有日志为结构化 JSON，包含 correlationId、操作者、projectId、workflowId、stageId、agentRunId、artifactId、executionId（如适用）。
- **NFR-OBS-002** 系统必须采用 OpenTelemetry 输出链路追踪，覆盖 API、工作流步骤、模型调用、DHIS2 调用。
- **NFR-OBS-003** 至少提供以下指标：工作流总数与失败数、阶段耗时、Agent 运行数与失败数、模型延迟与 token 用量、策略拒绝次数、校验失败数、VALIDATE 与 COMMIT 成功与失败数、部署后验证失败数、Gate 平均等待时长。
- **NFR-COST-001** 每次模型调用记录输入、输出 token 与估算成本，可按项目、工作流、阶段、Agent、模型汇总。
- **NFR-COST-002**（本期）可为项目设置模型成本预算与告警阈值；超出预算时拒绝新的 Agent 运行并通知项目负责人（进行中的运行允许完成），人工编写模式不受影响。

### 22.6 可用性与无障碍

- **NFR-UX-001** Factory UI 必须满足 WCAG 2.1 AA 级无障碍要求。
- **NFR-UX-002** 支持以下浏览器的最新两个主版本：Chrome、Edge、Firefox、Safari；最小支持视口宽度 1280 像素。
- **NFR-UX-003** 界面语言至少支持简体中文与英文，所有界面文案通过翻译资源管理。

### 22.7 可维护性与可扩展性

- **NFR-MNT-001** 新增元数据对象类型只需新增类型描述器、编译模板与校验规则（见 FR-COV-001）。
- **NFR-MNT-002** 新增 DHIS2 版本只需新增版本能力 Profile（见 FR-CAP-001）。
- **NFR-MNT-003** 新增模型提供方只需新增 `LLMProvider` 适配器。
- **NFR-MNT-004** 核心领域逻辑单元测试行覆盖率不低于 85%；Compiler、Validator、公共基础库、状态机与 Gate 规则不低于 90%。

### 22.8 兼容性维度

系统必须维护以下版本维度，并在每个产物的构建记录中登记：DHIS2 版本与 Profile 版本、Blueprint Schema 版本、Blueprint 版本（Registry 随其版本化）、类型描述器版本、提示词版本、模型版本、Compiler 版本、模板版本、OpenAPI 版本、开发包模板版本。

---

## 23. 数据模型与数据管理

全部后端子系统共用一个 PostgreSQL 数据库，每个子系统独占一个 schema（`core`：S0；`cp`：S1；`bp`：S2；`ai`：S3；`dx`：S4），禁止跨 schema 的 SQL 访问与外键；大体积产物内容保存在对象存储中，数据库只保存位置与哈希。每类数据只有一个所有者子系统，各实体的完整字段见对应子系统 SRS 的数据需求章节。

### 23.1 核心实体

| 实体 | 所有者 | 最少字段 |
| --- | --- | --- |
| Project | S1 | id、name、description、ownerName、codePrefix、milestoneStatus、archived、currentBlueprintVersion（交付模式、项目类型、目标版本约束、语言属于 Blueprint） |
| ProjectTargetEnvironment | S1 | projectId、environmentKey、promotionOrder、isDesignReference |
| User / Role / Session | S1 | 用户：id、userName、displayName、email、enabled、locked、mustChangePassword；角色：name、actions；会话：id、userId、createdAt、expiresAt、csrf |
| ProjectMember | S1 | projectId、userId、roles（本期） |
| ArtifactGrant / ProductionExemption | S1 | 受限产物授权：projectId、userId、artifactType 或 artifactId、grantedBy；生产豁免：projectId、environmentKey、reason、grantedBy、expiresAt |
| SecretCredential | S1 | name、secretRef、ciphertext、rotatedAt、createdBy（值只以 AES-256-GCM 密文存在，接口只回引用） |
| Workflow | S1 | id、projectId、status、paused、currentStageId、lastEventSeq、version |
| Stage | S1 | id、workflowId、deploymentRunId、stageType、status、attempt、inputBlueprintRef、outputArtifactIds、failureSourceStage、feedback |
| DeploymentRun | S1 | id、workflowId、environmentKey、blueprintVersion、status、outdated、validatedPayloadHash |
| Question | S1 | id、stageId、source（类型、agentRunId、本地 ID）、text、severity、affects、answerMode（SINGLE/MULTIPLE）、suggestions（含 recommended）、blocking、status、answer、answeredBy、answeredAt |
| Gate | S1 | id、workflowId、deploymentRunId、type、environmentKey、blueprintRef、boundRefs、warnings、requiredApprovals、status |
| Approval | S1 | id、gateId、approverName、decision、comment、warningDispositions、confirmations、seenBoundHashes |
| ChangeRequest | S1 | id、projectId、reason、affectedBusinessKeys、deletionCandidates、requestedBy、status、impactReportArtifactId、resultBlueprintVersion |
| Notification | S1 | id、projectId、type、objectRef、read、createdAt |
| Artifact | S0 | id、projectId、workflowId、type、version、status、contentLocation、contentHash、dataClassification、buildRecord、sourceArtifactIds、prevVersionId、creator、createdAt、contentPurged |
| AuditEvent | S0 | id、seq、occurredAt、operatorType、operatorName、sourceSubsystem、eventType、objectType、objectId、projectId、before、after、correlationId、prevHash、hash |
| BlueprintVersion | S2 | id、projectId、version、parentVersion、schemaVersion、changeLevel、contentHash、artifactId、status、decisionIds、createdBy |
| BlueprintDraft / BlueprintPatch | S2 | 草稿：id、projectId、baseVersion、stageScope、status、currentHash、frozenHash；Patch：id、draftId、baseVersion、author、operations、status、resultVersion |
| RegistryEntry / EnvironmentBinding | S2 | 条目：businessKey、dhis2Type、uid、code、disposition、managed、controlledFields、status、decisionId、观测基准；绑定：environmentKey、businessKey、uid、观测基准、resolution |
| Decision | S2 | id、projectId、type、subject、selectedOption、alternatives、rationale、risks、confidence、confirmation |
| AgentRun | S3 | id、projectId、stage、agentType、promptVersions、provider、providerClass、model、inputArtifactIds、inputHashes、policyDecision、outputArtifactId、status、usage、estimatedCost、language |
| LLMExchange / StageChat | S3 | 交换：id、agentRunId 或解析运行、provider/model、请求消息、原始应答、token 用量、耗时、错误、时间；对话：projectId、stage、turns、contextHeader |
| Provider / ModelRegistration | S3 | 提供方：ref、baseUrl、apiKeySecretRef、capabilities、enabled；模型：ref（provider/model）、capabilities、单价、applicableAgents、endpoint 覆盖、apiKeySecretRef 覆盖、enabled |
| Environment | S4 | key、name、baseUrl、environmentClass、versionConstraint、authMode、secretRefs、tlsRequired、llmDataPolicy、protectionSettings、status |
| CapabilityProfile / DiscoveryResult / Snapshot | S4 | 环境键、版本或范围、产物 ID、内容哈希、时间 |
| Execution | S4 | id、projectId、environmentKey、executorType、mode、payloadArtifactId、payloadHash、idempotencyKey、asyncJobRef、status（含 NEEDS_MANUAL_CHECK）、importReportArtifactId、operator |

### 23.2 产物类型与状态

- **FR-DATA-001** 产物类型至少包括：`BUSINESS_INPUT`、`NRR`、`AGENT_OUTPUT`、`DISCOVERY_RESULT`、`CAPABILITY_PROFILE`、`BLUEPRINT`、`METADATA_PACKAGE`、`DATASTORE_CHANGESET`、`OPENAPI`、`UI_SPECIFICATION`、`FIXTURE_PACKAGE`、`VALIDATION_REPORT`、`DIFF_REPORT`、`EXECUTION_PLAN`、`DHIS2_IMPORT_REPORT`、`SNAPSHOT`、`EXECUTION_REPORT`、`POST_VERIFICATION_REPORT`、`FRONTEND_PACKAGE`、`HANDOFF_CHECK_REPORT`、`MANIFEST`、`CHANGE_IMPACT_REPORT`、`PROJECT_EXPORT`。每种类型登记其允许的所有者子系统，只有所有者可创建该类型产物。领域模型作为 Blueprint 的分节保存，不单独成为产物类型。
- **FR-DATA-002** 产物状态为 `DRAFT`、`PROPOSED`、`APPROVED`、`SUPERSEDED`。产物内容一律不可变；修改产生新产物版本，旧版本变为 SUPERSEDED。
- **FR-DATA-003** 数据库结构变更通过迁移脚本管理，禁止手工修改生产数据库结构；每个子系统维护本 schema 的迁移，由公共迁移运行器按 `core → dx → bp → ai → cp` 顺序执行，生产环境只允许前向迁移。

### 23.3 数据保留与删除

- **FR-DATA-004** 产物、Blueprint 版本与执行记录默认永久保留；平台管理员可按组织策略配置已归档项目的保留期限。
- **FR-DATA-005** 审计记录的保留期限不得短于其关联项目的保留期限，且不受项目删除影响。
- **FR-DATA-006** 项目删除只允许对已归档项目执行，删除前系统必须生成 `PROJECT_EXPORT` 产物供下载；删除时各子系统清理本方项目数据、产物内容按保留规则清除（元数据与审计保留），UID 分配记录保留以防复用；删除操作记录审计。
- **FR-DATA-007** 含 PHI/PII 分级的输入产物必须支持单独清除内容（保留元数据与哈希），以满足数据主体删除要求。

---

## 24. 技术栈与部署

系统由 S0～S5 六个子系统组成：源码位于独立仓库、独立开发与发版；S0～S4 以 Python 包发布到私有 PyPI，由 S1 按固定版本组装为后端运行时；S5 为独立的静态前端。后端子系统之间只通过公共 Python 库 API 调用，不使用消息中间件。

### 24.1 技术栈

| 领域 | 选型 |
| --- | --- |
| 后端语言与框架 | Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2.x（同步）、Alembic、httpx |
| 工程工具 | uv、ruff、mypy（strict）、pytest |
| 持久化工作流 | Temporal（生产必需，仅 S1 使用）；开发模式提供进程内执行适配器，两者实现同一接口 |
| 数据库 | PostgreSQL（共用一个数据库，按子系统划分 schema） |
| 实时事件扇出 | PostgreSQL LISTEN/NOTIFY（不引入 Redis 或其他中间件） |
| 对象存储 | S3 兼容存储（开发环境可用本地文件系统适配器） |
| 模型接入 | 统一 `LLMProvider` 接口；默认 OpenAI 兼容接口适配器（可配置地址、Key、模型名称），本地或私有模型经其 OpenAI 兼容接口接入 |
| Secret 管理 | Vault 或云 Secret Manager（开发环境可用环境变量后端） |
| 前端 | React、TypeScript、Vite、React Router、TanStack Query、Zustand、React Hook Form、Zod、`@dhis2/ui` |
| 图与编辑器 | React Flow（领域图、映射图、依赖图）、Monaco Editor（JSON、YAML、Diff） |
| API 文档查看 | Scalar API Reference（只作为查看器；OpenAPI 事实来源是 Compiler 输出） |
| 可观测性 | OpenTelemetry、Prometheus、Jaeger 或兼容后端、结构化 JSON 日志 |
| 测试 | pytest、Testcontainers（仅用于 Factory 自身的 PostgreSQL、对象存储、Temporal）、Playwright、JSON Schema 与 OpenAPI 校验器、DHIS2 官方在线演示实例（v2.40～v2.43）、Temporal 测试环境 |

### 24.2 代码仓库结构

| 仓库 | 子系统 | 主要内容 |
| --- | --- | --- |
| factory-foundation | S0 | 公共基础库（`factory_foundation`）、`core` schema 迁移、测试套件 |
| factory-control | S1 | 控制平面（`factory_control`）、`cp` schema 迁移、运行时组装、Dockerfile、Docker Compose、Helm Chart |
| factory-blueprint | S2 | Blueprint 与构建（`factory_blueprint`）、分节 Schema、类型描述器与编译模板、`bp` schema 迁移 |
| factory-intelligence | S3 | 需求与智能（`factory_intelligence`）、Prompt 源文件、评测集、`ai` schema 迁移 |
| factory-dhis2 | S4 | DHIS2 集成与执行（`factory_dhis2`）、版本 Profile 数据、`dx` schema 迁移 |
| factory-web | S5 | Factory UI |

每个库子系统在包内提供唯一公共入口模块 `api`（含 `initialize()` 与 `MIGRATION_CONFIG`）及测试替身模块 `testing`；Golden Project 与端到端测试由测试组维护。

### 24.3 部署线框图

```text
                     +------------------+
   浏览器  --HTTPS-->| Reverse Proxy    |（静态前端与 /api/v1 同域）
                     +--------+---------+
                              |
             +----------------+----------------+
             |                                 |
   +---------v---------+             +---------v---------+
   | factory-web (S5)  |             | factory-api (S1)  |
   | 静态前端          |             | REST + SSE（无状态）|
   +-------------------+             +----+-----+--------+
                                          |     |
                     +--------------------+     +----------------+
                     |                                           |
             +-------v------+                            +-------v------+
             | PostgreSQL   |<-- LISTEN/NOTIFY           | Temporal     |
             | core/cp/bp/  |                            +-------+------+
             | ai/dx        |                                    |
             +-------+------+                                    |
                     +-------------------+-----------------------+
                                         |
                              +----------v-----------+
                              | factory-worker (S1)  |---> 对象存储
                              | 内含 S0～S4 库：     |---> Secret Manager
                              | Agent / Compiler /   |---> 模型提供方（经 Policy Engine）
                              | 执行器               |
                              +----------+-----------+
                                         | HTTPS
                              +----------v-----------+
                              | 目标 DHIS2 实例      |
                              +----------------------+
```

### 24.4 部署单元

| 单元 | 说明 | 扩展方式 |
| --- | --- | --- |
| Reverse Proxy | TLS 终止、路由（前端与 API 同域） | 水平扩展 |
| factory-web | 静态前端 | CDN 或静态托管 |
| factory-api | 无状态 API 与 SSE；启动时执行迁移（可配置） | 水平扩展 |
| factory-worker | Temporal 工作进程：Agent、Compiler、执行器、定时任务 | 按任务队列水平扩展；须能以 HTTPS 访问目标 DHIS2 |
| PostgreSQL | 全部领域状态与审计 | 主备 |
| Temporal | 持久化工作流与定时调度 | 官方推荐拓扑 |
| 对象存储 | 产物内容 | 托管服务 |
| 本地模型服务（可选） | LOCAL_ONLY 与 PRIVATE_NETWORK 类模型 | 按 GPU 资源 |

- **FR-DEP-001** 开发模式下允许以进程内工作流适配器替代 Temporal，两者实现同一接口；各子系统在代码与数据层面保持隔离。
- **FR-DEP-002** 系统配置通过环境变量与配置文件提供（`APP_ENV`、`DATABASE_URL`、`OBJECT_STORE_URL`、`SECRET_BACKEND`、Temporal 地址、`OTEL_EXPORTER_OTLP_ENDPOINT` 等；各子系统可声明自己的配置项）。DHIS2 地址与凭证不属于系统级配置，只存在于 Environment 与 Secret Manager 中。
- **FR-DEP-003** 系统必须以容器镜像交付，并提供 Docker Compose（开发与评估）与 Helm Chart（生产）两种部署描述。
- **FR-DEP-004** 后端进程启动顺序：日志与追踪初始化 → 迁移（仅 factory-api）→ 公共基础库初始化 → S4、S2、S3 初始化 → S1 登记；定时任务（审计链接每 5 秒、日常清理每小时、审计全链校验与保留期清理与凭证到期检查每日）由 `factory-worker` 调度（Temporal schedule；进程内引擎下为定时循环），`factory-api` 只提供 HTTP/SSE。裸机部署下两者各由一个 systemd 单元守护，worker 必须在 api 通过 `/healthz` 后启动；`/readyz` 以真实数据库语句探活，`/healthz` 只表示进程存活。
- **FR-DEP-005** 组装时必须运行各子系统公共 API 的兼容性测试，任一失败则构建失败。
- **FR-DEP-006** 除镜像交付（FR-DEP-003）外，允许单机裸机部署：由反向代理以 TLS 同时前置静态前端与 `/api/v1`（二者必须同域，见 00 文档 6.11），后端进程只监听回环地址并由进程守护机制管理，对外端口可配置，对象存储可指向本地文件系统。会话 Cookie 带 `Secure` 属性（FR-SEC-005），因此浏览器侧只有 HTTPS 才能登录；以明文 HTTP 暴露时只能运行在不启用认证的里程碑模式下。

---

## 25. 测试与质量保证

质量保证的核心是 Golden Project 回归集：一组固定的业务项目，从输入到开发包全链路反复执行；任何 Agent、提示词、模型、Compiler、Schema 或 Profile 的变更都必须通过它。

### 25.1 测试分层

| 层级 | 对象 | 要求 |
| --- | --- | --- |
| 单元测试 | 状态迁移、Gate 规则、UID 分配、依赖图、哈希、Patch 应用、重试与幂等、各校验规则、策略判定 | 无外部依赖，随每次提交运行 |
| Compiler 快照测试 | 固定 Blueprint → 固定产物 | 输出哈希必须与基线一致；基线变更需评审 |
| 集成测试 | PostgreSQL、对象存储、Temporal、LLM 适配器（模拟）、Secret 后端 | 使用 Testcontainers 与公共基础库测试套件 |
| 公共 API 兼容性测试 | 各子系统公共 API 与其测试替身 | 各子系统 CI 运行依赖方的兼容性测试；组装时全量运行 |
| 契约测试 | 每个版本 Profile 对应的 DHIS2 端点、参数、元数据与 Tracker 载荷 | 在对应版本的 DHIS2 官方在线演示实例上运行 VALIDATE 与 COMMIT（见 25.4） |
| Agent 评测 | 每个 Agent 的评测集（输入 + 期望结构化输出） | 提示词或模型变更前后对比，结果记录到 Model Registry |
| 端到端测试 | 输入 → 各阶段 → Gate → VALIDATE → COMMIT → 部署后验证 → 开发包 | 使用 Playwright 驱动 UI；LLM 使用录制回放或固定评测模型 |
| 安全测试 | 凭证泄露、提示词注入、依赖漏洞；含认证授权 | 每个里程碑退出前执行 |

### 25.2 Golden Project 回归集

每个项目都有固定输入、固定 Blueprint 基线、期望的元数据包与开发包：

| 编号 | 场景 | 启用里程碑 |
| --- | --- | --- |
| GP-01 | 单 Tracker Program，含属性、选项集、Program Stage（Greenfield） | M2 |
| GP-02 | 单 Event Program，含 CategoryCombo | M2 |
| GP-03 | 多 Program 医疗服务场景，含 RelationshipType | M2 |
| GP-04 | ProgramRule 与 ProgramIndicator 密集场景 | M2 |
| GP-05 | ValidationRule、自定义 Attribute 与多语言场景 | M2 |
| GP-06 | Brownfield：复用既有 OptionSet 与 DataElement、更新既有 Program 的受控字段 | M2 |
| GP-07 | Brownfield：仅引用既有 OrgUnit 与用户组，跨两个环境晋级 | M4 |
| GP-08 | 共享与权限模型复杂场景 | M2 |
| GP-09 | 多页面自定义应用（模式 B） | M3 |
| GP-10 | 聚合数据 DataSet 场景 | M5 |
| GP-11 | DataStore 应用配置场景 | M5 |
| GP-12 | 变更请求：对已部署项目新增字段并修改规则 | M2 |

### 25.3 质量指标

| 类别 | 指标 |
| --- | --- |
| 结构质量 | Blueprint Schema 通过率；Registry 引用一致率；OpenAPI 校验通过率；静态校验通过率 |
| 执行质量 | VALIDATE 首次通过率；COMMIT 失败率；部署后验证失败率；恢复成功率 |
| Agent 质量 | 每阶段 Question 数量；人工修正率；Patch 拒绝率；跨产物不一致率；评测集得分 |
| 效率 | 从创建项目到首次 VALIDATE 通过的时长；各 Gate 平均等待时长；每个项目的模型成本 |

这些指标用于工程改进，禁止作为业务正确性的保证对外宣称。

### 25.4 DHIS2 回归测试环境

DHIS2 相关的契约测试与 Profile 回归**直接使用 DHIS2 官方在线演示实例**，不使用容器化 DHIS2 实例，以保证测试对象就是真实发布的版本：

| 版本 | Profile | 实例基址（Environment 的 baseUrl，不含 `/api`） |
| --- | --- | --- |
| 2.40.12 | `Dhis240Profile` | `https://play.im.dhis2.org/stable-2-40-12` |
| 2.41.10 | `Dhis241Profile` | `https://play.im.dhis2.org/stable-2-41-10` |
| 2.42.6 | `Dhis242Profile` | `https://play.im.dhis2.org/stable-2-42-6` |
| 2.43.1 | `Dhis243Profile` | `https://play.im.dhis2.org/stable-2-43-1` |

- **FR-TST-001** 上表实例以测试配置维护（不写死在代码中）；官方下线某实例或升级补丁版本时，更新配置并重新执行回归。每个实例在测试中登记为 TEST 类环境，凭证使用官方公开的演示账户，以 Secret 引用配置在 CI 的 Secret 存储中。
- **FR-TST-002** 演示实例是公开共享、会被官方定期重置的环境，测试必须满足：① 本次运行创建的全部对象使用带运行标识的专属 code 前缀；② 不修改、不删除实例中既有的演示对象，只以 REFERENCE / REUSE 方式引用（如组织单元、default 分类组合）；需要“既有对象”的 Brownfield 场景（如 GP-06），先在本次运行中创建该对象作为前置数据；③ 测试结束后删除本次创建的元数据与 Tracker 数据（清理失败只告警）；④ 只使用合成数据；⑤ 同一实例上本项目的回归运行串行执行。
- **FR-TST-003** 回归按日定时执行，并在每次发版前执行；不作为代码合并的阻断检查（合并检查使用各子系统的测试替身）。实例不可达、被重置或返回服务端错误时，本次结果记为“无法判定”而不是“失败”，并自动重试；发版前每个受支持版本必须有一次在 7 天内全部通过的回归结果。
- **FR-TST-004** 演示实例的数据规模与负载不代表生产，性能类非功能需求（如 NFR-PERF-*、执行管线耗时）不以演示实例的结果作为验收依据，需在规模接近生产的非生产实例上确认。
- **FR-TST-005** 同版本多环境晋级（GP-07）在演示实例上以两个不同版本的实例分别担任 TEST 与 UAT 执行；同版本多环境的完整验证在项目自有的非生产环境中进行。

---

## 26. 发布规划

产品按五个里程碑依次交付，M2 为 MVP 正式版（模式 A、Tier 1、v2.41）。本规格不规定人力与工期；里程碑以能力范围和退出标准定义，并行度由团队规模决定。M3 与 M4 的工作可以并行开展，但退出评审按顺序进行。

```text
M1 基础平台 ──> M2 元数据工厂（MVP）──> M3 自定义应用交付 ──> M4 生产强化 ──> M5 范围扩展
```

| 里程碑 | 能力范围 | 退出标准 |
| --- | --- | --- |
| M1 基础平台 | 公共基础库；项目、工作流与阶段状态机、Gate 与审批、通知、产物存储与哈希、审计、Temporal 集成；Blueprint Schema、Patch、工作草稿与版本；环境管理与连接测试、Secret 管理；Policy Engine、LLMProvider、Prompt / Model Registry、模拟 Agent；人工编写模式框架；基础 Factory UI 与事件流 | 使用模拟 Agent 跑通所有设计阶段与 Gate；崩溃恢复测试通过；审批失效与策略拒绝测试通过；标记为 M1 的验收标准全部通过 |
| M2 元数据工厂（MVP） | 需求规范化（文本图、清单、文本型文档、图片）、领域建模、环境发现、映射与 Registry（全部 Disposition、UID 池、环境绑定）、Tier 1 Metadata Compiler、多语言、四层校验（含本地表达式校验）、Deployment Run、Metadata Executor、漂移检查、Snapshot、Diff、部署后验证、人工核实、变更请求、Dhis241Profile、审计完整性校验、项目导出与导入、归档与删除、PRODUCTION 的先验证、时间窗、紧急锁定与备份确认 | GP-01 至 GP-06、GP-08、GP-12 在 v2.41 官方在线演示实例上全链路通过；标记为 M2 的验收标准全部通过 |
| M3 自定义应用交付 | 页面模型、API Agent 与 UI Agent、Fixture Agent、OpenAPI Compiler、UI Spec Compiler、Fixture Compiler、Tracker Data Executor 与演示用户、Package Compiler、开发包完整性检查、交付模式升级、Review Assistant 与阶段内对话 | GP-09 通过；一名未参与设计的前端开发者仅凭开发包完成至少一个页面并联调成功；标记为 M3 的验收标准全部通过 |
| M4 生产强化 | 身份认证、角色与权限（OIDC、本地账号、项目成员、受限产物授权）、PRODUCTION 多人审批与职责分离、生产豁免、Dhis240Profile、Dhis242Profile、Dhis243Profile、多环境晋级回归、脱敏处理、成本预算、上传恶意内容扫描、高可用部署、备份恢复演练 | Golden Project 在 v2.40、v2.41、v2.42、v2.43 官方在线演示实例上均通过；GP-07 通过；安全评审通过；标记为 M4 的验收标准全部通过 |
| M5 范围扩展 | Tier 2 与 Tier 3 对象、DataStore Executor、App Upload Executor、前端起步工程生成、从既有元数据反向构建 Blueprint | GP-10、GP-11 通过；生成的起步工程可构建并连接目标环境；标记为 M5 的验收标准全部通过 |

**当前实现状态（2026-10-25，对照代码）**：M1、M2 全部，以及 M3 的阶段内对话、页面/API/Fixture Agent、HANDOFF，M4 的身份与权限（本地账号、OIDC 依赖方、项目成员、受限产物授权、生产豁免、多人审批与职责分离）、预算写入、上传内容扫描已交付；S1 对外契约为 **1.19.0 / 147 条路径 / 172 个操作**。`FACTORY_AUTH_MODE=off` 仅用于本地演示/评估。M3 的 Tracker Data Executor 与 M4 的高可用实机验证、四个 DHIS2 Profile 的完整回归仍为未交付项；M4 的 DataStore / App 上传执行器按 M5 处理。里程碑表保留原始规划，不因当前交付进度改写历史口径。

---

## 27. 验收标准

每条验收标准以“给定 / 当 → 则”描述，并标明首次必须通过的里程碑；后续里程碑必须持续通过全部既有标准。

### 27.1 验收场景

| 编号 | 给定 / 当 | 则 | 里程碑 |
| --- | --- | --- | --- |
| AC-WF-01 | 新建项目 | UI 显示全部阶段、当前阶段、下一步需要提供的信息；模式 A 项目的页面设计与交付阶段显示为 SKIPPED | M1 |
| AC-WF-02 | Agent 运行期间工作进程崩溃 | 服务恢复后从持久化步骤继续，不重复任何已完成的不可逆操作，无法确认的操作进入人工检查 | M1 |
| AC-WF-03 | 已通过阶段所依赖的 Blueprint 产生 Major 版本 | 下游阶段变为 INVALIDATED，相关 Gate 变为 INVALIDATED | M1 |
| AC-GATE-01 | 已批准的产物内容发生变化 | Gate 自动失效，需要重新审批 | M1 |
| AC-GATE-02 | 客户端直接调用 COMMIT 接口，但 DEPLOY_GATE 未批准 | 后端返回 409 `GATE_NOT_APPROVED`，不产生任何 DHIS2 请求 | M1 |
| AC-GATE-03 | 审批人拒绝 Gate 并填写理由 | 阶段回到 READY，理由显示为待处理反馈，产生拒绝通知 | M1 |
| AC-GATE-04 | PRODUCTION 环境，执行者本人作为审批人之一 | 审批不满足职责分离，COMMIT 被拒绝 | M4 |
| AC-POL-01 | 默认策略下，Agent 上下文包含 CONFIDENTIAL 产物，目标提供方为 APPROVED_EXTERNAL | 运行被拒绝，返回 `POLICY_DENIED` 并列出产物；审计记录该判定 | M1 |
| AC-POL-02 | 输入产物被标注为 PHI/PII | 该产物在任何策略下都不进入模型上下文 | M1 |
| AC-AG-01 | 全部模型均被策略拒绝 | 用户可通过人工编写模式完成该阶段并提交审批 | M1 |
| AC-AG-02 | 同一阶段有 3 道 OPEN Question，其中 1 道为阻塞题 | 界面一次提交整页问卷；服务端先记录全部答案再最多恢复一次运行；阻塞题未答时答案落库但 `resumed=false`，不视为提交失败 | M4 |
| AC-REQ-01 | 上传 Mermaid ER 图 | 由确定性解析器转换为 NRR，不调用 LLM；同一输入两次解析结果一致 | M2 |
| AC-REQ-02 | 上传截图，视觉抽取某字段置信度低于阈值 | 该字段以 Question 呈现，不进入已确认 NRR | M2 |
| AC-BP-01 | 任意编译产物 | 可追溯到唯一的 Blueprint 版本与哈希 | M2 |
| AC-BP-02 | ARCHITECTURE_GATE 已通过，下游 Agent 发现与映射冲突 | Agent 只能产生 Question 或建议 Change Request，映射与 Registry 不变 | M2 |
| AC-REG-01 | 对同一 Blueprint 版本重复编译 3 次 | 不产生新 UID，三次输出哈希一致 | M2 |
| AC-REG-02 | 映射中出现 12 位或数字开头的 UID | Blueprint 校验报告 BLOCKING | M2 |
| AC-REG-03 | Brownfield 项目复用既有 OptionSet | Registry 条目为 REUSE，元数据载荷不包含该 OptionSet，引用校验通过 | M2 |
| AC-REG-04 | UPDATE 条目只声明 name 与 description 为受控字段 | 编译后的对象保留目标环境中其余字段的原值 | M2 |
| AC-COV-01 | 映射中的 DataElement 未指定 CategoryCombo | 编译结果显式引用目标环境 default CategoryCombo 的 UID | M2 |
| AC-COV-02 | 项目声明翻译语言为英文与法文，某对象缺少法文名称 | 静态校验报告 WARNING；补齐后载荷包含两种语言的翻译 | M2 |
| AC-VAL-01 | 载荷缺失引用、类型错误或存在未登记对象 | 静态校验报告 BLOCKING，阶段 BLOCKED，不能进入 DHIS2_VALIDATE | M2 |
| AC-VAL-02 | 静态校验通过，服务端 VALIDATE 返回错误 | 保存 ImportReport，错误映射到 businessKey 与来源阶段，禁止 COMMIT | M2 |
| AC-EXE-01 | 执行 VALIDATE | 目标环境中不产生任何变化 | M2 |
| AC-EXE-02 | VALIDATE 通过后编译产物被重新生成 | 载荷哈希变化，COMMIT 被拒绝，需重新 VALIDATE 与审批 | M2 |
| AC-EXE-03 | VALIDATE 与审批之间，目标环境中被 REUSE 的对象被他人修改 | 漂移检查报告 `DRIFT_DETECTED`，DEPLOY_GATE 失效，要求重新校验 | M2 |
| AC-EXE-04 | 两个请求同时对同一环境 COMMIT | 只有一个获得锁并执行，另一个返回 `CONCURRENCY_ERROR` | M2 |
| AC-EXE-05 | COMMIT 期间网络中断后客户端重试 | 系统先查询已持久化状态与目标环境，确认后才决定是否执行，不产生重复提交 | M2 |
| AC-EXE-06 | COMMIT 完成 | 部署后验证逐对象比对受控字段、引用与共享设置，生成报告 | M2 |
| AC-AUD-01 | 任一 COMMIT | 可追溯到环境、操作者、审批人、载荷与哈希、Blueprint 版本、Gate、执行记录、ImportReport | M2 |
| AC-DATA-01 | 导出项目并导入另一 Factory 部署 | Blueprint 全部版本与 Registry 完整恢复，重新编译输出哈希一致 | M2 |
| AC-API-01 | 目标 Profile 为 v2.41，API Intent 需要读取 Tracker 事件 | 编译结果使用新 Tracker API 端点与当前参数命名；契约中出现任何旧 Tracker 端点均报告 BLOCKING | M3 |
| AC-API-02 | 两个页面都需要患者列表 | OpenAPI 中以一个共享 Schema 组件表示，两个页面通过引用使用 | M3 |
| AC-API-03 | 检查编译出的 OpenAPI | 包含以 baseUrl 为变量的 servers、目标版本支持的 securitySchemes、每个操作的 Registry 引用 | M3 |
| AC-FE-01 | 模式 B 项目完成 | 生成开发包，完整性检查全部通过，页面标记为 FRONTEND_READY | M3 |
| AC-FE-02 | 任一 FRONTEND_READY 页面 | 具备页面规格、API Intent、UI Intent、字段映射、请求响应示例及已部署的元数据依赖 | M3 |
| AC-FE-03 | 检查开发包内容 | 不含任何凭证或真实个人数据；包含连接已初始化环境的说明、示例配置与 CORS 等实例侧配置说明 | M3 |
| AC-CAP-01 | 同一 Blueprint 版本部署到 v2.42 环境 | 系统以 Dhis242Profile 重新编译与校验，差异在 Diff 中展示 | M4 |
| AC-CAP-02 | 连接 v2.39 实例，或尚未提供 Profile 的版本 | 连接测试报告版本不受支持，该环境不能创建 Deployment Run | M2 |
| AC-CAP-03 | 在四个官方在线演示实例上执行 Golden Project 回归 | 每个实例以对应 Profile 完成 VALIDATE 与 COMMIT，运行结束后本次创建的对象已被清理，既有演示对象未被修改 | M4（v2.41 于 M2） |
| AC-ENV-01 | 同一 Blueprint 版本从 TEST 晋级到 UAT | 与环境无关对象的 UID 一致；REFERENCE 条目按 UAT 的发现结果解析 | M4 |
| AC-SEC-01 | 人为修改一条审计记录 | 完整性校验功能报告哈希链断裂位置 | M2 |
| AC-OP-01 | MVP 中改变状态的请求未携带操作者名称 | 返回 400 `OPERATOR_REQUIRED` | M1 |
| AC-RUN-01 | 同一 Blueprint 版本已在 TEST 的 Run 中 VERIFIED，随后产生新版本 | 该 Run 标记 OUTDATED，历史记录不变；向 PRODUCTION 部署前须在新版本上重新完成非生产验证 | M2 |
| AC-RUN-02 | 提交请求发出后既无响应也无任务 ID | 执行进入 MANUAL_CHECK，环境提交锁保持；人工核实后释放 | M2 |

### 27.2 项目完成定义

| 检查项 | 模式 A | 模式 B |
| --- | --- | --- |
| REQUIREMENT_GATE、DOMAIN_GATE、ARCHITECTURE_GATE 通过 | 必需 | 必需 |
| DESIGN_GATE 通过 | 不适用 | 必需 |
| 基线编译完成且环境无关校验无 BLOCKING | 必需 | 必需 |
| 至少一个目标环境的 Deployment Run：环境绑定编译完成，静态与跨产物校验无 BLOCKING，PACKAGE_GATE 通过 | 必需 | 必需 |
| 该 Run：Capability Profile 已持久化，漂移检查通过，Snapshot 已创建，DHIS2 VALIDATE 通过，DEPLOY_GATE 通过，COMMIT 完成 | 必需 | 必需 |
| 该 Run：部署后验证通过（Run 为 VERIFIED），审计完整 | 必需 | 必需 |
| 开发包生成，完整性检查通过，HANDOFF_GATE 通过 | 不适用 | 必需 |

---

## 28. 风险与缓解

| 编号 | 风险 | 后果 | 缓解措施 |
| --- | --- | --- | --- |
| R-01 | LLM 误解业务需求 | 映射错误、返工 | NRR 与来源引用；低置信度转为 Question；REQUIREMENT_GATE 与 DOMAIN_GATE |
| R-02 | 人工审批流于形式 | 错误被批准进入生产 | 按阶段整体审批；Gate 页面给出变更摘要、风险与未决 WARNING；WARNING 必须逐条处理；生产环境多人审批与职责分离 |
| R-03 | 架构或 UID 漂移 | 跨产物不一致、数据断链 | Registry 为唯一 UID 来源；UID 由 DHIS2 官方接口生成并入池；Patch、工作草稿与版本机制；审批绑定哈希 |
| R-04 | 覆盖他人维护的对象 | 破坏既有系统 | Disposition 语义；受控字段；Managed Scope；默认禁止删除 |
| R-05 | 目标环境在设计与提交之间被修改 | 提交结果与预期不符 | 发现结果带时间戳；提交前漂移检查；VALIDATE 与 COMMIT 使用同一载荷 |
| R-06 | DHIS2 版本演进导致 API 失效 | 契约或载荷不可用 | 版本能力 Profile；资源目录逻辑名；弃用与移除清单；在官方在线演示实例上按版本回归；实例升级后强制重新探测 |
| R-17 | 官方在线演示实例为公开共享环境，可能不可用、被重置、被他人修改或被官方下线 | 回归结果不稳定或中断 | 专属 code 前缀与运行后清理；不依赖可变的演示数据；“无法判定”与自动重试；实例清单以配置维护；发版前 7 天内须有全部通过的结果（25.4） |
| R-07 | 敏感数据泄露给外部模型 | 合规事故 | 数据分级；默认拒绝外部提供方；Policy Engine 逐次判定；PHI/PII 永不进入上下文；审计 |
| R-08 | 凭证泄露 | 目标实例被入侵 | Secret Manager；浏览器与模型永不接触凭证；日志脱敏；最小权限与账户分离 |
| R-09 | 提交后无法完整回滚 | 生产环境处于中间状态 | 原子提交；Snapshot 与 Diff；明确的恢复策略；生产备份确认检查项；先在非生产环境验证 |
| R-10 | 长流程状态丢失 | 重复提交或流程中断 | Temporal 持久化工作流；异步任务 ID 持久化；幂等键 |
| R-11 | 提示词或模型升级引入退化 | 质量下降且难以察觉 | Agent 评测集；Golden Project 回归；提示词与模型版本记录 |
| R-12 | 模型不可用或被策略全部拒绝 | 项目停滞 | 人工编写模式（FR-AG-011）；多提供方配置 |
| R-13 | 前端团队信息不足 | 开发返工、沟通成本 | 开发包完整性检查；字段映射与示例；追溯链；M3 退出标准中的独立开发者验证 |
| R-14 | 范围过大导致交付延期 | 无法按期上线 | 分层覆盖（Tier）；M2 聚焦模式 A 与 Tier 1；里程碑以退出标准为门禁；子系统并行开发 |
| R-15 | MVP 不含认证与访问保护 | 未授权人员可操作系统 | 仅在受控范围内使用 MVP；操作者名称全程审计；身份与权限为本期交付 |
| R-16 | 子系统独立开发导致接口不一致 | 集成失败 | 公共 API 随提供方发布并语义化版本管理；测试替身；兼容性测试纳入组装构建 |

---

## 附录 A：术语表

| 术语 | 定义 |
| --- | --- |
| App Blueprint | 项目级聚合根与唯一语义事实源，包含需求、领域、映射、Registry、页面、API Intent、UI Intent 等子模型 |
| Blueprint Patch | 对 Blueprint 的结构化修改提案，进入工作草稿，经 Gate 批准后随草稿提升为新版本 |
| 工作草稿 | 设计阶段内叠加了未批准 Patch 的 Blueprint 状态；冻结后由 Gate 审批 |
| 基线编译 / 环境绑定编译 | 环境无关的编译 / 以目标环境发现结果解析引用与合并 UPDATE 对象后的编译 |
| Deployment Run | 把一个 Blueprint 版本部署到一个目标环境的完整过程 |
| 设计基准环境 | 项目目标环境中用于 DISCOVERY 与映射的环境 |
| 环境绑定 | Registry 条目在某个环境中的 UID 与观测基准 |
| OUTDATED | 已完成的 Deployment Run 所基于的 Blueprint 版本已不是当前版本 |
| 操作者 | 启用认证时由会话身份给出；未启用认证时为请求声明的操作人名称 |
| NRR | 规范化需求表示，所有输入统一转换后的结构化需求 |
| Mapping Model | 领域元素到 DHIS2 结构的映射模型 |
| Metadata Registry | DHIS2 对象身份与引用的规范化索引，UID 的唯一来源 |
| Disposition | Registry 条目的处置方式：CREATE、UPDATE、REUSE、REFERENCE、EXTERNAL、DEPRECATED |
| Managed Scope | 某项目在某环境中负责创建、修改或停用的对象集合 |
| 受控字段 | UPDATE 对象中由本项目负责维护的字段 |
| API Intent | 页面所需 DHIS2 API 调用的结构化描述，由 OpenAPI Compiler 编译 |
| UI Intent | 页面结构与行为的结构化描述，由 UI Spec Compiler 编译 |
| 版本能力 Profile | 封装某个 DHIS2 版本行为差异的代码与数据 |
| Capability Profile | 版本能力 Profile 与目标实例实际探测结果合成的环境能力记录 |
| Discovery | 读取目标环境既有元数据、版本与能力的过程 |
| Factory Snapshot | 系统保存的目标环境相关对象状态记录，不是 DHIS2 原生事务快照或数据库备份 |
| 漂移（Drift） | 目标环境中的对象在发现之后被他人修改 |
| Gate | 持久化的人工审批节点，绑定内容哈希 |
| 人工编写模式 | 不经 Agent、由用户直接编写阶段结构化内容并以 Patch 提交的方式 |
| Golden Project | 用于全链路回归的固定业务项目 |
| FRONTEND_READY | 页面在开发包中具备全部规格且依赖已部署验证的状态 |

---

## 附录 B：需求追溯矩阵

| 需求领域 | 需求编号 | 验收标准 | 回归项目 |
| --- | --- | --- | --- |
| 工作流与状态 | FR-WF-001 至 FR-WF-012 | AC-WF-01、AC-WF-02、AC-WF-03、AC-RUN-01、AC-OP-01 | GP-12 |
| Human Gate | FR-GATE-001 至 FR-GATE-009 | AC-GATE-01 至 AC-GATE-04 | 全部 |
| 数据分级与策略 | FR-POL-001 至 FR-POL-008 | AC-POL-01、AC-POL-02 | — |
| Agent 运行时 | FR-AG-001 至 FR-AG-011；FR-IF-008 | AC-AG-01、AC-AG-02、AC-BP-02 | 全部 |
| 需求输入 | FR-REQ-001 至 FR-REQ-009 | AC-REQ-01、AC-REQ-02 | GP-01 至 GP-06 |
| Blueprint 与 Registry | FR-BP-001 至 FR-BP-009；FR-REG-001 至 FR-REG-009 | AC-BP-01、AC-REG-01 至 AC-REG-04、AC-DATA-01 | GP-06 |
| 元数据覆盖 | FR-COV-001 至 FR-COV-012 | AC-COV-01、AC-COV-02 | GP-01 至 GP-05、GP-08 |
| 环境发现与映射 | FR-DOM-001 至 FR-DOM-002；FR-DSC-001 至 FR-DSC-005；FR-MAP-001 至 FR-MAP-004 | AC-REG-03、AC-EXE-03 | GP-06、GP-07 |
| Compiler | FR-CMP-001 至 FR-CMP-011 | AC-REG-01、AC-REG-04、AC-COV-01 | 全部 |
| 校验 | FR-VAL-001 至 FR-VAL-004 | AC-VAL-01、AC-VAL-02 | 全部 |
| 环境与能力 Profile | FR-ENV-001 至 FR-ENV-007；FR-CAP-001 至 FR-CAP-005 | AC-CAP-01 至 AC-CAP-03、AC-ENV-01 | GP-07 |
| 回归测试环境 | FR-TST-001 至 FR-TST-005 | AC-CAP-03 | 全部 |
| 执行与 Snapshot | FR-EXE-001 至 FR-EXE-012；FR-SNP-001 至 FR-SNP-003 | AC-EXE-01 至 AC-EXE-06、AC-AUD-01、AC-RUN-02 | 全部 |
| 页面、API、UI Intent | FR-PG-001 至 FR-PG-002；FR-API-001 至 FR-API-005；FR-UII-001 至 FR-UII-002 | AC-API-01 至 AC-API-03 | GP-09 |
| 前端开发包 | FR-FE-001 至 FR-FE-006 | AC-FE-01 至 AC-FE-03 | GP-09 |
| Factory UI | FR-UI-001 至 FR-UI-012 | AC-WF-01、AC-AG-01 | GP-09 |
| 外部接口 | FR-IF-001 至 FR-IF-008 | AC-GATE-02、AC-EXE-05 | — |
| 安全与审计 | FR-SEC-001 至 FR-SEC-014 | AC-AUD-01、AC-GATE-04、AC-SEC-01 | — |
| 数据管理 | FR-DATA-001 至 FR-DATA-007 | AC-DATA-01 | — |
| 部署 | FR-DEP-001 至 FR-DEP-006 | — | — |

各子系统 SRS 以来源标记把本表中的需求分解到子系统需求，并给出子系统级验收标准。

---

## 附录 C：参考资料

实现阶段必须以目标版本的官方文档和目标实例的实际探测结果为准，并持续核对以下来源：

| 资料 | 链接 | 用途 |
| --- | --- | --- |
| DHIS2 官方在线演示实例 | https://play.im.dhis2.org/stable-2-40-12 、https://play.im.dhis2.org/stable-2-41-10 、https://play.im.dhis2.org/stable-2-42-6 、https://play.im.dhis2.org/stable-2-43-1 | 各受支持版本的回归测试对象（25.4） |
| DHIS2 v2.41 Tracker API | https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-241/tracker.html | 新 Tracker 端点；旧端点在 v42 移除的说明 |
| DHIS2 v2.41 Tracker（已弃用 API） | https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-241/tracker-deprecated.html | 旧端点清单 |
| DHIS2 2.41 发布说明 | https://github.com/dhis2/dhis2-releases/tree/master/releases/2.41 | Tracker 参数与命名统一、多 UID 参数改用逗号分隔 |
| DHIS2 Metadata API | https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-242/metadata.html | 元数据导入模式与参数 |
| DHIS2 v2.41 Data Store | https://docs.dhis2.org/cs/develop/using-the-api/dhis-core-version-241/data-store.html | DataStore API |
| DHIS2 App Platform | https://developers.dhis2.org/docs/app-platform/getting-started/ | 自定义应用开发 |
| DHIS2 App Runtime | https://developers.dhis2.org/docs/app-runtime/getting-started/ | 数据访问与认证 |
| DHIS2 Design System / UI Library | https://developers.dhis2.org/design-system/ | `@dhis2/ui` 组件 |
| OpenAPI Specification | https://spec.openapis.org/oas/ | OpenAPI 3.1 |
| JSON Patch（RFC 6902） | https://www.rfc-editor.org/rfc/rfc6902 | Blueprint Patch 语义 |
| WCAG 2.1 | https://www.w3.org/TR/WCAG21/ | 无障碍要求 |
| Scalar API Reference | https://scalar.com/products/api-references/integrations/react | API 文档查看器 |
| Temporal Documentation | https://docs.temporal.io/ | 持久化工作流 |
