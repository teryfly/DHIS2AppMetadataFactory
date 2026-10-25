# DHIS2 App Studio / Metadata Factory
# S2 Blueprint 与构建（Blueprint & Build）子系统需求规格说明书

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 子系统 | S2 Blueprint 与构建 |
| 文档版本 | V1.1.5 |
| 上位文档 | 总体 SRS V1.4.0；《00 总体架构与集成约定》V1.3.0（下称“00 文档”） |
| 交付形式 | Python 3.12+ 库（建议包名 `factory_blueprint`），发布到私有 PyPI |
| 依赖 | S0 公共 API；S4 公共 API（只读查询）及其 CapabilityProfile、DiscoveryResult 内容格式定义 |
| 数据库 schema | `bp` |
| 需求编号前缀 | BP- |
| 来源标记 | 【直引】【裁剪】【调整】【新增】，定义见 00 文档 0.3 |

**V1.1.5（对照 0.2.0 实现反向校准：依赖图与开发包读取、构建参数持久化、导出导入与清理、Registry 删除与 UID 池、codePrefix、公共 API 表）** 本版以 `factory_blueprint` 0.2.0 的 `api.__all__` 与迁移 0001～0003 为准，校正第 3.3、3.5、3.7、3.8、3.11、3.12 节与第 5、7 章；新增 BP-CMP-021a（元数据包文件清单读取）、BP-API-002/003（Fake 预设位与依赖注入）。实现已交付 M1～M4 范围，M5 的 DataStore 编译器与前端起步工程仍未交付。

---

## 1. 目的与范围

### 1.1 目的

S2 是项目语义的唯一事实源与全部确定性产物的生成者：维护 App Blueprint（含 Registry 与映射决策）的版本与工作草稿，接受并校验 Patch，判定变更级别与影响范围，分配 UID 与 code，并从 Blueprint 确定性地编译元数据包、OpenAPI、UI Specification、Fixture 与前端开发包，执行本地的三层校验。本文档是 S2 开发组的唯一需求输入。

### 1.2 范围内

1. Blueprint JSON Schema 与 Schema 版本迁移；
2. Blueprint 版本、工作草稿、Patch；
3. 变更级别判定与影响分析；
4. Metadata Registry：Disposition、受控字段、UID 与 code 分配、环境绑定、Managed Scope 计算、跨项目所有权冲突检查；
5. 映射决策与架构决策的结构与完整性；
6. 类型描述器（Tier 1～3）与依赖图；
7. 编译：基线编译、环境绑定编译、各 Compiler、复现；
8. 校验：Blueprint 校验、产物静态校验（含本地表达式校验）、跨产物一致性校验；
9. 对象级 Diff：Blueprint 版本间、Registry 版本间、环境当前状态与编译结果之间；
10. 前端开发包生成与完整性检查；
11. Blueprint 导出与导入、项目数据清理；
12. 为人工编写模式提供分节 Schema。

### 1.3 范围外

| 不负责 | 所属 |
| --- | --- |
| 工作流与阶段状态、Gate 与审批、Question 存储、Change Request 状态 | S1 |
| 产物存储、审计链、事务、幂等存储 | S0 |
| 调用 LLM、解析原始输入、生成 NRR 内容与 Patch 内容 | S3 |
| 访问 DHIS2、发现、Capability Profile 的生成、执行计划、VALIDATE / COMMIT、部署后验证、Snapshot 比对、两个环境部署状态的比对 | S4 |

S2 禁止：调用 LLM；写入或修改 DHIS2；调用 S1 或 S3；在编译中读取系统时钟或随机源；自动修复任何产物（总体 SRS C-07）。

---

## 2. 领域概念与定义

| 概念 | 定义 |
| --- | --- |
| App Blueprint | 项目级聚合根与唯一语义事实源（总体 SRS 5 章）；由若干分节组成 |
| 分节（Section） | Blueprint 的顶层部分：project、requirements、domain、useCases、actors、organizationModel、accessModel、architecture、mapping、registry、pages、apiIntents、uiIntents、rules、fixtures、environmentConstraints、generationPolicy |
| Blueprint 版本 | 不可变的已批准 Blueprint 快照，内容以 BLUEPRINT 产物存于 S0 |
| 工作草稿（Draft） | 基于当前版本、叠加 PROPOSED Patch 的可变状态；每个草稿有一个阶段范围 |
| 阶段范围 | 草稿允许修改的分节集合，由所属设计阶段决定（附录 A） |
| 冻结（Freeze） | 把草稿当前内容物化、校验并暂存为待提升内容的操作；产生草稿哈希 |
| Patch | 对 Blueprint 的结构化修改提案：RFC 6902 操作 + 业务元数据（总体 SRS 5.3） |
| 变更级别 | Cosmetic、Minor、Major（总体 SRS 5.2） |
| 影响集 | 一次变更影响的阶段、分节、Registry 条目、页面与产物类型 |
| Registry 条目 | DHIS2 对象身份与引用的规范化索引项（总体 SRS 5.5） |
| 环境绑定 | Registry 条目在某个环境中的 UID 与观测基准（D-02） |
| Managed Scope | 某项目在某环境中 Disposition 为 CREATE、UPDATE、DEPRECATED 的对象集合（D-03） |
| 类型描述器 | 描述一种 DHIS2 元数据对象类型的数据与规则（总体 SRS FR-COV-001） |
| 构建上下文 | 编译的外部输入：构建时间、随机种子、请求者；由调用方提供 |
| 构建记录 | 产物的可复现依据：Blueprint 版本与哈希、Compiler 与模板版本、Capability Profile 与发现结果哈希、构建上下文、输出哈希 |
| 基线编译 / 环境绑定编译 | 见 00 文档术语 |
| 校验问题（Issue） | 一条校验结果，含问题 ID、规则 ID、级别、对象、位置、说明、来源阶段、建议处理 |

---

## 3. 功能需求

### 3.1 Blueprint Schema（BP-SCH）

- **BP-SCH-001**【直引】（FR-BP-001）Blueprint 必须有正式 JSON Schema，并随 `blueprintSchemaVersion` 版本化。
- **BP-SCH-002**【调整】（5.1、D-15）`project` 分节包含：交付模式、项目类型、目标 DHIS2 版本约束、默认语言与翻译语言。它们是这些信息的唯一存储位置。
- **BP-SCH-003**【调整】（5.1、D-03）`generationPolicy` 包含：code 前缀与命名规范、UID 池补充批量与低水位、环境绑定匹配规则的按类型覆盖、默认语言与翻译语言、删除策略、决策置信度阈值、输入抽取置信度阈值（供 S3 使用，总体 SRS FR-REQ-005 的“项目阈值”）。
- **BP-SCH-004**【直引】（FR-BP-002）Blueprint 禁止内嵌原始文档、图片、完整 Markdown 或完整元数据 JSON，只能通过产物引用关联。
- **BP-SCH-005**【直引】（FR-BP-003）每个条目必须有稳定的 `businessKey`，并记录来源（来源产物 ID 与位置、决策 ID）。
- **BP-SCH-006**【新增】S2 必须为每个分节提供独立的 JSON Schema，供人工编写模式的表单与 JSON 编辑器使用（总体 SRS FR-UI-011），也供 S3 约束 Agent 输出。理由：人工编写与 Agent 输出须遵循同一 Schema。
- **BP-SCH-007**【新增】Schema 升级时，S2 必须提供从旧 Schema 版本到新版本的迁移函数；已有项目在下一次草稿打开时迁移，迁移结果作为 SYSTEM 作者的 Patch 进入草稿并随 Gate 审批。理由：长期项目会跨越 Schema 版本。
- **BP-SCH-008**【新增】提供分节目录查询：当前与可用的 Schema 版本、每个分节的名称、条目容器路径与键字段（整对象型分节标明无键）。理由：人工编写界面需先获得分节清单再按分节取 Schema（总体 SRS FR-UI-011）。

### 3.2 版本（BP-VER）

- **BP-VER-001**【直引】（5.2）每个版本记录：blueprintId、version、contentHash、parentVersion、status、sourceArtifacts、decisionIds、createdBy、createdAt；【调整】另记录 schemaVersion、changeLevel、artifactId（内容所在 BLUEPRINT 产物）。
- **BP-VER-002**【调整】（D-04、D-12）版本内容只存一份，即 S0 中的 BLUEPRINT 产物；`bp` 中只保存版本索引与为查询建立的规范化索引（Registry 条目、决策、页面键等，见第 5 章）。
- **BP-VER-003**【调整】（FR-BP-004、P-05）版本不可变；任何修改都经草稿与 Patch 产生新版本。
- **BP-VER-004**【调整】（D-04）版本号 `MAJOR.MINOR.PATCH` 由 S2 按变更级别自动递增：Major 增主版本、Minor 增次版本、Cosmetic 增修订号。初始化产生 `0.1.0`。
- **BP-VER-005**【新增】正式版本只在以下调用中产生：提升草稿、交付模式升级、导入。理由：00 文档 3.3，使 S1 能同步执行失效传播。
- **BP-VER-006**【直引】（NFR-CAP-005）单项目版本数不设上限，历史版本全部可查询。

### 3.3 工作草稿与 Patch（BP-DRF）

- **BP-DRF-001**【新增】每个项目同一时刻最多一个打开的草稿；草稿以当前版本为基准，带阶段范围（附录 A）。打开已存在的草稿返回该草稿（幂等）。理由：D-04；设计阶段串行执行。
- **BP-DRF-002**【调整】（5.3）Patch 结构：patchId、baseVersion、作者（kind：AGENT / USER / SYSTEM；agentRunId 或操作者名称）、decisionIds、operations（RFC 6902）、rationale、声明的影响（可空，仅供参考）。
- **BP-DRF-003**【调整】（FR-BP-006）Patch 的 baseVersion 必须等于草稿的基准版本；不一致时拒绝，并返回一个“需人工合并”的 Question 数据（由调用方交给 S1 保存）。
- **BP-DRF-004**【直引】（FR-BP-007、FR-BP-008）来自 Agent 与来自用户的 Patch 走相同校验；通过校验的 Patch 状态为 PROPOSED 并叠加进草稿。
- **BP-DRF-005**【新增】Patch 校验（任一失败即 REJECTED，并返回问题列表）：
  1. 操作路径只触及草稿阶段范围内的分节；
  2. 应用后分节内容通过 JSON Schema；
  3. businessKey 唯一且引用可解析；
  4. 禁止在 Patch 中直接写入或修改 UID（UID 只由 S2 分配，总体 SRS P-04）；
  5. 当前版本已通过 ARCHITECTURE_GATE（即版本中映射已冻结）时，触及 mapping、registry、architecture 分节的 Patch 必须携带已批准的 Change Request ID（总体 SRS FR-MAP-004、AC-BP-02）；
  6. 允许以 RFC 6902 `remove` 直接删除条目（不要求先置为 DEPRECATED）；删除任何条目均为 Major（附录 B），被删 Registry 条目的 UID 在提升时置为 RETIRED（BP-REG-006），删除后遗留的引用按第 3 条判为不可解析。
  理由：子系统实现需要完整、可判定的校验规则清单（总体 SRS FR-BP-006、FR-MAP-004）。
- **BP-DRF-006**【新增】Patch 状态：PROPOSED、REJECTED、APPLIED（已进入版本）、DISCARDED（草稿被重置）。
- **BP-DRF-007**【新增】提供草稿重置：草稿内全部 PROPOSED Patch 置为 DISCARDED，草稿回到基准版本。供阶段重跑使用。理由：CP-WF-012。
- **BP-DRF-008**【新增】提供草稿冻结（外部 I/O 函数）：
  1. 物化草稿内容并计算草稿哈希；
  2. 执行 Blueprint 校验（3.9.2）与针对调用方给出的目标环境列表的 UID / code 冲突检查（3.5）；冻结报告只保留来源阶段不晚于本草稿阶段的问题（草稿本就故意不完整，后续阶段的问题留给该阶段的 Gate）；
  3. 把内容暂存到 S0；暂存前把其中 Registry 条目置为 APPROVED，使 `content_hash`、`frozen_hash` 与暂存字节描述同一份内容；
  4. 返回草稿哈希、暂存句柄与问题列表。
  存在 BLOCKING 问题时冻结结果标记为不可提升。理由：Gate 需要绑定稳定哈希；产物暂存属外部 I/O，不能在提升事务中执行（00 文档 6.2）。
- **BP-DRF-009**【新增】提供草稿提升（在调用方工作单元中执行）：校验草稿当前哈希等于期望哈希且冻结结果可提升 → 判定变更级别 → 分配版本号 → 以暂存句柄登记 BLUEPRINT 产物 → Patch 置为 APPLIED → 更新规范化索引 → 计算影响集 → 写审计 → 返回新版本号、变更级别、受影响阶段集合与影响集。以幂等键去重。理由：CP-GATE-014。
- **BP-DRF-010**【直引】（NFR-CON-001）基于同一基准版本只能有一个草稿被提升；其他草稿（如存在）必须重新基于新版本。
- **BP-DRF-011**【新增】提供按项目查询当前活动草稿：返回草稿 ID、基准版本、所属阶段、阶段范围、状态、当前哈希、冻结哈希、是否可提升、PROPOSED Patch 数；无打开草稿时返回空。草稿条目内容通过 `get_entries` 的 `EntrySelector.source=DRAFT` 按范围读取，不提供整份草稿内容的读取。理由：S1 需随阶段视图返回活动草稿 ID（S1 CP-UI-002）；整份读取在大规模项目下无必要。

### 3.4 变更级别与影响分析（BP-IMP）

- **BP-IMP-001**【直引】（FR-BP-005）变更级别由 S2 根据草稿与基准版本的差异自动判定，调用方不得指定或降级。
- **BP-IMP-002**【调整】（5.2）判定规则（取全部变更中的最高级别，细则见附录 B）：
  - Cosmetic：描述、显示名、简称、翻译、注释等不影响结构的字段；
  - Minor：新增兼容对象（新数据元、新页面、新规则等），不改变既有对象的类型、UID、关系；交付模式由 A 升级为 B；
  - Major：改变业务实体、DHIS2 对象类型、UID、关系、Program 结构、权限模型、API 语义，或改变既有条目的 Disposition。
- **BP-IMP-003**【调整】（FR-WF-003、6.5）受影响阶段的计算：每个分节对应其所属的最早设计阶段（附录 A），影响集包含该阶段及其之后全部阶段；Major 时至少包含 MAPPING 及其之后阶段。
- **BP-IMP-004**【调整】（6.5 用途 2）影响集还必须包含：受影响的 Registry 条目（沿依赖图向下游传递）、受影响的页面与 operationKey、需重新生成的产物类型。
- **BP-IMP-005**【调整】（FR-WF-007）为 Change Request 提供影响分析：输入一组 businessKey（及可选的拟定变更描述），输出受影响的阶段、Registry 条目、页面与产物类型；S2 不负责计算已部署环境（由 S1 补充）。

### 3.5 Metadata Registry（BP-REG）

- **BP-REG-001**【调整】（5.5、D-02）Registry 条目字段：businessKey、dhis2Type、uid（CREATE 与 UPDATE 为全环境一致的 UID）、code、disposition、managed、controlledFields、status（PROPOSED / APPROVED）、decisionId、以及设计基准环境的观测基准（sourceEnvironment、observedLastUpdated、observedHash、discoveryResultId）。
- **BP-REG-002**【直引】（5.5.1）Disposition 语义：CREATE、UPDATE、REUSE、REFERENCE、EXTERNAL、DEPRECATED，是否进入载荷与是否属于 Managed Scope 按总体 SRS 5.5.1 表。
- **BP-REG-003**【直引】（FR-REG-001）UPDATE 条目必须声明受控字段；受控字段必须属于该类型描述器允许受控的字段集合。
- **BP-REG-004**【直引】（FR-REG-002）REUSE、REFERENCE、UPDATE 条目必须记录观测基准，用于漂移检测。
- **BP-REG-005**【直引】（FR-REG-003）同一业务概念禁止两个 UID；同一 UID 禁止对应两个业务概念。
- **BP-REG-006**【调整】（FR-REG-004、FR-REG-006）CREATE 条目的 UID 在条目首次进入草稿时分配并持久化，此后重复编译不得产生新 UID。【新增】所属 Patch 被拒绝或丢弃时，已分配的 UID 置为 RETIRED，永不复用；条目在提升时被直接删除（RFC 6902 `remove`）的，其 UID 置为 RETIRED；置为 DEPRECATED 的，其账本状态置为 DEPRECATED。三种状态都保留在账本中，永不复用。理由：分配发生在批准前，需要明确未批准 UID 的去向。
- **BP-REG-007**【直引】（FR-REG-005）UID 必须为 11 个字符，首字符为字母，其余为字母或数字。
- **BP-REG-008**【调整】（FR-REG-007）UID 由目标 DHIS2 实例官方提供的 UID 生成接口产生（经 S4 只读调用，逻辑资源 `system.id`），分配后持久化。分配时检查：本项目 Registry 与本 Factory 全部项目中无相同 UID；冻结与环境绑定编译时，调用 S4 检查每个相关环境中不存在同 UID 的其他对象。
- **BP-REG-008a**【新增】S2 为每个项目维护 UID 池：
  1. 补充池是外部 I/O 函数：调用 S4 在指定环境（调用方传入设计基准环境）生成一批 UID，校验格式（BP-REG-007）与本 Factory 内唯一性（已分配或已在任何项目池中的 UID 一律丢弃）后入池；批量与低水位由调用方传入，默认各 200 与 50（`generationPolicy` 中的值为调用方取值来源，S2 不自行读取版本内容）；
  2. 分配在事务内从池中按取得顺序（FIFO）取出，不访问 DHIS2；分配记录随 UID 保存铸造它的来源环境；
  3. 池不足时 Patch 提交以可重试错误 `UID_POOL_EXHAUSTED` 拒绝，调用方补充池后重试；补充函数返回补充后的池内可用数量；
  4. 池中未使用的 UID 不属于任何对象，项目数据清理时随项目一并丢弃（池按项目整体删除，不提供按环境单独清池的接口）。池内 UID 的 `sourceEnvironment` 只用于记录来源环境，不构成环境绑定。
  理由：UID 分配发生在事务内的 Patch 提交中，而调用 DHIS2 属于外部 I/O，不能放进事务（00 文档 6.2）。
- **BP-REG-009**【调整】（FR-REG-008）每个托管对象按 `generationPolicy` 的 `codePrefix` 与 `namingConvention` 生成稳定 code（当前支持 `PREFIX_TYPE_KEY`（默认）、`PREFIX_TYPE_INDEX`、`TYPE_KEY`）；项目内同类型唯一。需要新分配 UID 的 CREATE 条目由 S2 生成 code，作者随条目提供的 code 不被采信；已有观测 UID 的条目按现有 code 进入 Registry。冻结与环境绑定编译时，调用 S4 检查目标环境同类型对象中无同 code 的其他对象。
- **BP-REG-010**【新增】环境绑定（D-02）：
  1. 设计基准环境的绑定随 MAPPING 阶段的 Patch 进入草稿并随 ARCHITECTURE_GATE 批准；
  2. 其他环境的绑定在该环境的环境绑定编译中，依据该环境发现结果自动解析：REUSE / REFERENCE 默认按 code 匹配，其次按设计基准环境的 UID 匹配；UPDATE 必须按 UID 匹配；
  3. 无匹配或多重匹配时产生 BLOCKING 问题；用户可通过人工设置环境绑定解决（记录操作者与说明，写审计），人工设置不产生 Blueprint 新版本；
  4. 环境绑定按（项目、Blueprint 版本、环境、businessKey）保存，不属于版本内容。
  理由：总体 SRS FR-ENV-004 要求同一版本部署到多个环境，环境相关引用不能进入版本内容。
- **BP-REG-011**【新增】提供 Managed Scope 计算：给定项目、版本与环境，返回 Disposition 为 CREATE、UPDATE、DEPRECATED 的对象及其该环境的 UID。理由：D-03；S4 的 Snapshot 与部署后验证需要它（经 S1 传递）。
- **BP-REG-012**【新增】跨项目所有权冲突检查（D-03）：同一环境中同一 UID 被两个项目声明为 CREATE 或 UPDATE 时，报告 BLOCKING。检查在冻结与环境绑定编译时执行。
- **BP-REG-013**【直引】（FR-COV-007）OrganisationUnit 默认 REFERENCE；只有 Greenfield 项目且存在经 ARCHITECTURE_GATE 批准的对应决策时允许 CREATE。
- **BP-REG-014**【直引】（FR-COV-008）允许 CREATE / UPDATE UserRole 与 UserGroup；禁止通过元数据载荷创建带密码的用户账号。
- **BP-REG-015**【新增】提供环境绑定读取：按项目、版本与一个或多个环境键返回每个条目的绑定，字段包括环境键、businessKey、UID、`observedHash`、`observedLastUpdated`、`discoveryResultId`、解析方式（AUTO / MANUAL）、设置人、说明、是否待重新解析；`get_registry` 每次只附带一个环境的绑定（`environment_bindings` 为该环境的 UID 映射），跨环境视图用本读取函数。理由：D-02 的观测基准判定需要 `observedHash` 与 `discoveryResultId`，界面需一次看到跨环境绑定。

### 3.6 映射与决策（BP-DEC）

- **BP-DEC-001**【直引】（5.4）每条映射包含：领域元素 businessKey、选定的 DHIS2 表达、备选方案及放弃理由、风险与置信度、关联决策 ID 与审批记录引用。
- **BP-DEC-002**【直引】（FR-MAP-001）存在多种合理表达的领域元素，必须有映射决策：决策 ID、领域元素、选定表达、至少一个备选方案、取舍理由、风险、置信度、影响的下游产物。
- **BP-DEC-003**【直引】（FR-MAP-002）映射阶段必须为每个 Registry 条目确定 Disposition；UPDATE 必须列出受控字段并说明影响范围。
- **BP-DEC-004**【调整】（FR-MAP-003）置信度低于 `generationPolicy` 阈值或影响级别为 HIGH 的决策，必须带“用户确认”记录（Question ID 与回答人，或人工 Patch 的操作者）；缺失时冻结报告 BLOCKING。理由：S2 不创建 Question，以数据规则保证“禁止 Agent 自行采纳”。
- **BP-DEC-005**【直引】（FR-DOM-002）没有已批准领域模型（DOMAIN_GATE 对应版本）时，禁止在草稿中写入映射。
- **BP-DEC-006**【新增】提供按 ID 查询决策（含理由、备选、风险、置信度、影响级别、确认记录、影响的产物），供 S1 的 Gate 说明使用；`decision_ids` 为空时按项目与版本列出全部决策（按 decisionId 排序，当前不分页）。影响级别为枚举 `LOW` / `MEDIUM` / `HIGH`。理由：CP-GATE-019；界面检查器需浏览决策。

### 3.7 类型描述器与依赖图（BP-TD）

- **BP-TD-001**【直引】（FR-COV-001）每个元数据对象类型以类型描述器注册：DHIS2 逻辑资源名、适用版本范围、依赖字段、可受控字段、编译模板、校验规则、所属 Tier。新增对象类型只需新增描述器、模板与规则（总体 SRS NFR-MNT-001）。
- **BP-TD-002**【直引】（6.1）Tier 1 描述器在 M2 交付，Tier 2、Tier 3 在 M5 交付；启用哪些 Tier 由 S2 配置决定。
- **BP-TD-003**【直引】（FR-COV-002）遇到未注册或未启用的对象类型时，编译以 BLOCKING 问题中止，禁止静默忽略。
- **BP-TD-004**【直引】（FR-COV-003～006）编译必须显式处理隐式依赖：DataElement 必须引用 CategoryCombo（未指定时引用目标环境 default CategoryCombo，Disposition 为 REFERENCE）；Program 必须声明 TrackedEntityType 或为 Event Program，并声明 CategoryCombo、accessLevel 与共享；TrackedEntityAttribute 区分挂载于 TrackedEntityType 还是 Program；全部托管对象的共享由 `accessModel` 编译，使用目标版本支持的共享格式。
- **BP-TD-005**【直引】（FR-COV-009～011）可显示字段支持多语言；按目标版本格式输出翻译；attributeValues 通过 Registry 引用 Attribute 定义并校验适用对象类型。
- **BP-TD-006**【直引】（6.5）编译器为每个元数据包构建依赖图，节点为 Registry 条目，边为字段级引用；用途：引用完整性校验、变更影响分析、Diff 与审核展示、编译诊断、删除安全检查。
- **BP-TD-007**【直引】（FR-COV-012）依赖图不用于决定导入顺序；完整元数据包作为一次逻辑导入提交。
- **BP-TD-008**【新增】类型描述器与编译模板以数据文件形式随 S2 包发布并版本化；描述器版本进入构建记录。理由：复现需要。
- **BP-TD-009**【新增】（总体 SRS 6.5、FR-UI-005）提供依赖图读取，作为依赖图的唯一提供方：
  1. 按构建 ID（基线编译或环境绑定编译）读取该构建产出的依赖图；依赖图是**元数据对象图**，节点只包含 Registry 条目（BP-TD-006），不包含页面、API Intent、UI Intent、决策等非元数据条目（这些条目的追溯关系属于来源追溯，不属于依赖图）。返回模型还带 buildId、projectId、blueprintVersion、构建类别（BASELINE / ENVIRONMENT）、environmentKey（基线为空）与图格式 `schemaVersion`；
  2. 节点字段：businessKey、DHIS2 类型、UID（基线取池分配，环境绑定编译取该环境绑定）、Disposition、是否托管、是否在本构建的载荷中（`in_payload`：该对象作为 CREATE 或 UPDATE 写入本构建的元数据载荷时为真，仅被引用时为假）、入度、出度；边字段：引用方与被引用方 businessKey、引用字段、两端 DHIS2 类型、`edge_kind`（当前只产生字段级引用 `ref`；`implicit` / `sharing` / `attribute` 待相应描述器、accessModel 与 attributeValues 生产者接入）；
  3. `orphans`：在**完整图**上入度与出度均为 0 的节点；`unresolved_registry_refs`：元数据字段引用的目标 businessKey 不在 Registry 中的引用（引用方、引用字段、目标 businessKey）。入度、出度、孤立与未解析判定一律在完整图上计算，筛选只裁剪返回内容：`orphans` 与 `unresolved_registry_refs` 只保留筛选后仍在视图内的条目，但其取值与完整图一致，不重新计算；
  4. 支持按 DHIS2 类型与 businessKey 筛选：两个维度同时给出时取交集（AND），同一维度内任一命中即可（OR），`None` 或空集合不收窄；保留的节点带来其一跳邻接边（边的另一端可以落在视图之外）；
  5. 只有状态为 COMPLETED 的基线或环境绑定构建才有依赖图：构建不存在时抛出 `NOT_FOUND`（`details.reason = NOT_EXIST`）；构建存在但未完成、不是基线／环境绑定构建（如 HANDOFF 构建）或未产出依赖图时抛出 `NOT_FOUND`（`details.reason = NOT_GENERATED`）；产物已被清除时仍返回 `ARTIFACT_PURGED`，不折叠为 `NOT_FOUND`。只读产物内容，按外部 I/O 函数声明。
  `unresolved_registry_refs` 只描述图上的引用断点，**不是校验问题**；跨分节的引用问题（pageKey、intentKey、decisionId 等）以校验问题为准（BP-VAL-030、BP-VAL-005）。理由：依赖图文件格式归 S2 所有，调用方不得自行计算或解析；统一算法与命名，避免多处实现不一致。

### 3.8 编译（BP-CMP）

#### 3.8.1 通用要求

- **BP-CMP-001**【直引】（12 章引言、FR-CMP-001）Compiler 是纯函数：相同的 Blueprint 版本、Compiler 版本、模板版本、Capability Profile、发现结果与构建上下文，必须产出字节级相同的产物；禁止调用 LLM、读取系统时钟或随机源、访问网络（S4 只读检查除外，其结果不进入产物内容）。
- **BP-CMP-002**【直引】（FR-CMP-002）对象排序、字段顺序、文件路径、命名与引用形式稳定；JSON 使用 S0 规范化序列化。
- **BP-CMP-003**【直引】（FR-CMP-003）每个产物附构建记录：Blueprint 版本与哈希、Compiler 版本、模板与描述器版本、Capability Profile 产物 ID 与哈希、发现结果产物 ID 与哈希、构建上下文、输出哈希。【新增】记录还保存复现所需的编译参数 `compile_parameters`（`designEnvironment`、`changeRequestId`、`deletionCandidates`），以及 S4 探测结果 `s4_probe`（只存库，永不进入产物字节）。`compiler_version` 当前为 `0.2.0`（0.2.0 起依赖图带出度／孤立／未解析字段），`package_template_version` 仍为 `0.1.0`（开发包模板本轮未变），描述器与编译模板版本为 `1`。
- **BP-CMP-004**【直引】（FR-CMP-004）提供复现：以构建记录重新编译，输出哈希必须一致，否则报告缺陷。【新增】复现按记录重放 `compile_parameters`，只比较该构建类别前缀下的产物文件哈希与包哈希（产物 ID 每次编译都会变，不参与比较；载荷哈希作为结果返回，但构建记录没有可比较的期望值）；没有可比产物时不得报告一致。`compiler_version` 升级后，旧版本编译器写下的构建记录在复现时会报告不一致——这正是复现应当说明的，而不是让旧记录静默通过。
- **BP-CMP-005**【调整】（C-06）编译只接受已批准的 Blueprint 版本，不接受草稿。
- **BP-CMP-006**【新增】编译函数为外部 I/O 函数（读写产物内容），在调用方事务之外执行；产物登记与构建记录登记在编译函数内部的短事务中完成。理由：00 文档 6.2。
- **BP-CMP-007**【新增】产物数据分级：编译产物的分级取其来源（Blueprint 版本、Capability Profile、发现结果）的最高级别（总体 SRS FR-POL-001）。

#### 3.8.2 基线编译

- **BP-CMP-010**【新增】输入：已批准版本、设计基准环境的 Capability Profile（可空）、构建上下文。输出：OPENAPI 与 API 参考（模式 B）、UI_SPECIFICATION 与页面文档（模式 B）、元数据基线（全部 CREATE 对象的完整定义，环境相关引用以占位表示，仅供校验与审核，不用于执行；内含 `dependency-graph.json`）、VALIDATION_REPORT（Blueprint 校验与环境无关的跨产物校验）。理由：D-01；在进入任何环境前发现环境无关的问题。

#### 3.8.3 环境绑定编译

- **BP-CMP-020**【新增】输入：已批准版本、环境键、该环境的发现结果产物、Capability Profile 产物、可选的已批准 CR ID、可选的删除候选列表（`{businessKey, uid}`）、可选的设计基准环境键、构建上下文。步骤：解析环境绑定（BP-REG-010）→ UID / code / 所有权冲突检查 → 运行各 Compiler → 生成 Diff。删除候选与设计基准环境是调用方给出的参数，且必须与构建记录一起持久化，复现才能重放（BP-CMP-003、BP-CMP-004）。理由：D-01。
- **BP-CMP-021**【调整】（12.3、12.4）输出 METADATA_PACKAGE，结构为：`metadata.json`（完整逻辑导入载荷）、`metadata_manifest.json`、`dependency-graph.json`、`change-summary.json`、`datastore-changes.json`（仅当 Blueprint 含应用配置；M5，当前分节 Schema 尚无该分节，故实际不产出）、`views/`、`README.md`；另输出 FIXTURE_PACKAGE（如有 Fixture 规格）、DIFF_REPORT、MANIFEST。依赖图与变更摘要由 MetaData Compiler 在完整图上生成，随包字节一起冻结。
- **BP-CMP-021a**【新增】提供元数据包文件清单读取：给定项目、版本与可选环境键，从该版本最近一次构建记录的 `output_hashes` 返回 `PackageManifest`（buildId、kind、environmentKey、packageHash、files[]），每个文件含路径、角色（`payload` / `manifest` / `graph` / `report` / `view` / `document`）、内容哈希、DHIS2 类型与对象数（仅载荷与按类型视图有对象数，其余为 null）。读取不打开产物字节，可在调用方事务内执行；尚未编译的版本返回空清单（`kind = NONE`），由调用方显示“尚未编译”，不得虚构包布局。对象身份仍以 Registry 为唯一答案。理由：S5 的按文件审核视图（S5 FR-UI-007）需要文件分组，而包内部布局归 S2 所有。
- **BP-CMP-022**【直引】（FR-CMP-005）元数据载荷是包含全部 CREATE、UPDATE 对象的单一逻辑导入载荷；按类型拆分的视图只读。
- **BP-CMP-023**【直引】（FR-CMP-006）载荷中禁止出现系统控制字段；这些信息全部放入 manifest。
- **BP-CMP-024**【直引】（FR-CMP-007、FR-REG-001）UPDATE 对象只变更受控字段：以该环境发现结果中的当前对象内容为基础合并生成完整对象。
- **BP-CMP-025**【直引】（FR-CMP-008）变更摘要：每种对象类型的新建、更新、不变数量，以及对 Managed Scope 之外对象的引用清单。
- **BP-CMP-026**【调整】（FR-CMP-009）删除不属于默认编译结果；只有已批准 CR 中列出、并通过删除安全检查（依赖图中无引用、目标环境无关联数据——后者调用 S4 只读查询）的 DEPRECATED 对象，才进入单独的删除候选集。【新增】删除候选由调用方显式传入（`deletion_candidates`），S2 逐条拒绝不满足条件的候选：条目不存在或不是 DEPRECATED、被依赖图引用、目标环境仍有 S4 报告关联数据、或未提供 CR ID。被拒候选不进入 manifest，也不报为编译失败；删除只对本次环境编译生效，不改变其他环境。
- **BP-CMP-027**【调整】（FR-ENV-005）manifest 必须标明哪些对象随环境变化，以及每个环境相关引用解析到的 UID。
- **BP-CMP-027a**【新增】`metadata_manifest.json` 还必须包含 S4 执行所需的全部信息：该环境的 Managed Scope（BP-REG-011）；每个 REUSE / REFERENCE / UPDATE 对象的观测基准（UID、observedLastUpdated、observedHash）；UID 到 businessKey 与来源阶段的映射；每个 CREATE / UPDATE 对象的受控字段清单；删除候选集（如有）。理由：S4 不调用 S2，漂移检查、Snapshot、ImportReport 映射与部署后验证只能依据 manifest。
- **BP-CMP-028**【调整】（FR-CMP-010、D-05）表达式中以 businessKey 引用对象，编译时替换为该环境的 UID；manifest 中列出每个表达式及其分类：“可在 DHIS2_VALIDATE 服务端校验”（引用对象全部在该环境已存在）或“延后至 POST_VERIFY 校验”。
- **BP-CMP-029**【新增】DIFF_REPORT：以对象为单位列出新建、更新（字段级，仅受控字段）、不变与仅引用，对比基础为该环境发现结果。理由：总体 SRS 15.5 第 1 种对比；PACKAGE_GATE 的审批依据。
- **BP-CMP-030**【调整】（FR-CAP-003）同一版本面向不同版本 Profile 的环境编译时，必须使用该环境的 Capability Profile；差异体现在 DIFF_REPORT 与版本间 Diff 中（总体 SRS AC-CAP-01）。

#### 3.8.4 各 Compiler

- **BP-CMP-040 Metadata Compiler**【裁剪】（12.1、6 章）按类型描述器与模板生成元数据载荷、依赖图、变更摘要与来源追溯。
- **BP-CMP-041 OpenAPI Compiler**（模式 B，M3）
  - 【直引】（FR-API-001）`resource` 使用逻辑名，由 Capability Profile 的资源目录解析为端点与参数名。
  - 【直引】（FR-API-002）全部受支持版本（v2.40 及以上）的 Tracker 数据必须解析为 `/api/tracker` 及其子资源；旧 Tracker 端点禁止出现，无例外。
  - 【直引】（FR-API-003）使用当前版本参数与字段命名，多 UID 参数逗号分隔。
  - 【直引】（FR-API-004）多个页面共享的数据能力以共享 Schema 组件 `$ref` 引用，禁止复制。
  - 【直引】（FR-API-005）包含以 `baseUrl` 为变量的 `servers`、目标版本支持的 `securitySchemes`（ApiToken、Basic、App Platform 会话 Cookie）、每个操作的 `x-registry-refs`。
  - 【新增】为每个 operationKey 生成请求与响应示例（依据 Registry 的值类型与选项集确定性生成）。理由：总体 SRS 17.3 完整性检查要求每个 operationKey 都有示例。
  - 【直引】（12.1）同时输出 OpenAPI 3.1 文档与 API 参考 Markdown。
- **BP-CMP-042 UI Spec Compiler**（模式 B，M3）【裁剪】（10.3、12.1）输出 UI Specification JSON 与页面 Markdown，内容覆盖总体 SRS 10.3 列出的全部要素；组件名优先使用 `@dhis2/ui`（FR-UII-001）。
- **BP-CMP-043 Fixture Compiler**（M3）
  - 【直引】（FR-POL-006、FR-POL-008）只生成合成数据，依据 Registry 值类型、选项集与 Fixture 规格；使用构建上下文中的种子，同一输入产生同一数据集；载荷为目标版本的 Tracker 导入格式。
  - 【直引】（FR-DOM-001）敏感属性的合成值必须明显为合成（如固定前缀）。
  - 【调整】（FR-COV-008）演示用户以规格形式输出（用户名、角色、用户组、组织范围），不含密码；凭证由 S4 在导入时生成并存入 Secret Manager。
  - 【直引】（FR-POL-007）使用真实数据作为 Fixture 不在 S2 生成范围内。
- **BP-CMP-044 Package Compiler**（模式 B，M3）
  - 【直引】（FR-FE-001）从同一 Blueprint 版本确定性生成开发包，目录结构按总体 SRS 17.1；每个 UID、字段、参数可追溯到 Registry 条目。
  - 【直引】（FR-FE-002）`registry.json` 按环境给出 UID，标出随环境变化的条目。
  - 【直引】（FR-FE-003、FR-FE-004、FR-FE-006）README、开发指南、`debugging.md` 的内容要求按原文。
  - 【直引】（FR-FE-005）禁止包含凭证。
  - 【调整】（12.1“执行记录”）部署状态信息（环境、部署时间、Blueprint 版本、部署后验证结果、Fixture 导入结果）来自调用方传入的部署证据（Run 标识与 S4 的执行报告、部署后验证报告产物 ID），S2 通过 S0 读取这些产物。
- **BP-CMP-045 DataStore Compiler**（M5）【直引】（12.1）从 Blueprint 应用配置条目生成 DataStore 变更集（namespace、key、value）。
- **BP-CMP-046 前端起步工程生成**（M5）【直引】（17.4）基于 Blueprint、OpenAPI、UI Specification 与 Registry 生成可运行的 DHIS2 Web App 起步工程。

### 3.9 校验（BP-VAL）

#### 3.9.1 通用

- **BP-VAL-001**【调整】（13.1）问题（Issue）结构：问题 ID（同一输入下稳定）、规则 ID、级别 `level`（`BLOCKING` / `WARNING` / `INFO`）、消息、businessKey、UID、DHIS2 类型、产物 ID、位置、分节、来源阶段、建议处理、详情；另提供由问题 ID、规则 ID、级别、消息、businessKey、来源阶段、建议处理组成的精简形式用于列表。本结构是全系统唯一的问题定义（00 文档 6.10），S4 的 ImportReport 标准化问题与 S3 输出中的问题均须遵循，级别字段一律称为 `level`。
- **BP-VAL-002**【直引】（C-07、13 章引言）校验器只报告，不修复。
- **BP-VAL-003**【新增】校验结果以 VALIDATION_REPORT 产物输出，同时返回 BLOCKING 数量与 WARNING 列表（问题 ID、规则 ID、对象、说明）。理由：S1 创建 Gate 时需要逐条 WARNING（CP-GATE-004）。
- **BP-VAL-004**【新增】来源阶段按问题所涉分节确定（附录 A）；无法确定时取最早的相关阶段。理由：总体 SRS FR-WF-005。
- **BP-VAL-005**【新增】提供问题查询：输入一组报告产物 ID（VALIDATION_REPORT 与按本结构写入的 DHIS2_IMPORT_REPORT），支持按级别、来源阶段、规则 ID、businessKey 筛选，服务端排序与分页（offset），返回问题列表与总数。只读，不访问 DHIS2。理由：界面需按级别、来源阶段、规则、对象筛选大规模问题列表（S1 CP-UI-004），报告内容格式归 S2 所有，由 S2 解析可避免他方依赖其内部格式。

#### 3.9.2 Blueprint 校验

- **BP-VAL-010**【直引】（13.2）Blueprint 符合 JSON Schema；businessKey 唯一且引用可解析；每个 Registry 条目 Disposition 合法、UPDATE 声明受控字段；UID 格式合法且在 Registry 内唯一；code 在项目内同类型唯一；每个领域元素都有映射或显式标记为不映射。
- **BP-VAL-011**【调整】另含：决策完整性与确认记录（BP-DEC-002、BP-DEC-004）；页面至少关联一个 Use Case，Use Case 参与者映射到权限模型中的角色或用户组（FR-PG-001）；页面、API Intent、UI Intent 中无未登记 UID（FR-PG-002）；Fixture 规格引用的对象均已登记。

#### 3.9.3 产物静态校验

- **BP-VAL-020**【直引】（13.3）身份、引用、Schema（依据 Capability Profile 的 Schema 包）、架构、范围、多语言（缺失翻译为 WARNING，FR-COV-010）、OpenAPI（3.1 校验、无弃用或已移除的端点与参数名）。
- **BP-VAL-021**【新增】本地表达式校验（D-05）：以 DHIS2 官方 ANTLR 语法为基础生成的 Python 解析器（按 Profile 版本管理语法差异），对 ProgramRule 条件、ProgramIndicator 表达式、ValidationRule 表达式做语法解析；校验 businessKey 引用可解析、引用对象类型正确、变量名已定义；失败为 BLOCKING。理由：总体 SRS FR-CMP-011 在对象未提交前无法由服务端完成。
- **BP-VAL-022**【新增】REUSE / REFERENCE 目标必须存在于该环境最近一次发现结果中（13.3 引用规则）；UID 冲突、code 冲突、跨项目所有权冲突为 BLOCKING。

#### 3.9.4 跨产物一致性校验

- **BP-VAL-030**【裁剪】（13.4）S2 执行以下维度：需求 ↔ 领域、领域 ↔ 映射、映射 ↔ Registry、Registry ↔ 元数据包、页面 ↔ API Intent、API Intent ↔ UI Intent（FR-UII-002）、API Intent ↔ Registry、页面 ↔ 权限模型、Fixture ↔ 元数据。总体 SRS 13.4 中“Capability ↔ 执行计划”一项属于 S4。
- **BP-VAL-031**【直引】（13.4 示例）UI 需要的数据能力在 API Intent 中无对应 operationKey、API Intent 引用 PROPOSED 状态的条目、载荷引用未登记为 REFERENCE 的 Managed Scope 外对象，均为 BLOCKING。

### 3.10 对象级 Diff（BP-DIFF）

- **BP-DIFF-001**【裁剪】（15.5）S2 提供：Blueprint 版本 N 与 N+1 的对比；Registry 版本 N 与 N+1 的对比（Registry 随 Blueprint 版本化，即按版本对比其 Registry 分节）；环境当前状态与本次编译结果的对比（DIFF_REPORT，BP-CMP-029）。均以对象为单位展示新建、更新（字段级）、不变与仅引用。
- **BP-DIFF-002**【调整】（15.5）“提交前 Snapshot 与提交后状态”与“同一版本在两个环境中的部署状态”的对比属于 S4。
- **BP-DIFF-003**【新增】提供工作草稿相对其基准版本的对象级 Diff（形式同 BP-DIFF-001，状态含新增、更新、不变、仅引用、移除）。理由：设计阶段审核需要看到“本阶段相对当前版本改了什么”（总体 SRS FR-UI-004）。

### 3.11 开发包完整性检查（BP-HO，M3）

- **BP-HO-001**【直引】（17.3）完整性检查项：每个页面都有页面规格、API Intent、UI Intent；每个 operationKey 都有请求与响应示例；每个表单字段都有字段映射；页面依赖的全部元数据已部署并通过部署后验证；OpenAPI 通过校验且不含弃用端点；每个翻译语言的翻译键完整；Fixture 已在 DEV 或 TEST 导入成功（如项目要求）；追溯链完整无悬空引用；不含凭证或真实个人数据。
- **BP-HO-002**【调整】（17.3）“已部署并通过部署后验证”与“Fixture 已导入”的判定依据为调用方传入的部署证据产物；S2 校验这些产物属于同一 Blueprint 版本。
- **BP-HO-003**【直引】（17.3）页面只有在全部检查通过后才标记为 FRONTEND_READY；检查结果以 HANDOFF_CHECK_REPORT 产物输出，含每个页面的状态。
- **BP-HO-004**【新增】（17.1）提供开发包文件读取：`list_package_files(artifact_id)` 返回包内文件的路径、类型、字节大小与内容哈希（清单按路径排序，与 `read_package_file` 由同一份字节计算哈希，二者不可能不一致）；`read_package_file(artifact_id, path)` 返回单个文件的内容、媒体类型、大小与哈希。类型枚举为 DOCUMENT、CONTRACT、UI_SPEC、REGISTRY、EXAMPLE、FIXTURE、I18N、TRACEABILITY（未识别路径一律归 DOCUMENT）。约束：
  1. 产物不存在，或存在但不是任何构建记录产出的产物时，抛出 `NOT_FOUND`（`details.reason = NOT_EXIST`）；
  2. 产物存在但类型不是 FRONTEND_PACKAGE 时抛出 S0 内置错误码 `ARTIFACT_TYPE_MISMATCH`（FD-COM-001a；`details = {artifactId, expectedType: FRONTEND_PACKAGE, actualType}`）——这是调用方错误，不得以 `NOT_GENERATED` 表达；S2 不得自行登记该错误码，仅当部署的 S0 目录尚未包含该码时补位登记（不得覆盖 S0 声明的所有者与可重试属性），S0 补齐后应删除该补位项；
  3. “项目尚无开发包”由调用方（S1）在解析项目当前开发包时判定并返回 `NOT_GENERATED`，S2 不承担此判定；
  4. `path` 为空、含 `.` 或 `..` 路径段、以 `/` 开头、含反斜杠、`%`、`#`、NUL 或其他控制字符（含 DEL）时，一律按 `NOT_FOUND`（`details.reason = NOT_EXIST`）处理，与“路径不存在”不可区分，不泄露包内结构；
  5. 已清除的产物仍返回 `ARTIFACT_PURGED`；包内文件不是 UTF-8 文本时返回 `ARTIFACT_ERROR`，不猜测编码。
  理由：开发包内部结构归 S2 所有，调用方不得自行解包。

### 3.12 导出、导入与清理（BP-EXP）

- **BP-EXP-001**【调整】（FR-BP-009）导出：S2 生成一个 BLUEPRINT_EXPORT 产物并返回其 ID、内容哈希、版本清单、当前版本与全部被引用产物 ID 清单，供 S1 调用 S0 解包与搬运；导出内容包含全部 Blueprint 版本内容、UID 分配账本、UID 池、环境绑定、构建记录与校验运行记录。Registry、决策、业务键与依赖图索引等派生数据不导出，由导入方按版本内容重建（BP-VER-002）；Patch 与草稿属于进行中的工作，不在导出范围。项目没有任何版本时以 `NOT_FOUND` 拒绝。
- **BP-EXP-002**【调整】（FR-BP-009、AC-DATA-01）导入（两阶段：准备为外部 I/O，提交在调用方工作单元中）：`prepare_import` 以导出**产物 ID** 与 S0 解包返回的原产物 ID → 新产物 ID 映射为输入，校验每个版本内容仍哈希到清单记录值、报告未被映射覆盖的产物 ID，并把结果暂存为导入批次；`commit_import` 恢复全部版本（内容哈希不变）、UID 分配账本、UID 池、环境绑定、构建记录与校验运行记录，并从版本内容重建全部派生索引。导入的环境绑定标记为“待重新解析”，因为目标部署的环境不同。提交拒绝三类冲突：目标项目已有不同内容的同版本号；目标项目当前版本不在导出内（否则会留下两个可提升版本）；某一 UID 已分配给本部署的其他项目（`BP_IMPORT_CONFLICT`）。同一批次重复提交以 `BP_IN_PROGRESS` 拒绝，同一幂等键重试返回首次结果。以相同构建上下文与产物重新编译，输出哈希必须与导出方部署一致。
- **BP-EXP-003**【新增】提供项目数据清理（在调用方工作单元中）：删除 `bp` 中该项目全部数据（版本、草稿、Patch、Registry 索引、决策、环境绑定、构建记录与校验运行、依赖与业务键索引、UID 池、导入批次与暂存内容）；活动 UID 分配置为 RETIRED，分配账本行永久保留以防复用（`bp.uid_allocation` 的数据库触发器拒绝 DELETE）。返回各表删除行数、退休 UID 数与保留的账本行数，并写 `bp.project.data_deleted` 审计。清理只作用于 `bp` 与该项目：S0 中的产物字节不在删除范围；UID 池按项目整体删除，不提供按环境清池。理由：CP-PRJ-007；UID 永不复用（总体 SRS FR-REG-004）。

---

## 4. 业务规则汇总

| 编号 | 规则 | 来源 |
| --- | --- | --- |
| BP-BR-01 | Blueprint 是唯一语义事实源；编译只消费已批准版本 | 【直引】P-02、C-06 |
| BP-BR-02 | 版本不可变；修改只经草稿与 Patch | 【直引】FR-BP-004 |
| BP-BR-03 | 变更级别自动判定，不得降级 | 【直引】FR-BP-005 |
| BP-BR-04 | UID 只由 S2 分配；一经分配永不重新生成、永不复用 | 【调整】P-04、FR-REG-004/006 |
| BP-BR-05 | 同一概念一个 UID，同一 UID 一个概念 | 【直引】FR-REG-003 |
| BP-BR-06 | UPDATE 只改受控字段 | 【直引】FR-REG-001、FR-CMP-007 |
| BP-BR-07 | ARCHITECTURE_GATE 后映射与 Registry 只能经已批准 CR 修改 | 【调整】FR-MAP-004 |
| BP-BR-08 | 低置信度或高影响决策必须有用户确认记录 | 【调整】FR-MAP-003 |
| BP-BR-09 | 编译确定性、字节级可复现 | 【直引】FR-CMP-001～004 |
| BP-BR-10 | 元数据以单一逻辑载荷输出；无系统控制字段；删除只在已批准 CR 中且通过安全检查 | 【直引】FR-CMP-005/006/009 |
| BP-BR-11 | 新生成契约禁止旧 Tracker 端点 | 【直引】FR-API-002 |
| BP-BR-12 | 校验只报告不修复 | 【直引】C-07 |
| BP-BR-13 | 同一环境同一 UID 不得被两个项目托管 | 【新增】D-03 |
| BP-BR-14 | 环境绑定不属于版本内容，人工设置不产生新版本 | 【新增】D-02 |

---

## 5. 数据需求（schema `bp`）

全部实体含创建与更新时间，略。版本内容本身存于 S0 的 BLUEPRINT 产物，以下索引表可由版本内容完全重建。

| 实体 | 最少字段 | 来源 |
| --- | --- | --- |
| BlueprintVersion | id、projectId、version、parentVersion、schemaVersion、changeLevel、contentHash、artifactId、status、decisionIds、createdBy；另镜像 deliveryMode、projectType、语言、目标版本约束、是否含 Fixture 规格、promotedFromStage、promotedFromDraftId | 【调整】23.1 |
| BlueprintDraft | id、projectId、baseVersion、stage、stageScope、changeRequestId、status（OPEN / FROZEN / PROMOTED / RESET）、content、baseContent、currentHash、frozenHash、stagingHandle、frozenIssues、promotable、createdBy | 【新增】D-04 |
| BlueprintPatch | id、projectId、draftId、baseVersion、authorKind、authorRef、decisionIds、operations、rationale、declaredImpact、changeRequestId、status、rejectIssues、assignedUids、appliedHash、resultVersion | 【调整】23.1 |
| PreparedContent | handle、projectId、kind、content、baseContent、contentHash、sourceArtifactIds、expiresAt；交付模式升级等两阶段操作的暂存镜像 | 【新增】BP-VER-005 |
| ImportBatch | id、projectId、sourceDeployment、exportManifest、idMap、payload、status（PREPARED / COMMITTED） | 【新增】BP-EXP-002 |
| RegistryEntryIndex | projectId、blueprintVersion、businessKey、dhis2Type、uid、code、disposition、managed、controlledFields、status、decisionId、observed 基准字段 | 【调整】23.1 |
| EnvironmentBinding | projectId、blueprintVersion、environmentKey、businessKey、uid、observedLastUpdated、observedHash、discoveryResultId、resolution（AUTO / MANUAL）、resolvedBy、note、pendingReparse | 【新增】D-02 |
| UidAllocation | uid、projectId、businessKey、dhis2Type、sourceEnvironment、status（ACTIVE / RETIRED / DEPRECATED）、allocatedAt、retiredAt、patchId | 【新增】BP-REG-006 |
| UidPool | uid、projectId、sourceEnvironment、fetchedAt | 【新增】BP-REG-008a |
| DecisionIndex | projectId、blueprintVersion、decisionId、type、subject、confidence、impactLevel、confirmedBy | 【调整】23.1 |
| BuildRecord | id、projectId、kind（BASELINE / ENVIRONMENT / HANDOFF）、blueprintVersion、blueprintHash、environmentKey、inputs（产物 ID 与哈希）、compileParameters（designEnvironment、changeRequestId、deletionCandidates）、compilerVersion、templateVersion、descriptorVersion、packageTemplateVersion、profileVersion、openapiVersion、blueprintSchemaVersion、buildContext、outputArtifactIds、outputHashes、packageHash、recordsHash、recipeHash、status、s4Probe、error | 【调整】FR-CMP-003、BP-CMP-004 |
| ValidationRun | id、buildId、reportArtifactId、blockingCount、warningCount、infoCount、issuesDigest | 【新增】BP-VAL-003 |

数据管理：
- **BP-DATA-01**【直引】（FR-DATA-003）`bp` schema 变更通过迁移脚本管理。当前迁移链为 `0001_bp_initial`（基础表与触发器）、`0002_bp_prepared_base`（`prepared_content.base_content`，供交付模式升级判定变更级别）、`0003_bp_compile_parameters`（`build_record.compile_parameters`，供复现重放）；两列均以“列已存在则跳过”的守卫编写，因为新库直接由 ORM 元数据建表。
- **BP-DATA-02**【直引】（NFR-CAP-001、NFR-CAP-002）单项目至少 5,000 个元数据对象、100 个页面。

---

## 6. 流程需求

### 6.1 设计阶段中的草稿

1. S1（或 S3 代表 Agent）打开草稿 → S3 或用户提交 Patch → S2 校验 → PROPOSED 并叠加，或 REJECTED 并返回问题。
2. 阶段结束，S1 调用冻结（外部 I/O）→ 返回草稿哈希、暂存句柄、问题。
3. 审批时 S1 在工作单元中调用提升 → 返回新版本、变更级别、受影响阶段与影响集。
4. 阶段重跑时 S1 调用重置。

### 6.2 基线编译

1. S1 调用基线编译（版本、设计基准环境 Capability Profile、构建上下文）→ 生成产物 → 运行 Blueprint 校验与环境无关的跨产物校验 → 返回构建 ID、产物 ID、校验结果。

### 6.3 环境绑定编译与校验

1. S1 调用环境绑定编译（版本、环境、发现结果、Capability Profile、CR、构建上下文）。
2. S2 解析环境绑定 → 调用 S4 做 UID / code 存在性检查 → 所有权冲突检查 → 编译元数据包、Fixture 包、manifest、DIFF_REPORT。
3. S1 调用校验 → S2 执行静态校验（含本地表达式）与跨产物校验 → 返回 VALIDATION_REPORT、BLOCKING 数量与 WARNING 列表。
4. 存在绑定无法解析的 BLOCKING 时，用户经 S1 人工设置绑定后，S1 重新调用环境绑定编译。

### 6.4 开发包

1. S1 传入版本、目标环境列表与部署证据 → S2 生成 FRONTEND_PACKAGE → 执行完整性检查 → 返回 HANDOFF_CHECK_REPORT 与各页面状态。

---

## 7. 公共库 API（`factory_blueprint.api`）

说明：“事务内”指可在调用方工作单元中执行；“外部 I/O”指必须在事务外调用（S0 强制检查）。全部有副作用的函数接受幂等键；需要数据库的只读函数以 `session` 参数参与调用方工作单元（表中省略该参数）。`api.__all__` 同时导出各函数返回的 Pydantic 模型、枚举与错误类，调用方不应解析产物字节或 `bp` 表来补充信息。

### 7.1 初始化与 Schema

| 函数 | 类别 | 输入 | 输出 |
| --- | --- | --- | --- |
| `initialize(*, session=None)` | 启动 | — | 登记产物类型、审计事件类型、错误码、ID 前缀与 `bp` 迁移配置 |
| `MIGRATION_CONFIG` | 常量 | — | `bp` schema 的迁移配置（供 S0 迁移运行器使用） |
| `list_sections(schema_version=None)` | 只读 | — | 当前与可用 Schema 版本；各分节的名称、条目容器、键字段 |
| `list_object_types()` | 只读 | — | 已登记类型描述器的目录（类型名、Tier、逻辑资源、载荷键等） |
| `get_section_schema(section, schema_version=None)` / `section_schema(...)` | 只读 | 分节名 | 自包含的 JSON Schema（Draft 2020-12，引用已内联） |
| `document_schema(schema_version=None)` / `current_schema_version()` / `available_versions()` | 只读 | — | 整文档 Schema、当前 Schema 版本、可用版本列表 |
| `migrate_document(document, frm, to)` / `register_migration(section, frm, to)` / `has_migration_path(frm, to)` | 只读／注册 | 文档、源与目标版本 | 迁移后的文档、分节迁移器注册、路径是否存在（BP-SCH-007） |
| `prepare_initial_blueprint(project_id, project_info)` | 外部 I/O | 交付模式、项目类型、版本约束、语言、生成策略、来源产物 | 暂存句柄 |
| `register_initial_blueprint(project_id, handle)` | 事务内 | 暂存句柄 | 版本 `0.1.0` 的 `VersionInfo` |

### 7.2 版本与查询

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `get_current_version(project_id)` | 只读 | 版本号、哈希、产物 ID、变更级别、Schema 版本 |
| `list_versions(project_id, page=1, page_size=50)` / `get_version(project_id, version)` | 只读 | 版本元数据（分页） |
| `get_project_semantics(project_id, version=None)` | 只读 | 交付模式、项目类型、语言、是否含 Fixture 规格、映射是否已冻结、提升来源阶段 |
| `get_entries(project_id, selector, version=None)` | 只读（读产物内容，外部 I/O） | 按 businessKey / 决策 ID / 页面 / 分节选取的最小条目集与未命中键；`EntrySelector.source` 为 `APPROVED`（默认）或 `DRAFT` |
| `locate_entries(project_id, selector, version=None)` | 只读（事务内） | 同一次选取的 `(section, entryKey, path)` 位置集，不含内容 |
| `get_decisions(project_id, decision_ids, version=None)` | 只读 | 决策详情；`decision_ids` 为空时列出该版本全部决策（当前不分页） |
| `diff_versions(project_id, from_version, to_version, sections=None)` | 只读 | 对象级 Diff（含 Registry） |
| `diff_draft(draft_id, sections=None)` | 只读 | 草稿相对基准版本的对象级 Diff（草稿未物化时 `DRAFT_NOT_MATERIALIZED`） |

### 7.3 草稿与 Patch

| 函数 | 类别 | 输入 | 输出 |
| --- | --- | --- | --- |
| `open_draft(project_id, stage, change_request_id=None, sections=None)` | 事务内 | 设计阶段；执行 CR 时传入已批准的 CR ID；`sections` 可进一步收窄阶段范围（省略时按附录 A） | 草稿 ID（已存在则返回之） |
| `prepare_draft_base(project_id, draft_id)` | 外部 I/O | — | 物化后的草稿哈希；把基准版本内容（必要时先按 BP-SCH-007 迁移）写入草稿，幂等 |
| `submit_patch(project_id, draft_id, patch, context, idempotency_key)` | 事务内 | Patch；上下文（阶段、CR ID） | 状态（PROPOSED / REJECTED）、问题列表、合并 Question 数据（如有）、新草稿哈希、新分配的 UID |
| `get_draft(draft_id)` / `get_active_draft(project_id)` | 只读 | — | 草稿元数据（BP-DRF-011）；无活动草稿时为空 |
| `reset_draft(draft_id)` | 事务内 | — | 已丢弃的 Patch 数量（其 UID 置为 RETIRED） |
| `freeze_draft(draft_id, target_environment_keys)` | 外部 I/O | 目标环境列表 | 草稿哈希、暂存句柄、是否可提升、问题列表、BLOCKING 数量与 WARNING 列表 |
| `promote_draft(draft_id, expected_hash, idempotency_key)` | 事务内 | 期望哈希 | 新版本号、变更级别、受影响阶段集合、影响集、产物 ID、已应用 Patch ID |
| `prepare_mode_upgrade(project_id)` / `commit_mode_upgrade(project_id, handle, idempotency_key)` | 外部 I/O / 事务内 | — | 新版本信息（同上）；升级暂存内容与用于判定变更级别的基准内容（`PreparedContent.baseContent`） |

### 7.4 影响分析与 Registry

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `analyze_impact(project_id, business_keys, description=None, version=None)` | 只读（读产物内容，外部 I/O） | 受影响阶段、条目、页面、产物类型 |
| `get_registry(project_id, version, environment_key=None)` | 只读 | Registry 条目（可附带一个环境的 UID 绑定） |
| `get_environment_bindings(project_id, version, environment_keys=None)` | 只读 | 环境绑定（BP-REG-015 全部字段；可一次返回多个环境；省略环境键时返回全部） |
| `get_object_relationships(project_id, version=None, dhis2_types=None, edge_kinds=None)` | 只读 | 依赖图的边（引用方、被引用方、字段、两端类型、`edge_kind`）；节点即 `get_registry`，不重复返回；可按类型与边种类收窄 |
| `get_managed_scope(project_id, version, environment_key)` | 只读 | 托管对象及其该环境 UID |
| `replenish_uid_pool(project_id, environment_key, batch_size=200, low_water_mark=50, min_available=None)` | 外部 I/O | 补充后的池内可用数量；补充量为 `max(batch_size, target - 当前可用, 0)`，`target` 为 `min_available`（给出时）否则为 `low_water_mark` |
| `get_uid_pool_status(project_id)` | 只读 | 可用数量、低水位、来源环境、是否需要补充（低水位取配置默认值，不是项目 `generationPolicy` 的值） |
| `set_environment_binding(project_id, version, environment_key, business_key, uid, note, resolved_by)` | 事务内 | 绑定记录（写审计；拒绝不存在的条目、非法 UID、CREATE 对象与其他项目管理的对象） |

### 7.5 编译、校验与复现

| 函数 | 类别 | 输入 | 输出 |
| --- | --- | --- | --- |
| `compile_baseline(project_id, version, capability_profile_artifact_id, build_context, progress, cancel, idempotency_key)` | 外部 I/O | Capability Profile 可空 | 构建 ID、产物 ID、构建记录、校验结果摘要（`BuildResult`） |
| `compile_for_environment(project_id, version, environment_key, discovery_artifact_id, capability_profile_artifact_id, change_request_id, build_context, progress, cancel, idempotency_key, deletion_candidates=None, design_reference_environment=None)` | 外部 I/O | 删除候选列表（`{businessKey, uid}`）仅随已批准 CR 生效；设计基准环境可覆盖发现结果中的环境键 | 构建 ID、METADATA_PACKAGE / FIXTURE_PACKAGE / MANIFEST / DIFF_REPORT 产物 ID、绑定解析问题（`BuildResult`） |
| `get_package_manifest(project_id, version=None, environment_key=None)` | 只读（事务内） | — | 该版本最近一次构建的元数据包文件清单（BP-CMP-021a） |
| `validate_build(build_id, progress, cancel, idempotency_key)` | 外部 I/O | — | VALIDATION_REPORT 产物 ID、BLOCKING / WARNING / INFO 数量、WARNING 列表、问题摘要哈希与来源阶段 |
| `reproduce(artifact_id)` | 外部 I/O | 该构建任一产物 ID | 是否一致、期望与实际的产物／载荷／包哈希、不一致项 |
| `query_issues(report_artifact_ids, level=None, source_stage=None, rule_id=None, business_key=None, sort_by="level", sort_dir="ASC", limit=50, offset=0)` | 外部 I/O（读产物内容） | 级别、来源阶段、规则 ID、businessKey | 问题列表（BP-VAL-001 结构）与总数 |
| `generate_frontend_package(project_id, version, environment_keys, deployment_evidence, build_context, progress, cancel, idempotency_key)` | 外部 I/O | 部署证据产物 ID（单个或列表） | FRONTEND_PACKAGE 与 HANDOFF_CHECK_REPORT 产物 ID、各页面状态；目标环境须已有环境绑定编译，其 Capability Profile 从该构建记录读取 |
| `get_dependency_graph(build_id, filters=None)` | 外部 I/O（读产物内容） | DHIS2 类型、businessKey | 节点（含入度、出度）、边、`orphans`、`unresolved_registry_refs`（BP-TD-009） |
| `list_package_files(artifact_id)` / `read_package_file(artifact_id, path)` | 外部 I/O（读产物内容） | `artifact_id` 为 FRONTEND_PACKAGE 产物 ID（注意：与提交执行中指 METADATA_PACKAGE 的 `package_artifact_id` 不是同一概念） | 文件清单 / 单个文件内容（BP-HO-004） |

### 7.6 导出、导入与清理

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `export_blueprint(project_id)` | 外部 I/O | `ExportResult`：导出产物 ID、内容哈希、版本与当前版本、被引用产物 ID 清单（BP-EXP-001） |
| `prepare_import(export_artifact_id, id_map, project_id=None)` | 外部 I/O | `ImportPreview`：批次 ID、将恢复的版本、未映射产物 ID、问题与计数；`id_map` 为 S0 解包返回的原产物 ID → 新产物 ID 映射（BP-EXP-002） |
| `commit_import(project_id, batch_id, idempotency_key)` | 事务内 | `ImportResult`：恢复的版本、当前版本、各表计数、待重新解析的绑定数 |
| `delete_project_data(project_id)` | 事务内 | `ProjectCleanupResult`：各表删除行数、退休 UID 数、保留账本行数（BP-EXP-003） |

### 7.7 测试替身与依赖注入

- **BP-API-001**【新增】`factory_blueprint.testing` 必须提供上述全部函数的 Fake 实现（内存版本、可预设的编译结果与问题），供 S1、S3 独立开发。理由：00 文档 6.1。
- **BP-API-002**【新增】Fake 必须提供以下预设位，使调用方无需数据库即可覆盖上述三条产物读取：`builds`（已分发的构建）、`graphs`（按 build ID 预设依赖图，缺省即 `NOT_GENERATED`）、`packages`（按 FRONTEND_PACKAGE 产物 ID 存 `{路径: 字节}`）、`non_package_artifacts`（存在但非开发包的产物 ID → 实际类型）。依赖图收窄、文件类型判定、哈希与路径校验必须调用生产实现（`narrow_graph`、`package_file_kind`、`content_hash`、路径校验），不得在替身中重写。理由：替身与生产的答案不一致会让 S1 测到不存在的状态。
- **BP-API-003**【新增】S4 只读协作者通过 `set_dhis2(implementation)` / `get_dhis2()` / `reset_providers()` 注入；默认实现是真实 S4 适配器，`StubDhis2` 只供测试或独立 Fake 显式安装。禁止在 S4 不可用时静默回退到虚构的 UID、存在性或删除判定。`configure(**changes)` / `settings()` / `BlueprintSettings` 控制启用的 Tier 与 Compiler（BP-TD-002）；默认 `enabled_compilers` 含 metadata、openapi、uiSpec、fixture、package，DataStore 不在其中。理由：00 文档 6.1、BP-TD-002。

---

## 8. 对其他子系统的依赖

| 提供方 | S2 需要的能力 | 用途 |
| --- | --- | --- |
| S0 | 工作单元、外部 I/O 标记、产物暂存与登记与读取、审计、幂等、错误、ID、规范化 JSON 与哈希、进度与取消 | 全部 |
| S4（只读） | `generate_uids(environment_key, count)`、`uid_exists(environment_key, uids)`、`code_exists(environment_key, dhis2_type, codes)`、`has_associated_data(environment_key, dhis2_type, uid)` | BP-REG-008、BP-REG-008a、BP-REG-009、BP-CMP-026 |
| S4（内容格式） | CapabilityProfile 产物（资源目录、Schema 包、导入参数、弃用与移除清单、类型描述器覆盖、版本校验规则集）与 DiscoveryResult 产物（对象类型、UID、code、name、lastUpdated、内容哈希、当前对象内容、关键引用关系）的格式定义 | 编译与校验的输入 |

S4 的 `uid_exists` / `code_exists` 按“占用者”作答而不是布尔值：UPDATE 条目的 UID 本来就应存在于环境中，只有占用者是不同类型的对象（或同 code 属于另一对象）才构成冲突。表达式语法版本由 S4 的 Profile 给出，S2 以自己的语法文件注册同名别名（BP-VAL-021）。

S2 不依赖 S1 与 S3。

---

## 9. 非功能需求

| 编号 | 要求 | 来源 |
| --- | --- | --- |
| BP-NFR-01 | 1,000 个对象规模的元数据包编译 < 30 秒 | 【直引】NFR-PERF-003 |
| BP-NFR-02 | 1,000 个对象规模的静态与跨产物校验 < 30 秒 | 【直引】NFR-PERF-004 |
| BP-NFR-03 | Patch 提交（含校验与 UID 分配）P95 < 500 ms（5,000 对象规模的项目） | 【新增】（人工编写交互需要） |
| BP-NFR-04 | 单项目至少 5,000 个元数据对象、100 个页面 | 【直引】NFR-CAP-001/002 |
| BP-NFR-05 | Compiler 与 Validator 单元测试行覆盖率 ≥ 90%，其余 ≥ 85% | 【直引】NFR-MNT-004 |
| BP-NFR-06 | 新增对象类型只需新增描述器、模板与规则；新增 DHIS2 版本只需 S4 提供新 Profile，S2 核心代码不变 | 【直引】NFR-MNT-001、FR-CAP-001 |
| BP-NFR-07 | Compiler 快照测试：固定 Blueprint 产生固定产物，哈希与基线一致；基线变更需评审 | 【直引】25.1 |
| BP-NFR-08 | 构建记录登记全部兼容性维度（Blueprint Schema、Compiler、模板、描述器、Profile、OpenAPI、开发包模板版本） | 【直引】22.8 |

---

## 10. 跨系统假设

| 编号 | 假设 | 不成立时的影响 |
| --- | --- | --- |
| BP-ASM-01 | S1 在编译调用中提供构建上下文（时间与种子），且对同一次构建重试时提供相同值 | 无法字节级复现 |
| BP-ASM-02 | S1 在冻结时提供项目全部目标环境，在编译开发包时提供部署证据 | 无法完成冲突检查与完整性检查 |
| BP-ASM-03 | S4 发布 CapabilityProfile 与 DiscoveryResult 的内容格式，且发现结果包含 UPDATE 合并所需的当前对象完整内容 | 无法编译 UPDATE 对象 |
| BP-ASM-04 | S3 提交的 Patch 遵循分节 Schema，决策中带确认记录（当用户回答了相关 Question 时） | 冻结出现 BLOCKING |
| BP-ASM-05 | S1 保证同一项目同一时刻只运行一个设计阶段 | 单草稿模型不成立 |
| BP-ASM-06 | S1 与 S3 在收到 `UID_POOL_EXHAUSTED` 时补充 UID 池后重试；S1 在运行 MAPPING、PAGE_DESIGN 与执行 CR 前预先补充 | Patch 提交反复失败 |
| BP-ASM-07 | 设计基准环境在补充 UID 池时可访问 | 无法分配新 UID（池内余量可继续使用） |

---

## 11. 验收标准

| 编号 | 给定 / 当 | 则 | 里程碑 | 来源 |
| --- | --- | --- | --- | --- |
| AC-BP-01 | 任意编译产物 | 可追溯到唯一的 Blueprint 版本与哈希 | M2 | 【直引】 |
| AC-BP-02 | 版本中映射已冻结，Patch 触及 mapping 且无已批准 CR | Patch 被拒绝，映射与 Registry 不变 | M2 | 【调整】 |
| AC-REG-01 | 同一版本重复编译 3 次 | 不产生新 UID，三次输出哈希一致 | M2 | 【直引】 |
| AC-REG-02 | Registry 中出现 12 位或数字开头的 UID | Blueprint 校验报告 BLOCKING | M2 | 【直引】 |
| AC-REG-03 | Brownfield 复用既有 OptionSet | 条目为 REUSE，载荷不含该 OptionSet，引用校验通过 | M2 | 【直引】 |
| AC-REG-04 | UPDATE 条目只声明 name 与 description 为受控字段 | 编译对象保留环境中其余字段原值 | M2 | 【直引】 |
| AC-COV-01 | DataElement 未指定 CategoryCombo | 编译结果显式引用该环境 default CategoryCombo 的 UID | M2 | 【直引】 |
| AC-COV-02 | 翻译语言为英文与法文，某对象缺法文名称 | 静态校验 WARNING；补齐后载荷含两种语言翻译 | M2 | 【直引】 |
| AC-VAL-01 | 载荷缺失引用、类型错误或存在未登记对象 | 静态校验 BLOCKING | M2 | 【直引】 |
| AC-API-01 | Profile 为 v2.41，API Intent 读取 Tracker 事件 | 使用新 Tracker 端点与当前参数名；任何旧端点报告 BLOCKING | M3 | 【直引】 |
| AC-API-02 | 两个页面都需要患者列表 | OpenAPI 中以一个共享 Schema 组件表示 | M3 | 【直引】 |
| AC-API-03 | 检查 OpenAPI | 含 baseUrl 变量的 servers、securitySchemes、每个操作的 Registry 引用 | M3 | 【直引】 |
| AC-FE-01 | 模式 B 项目部署证据齐全 | 开发包生成，完整性检查全部通过，页面 FRONTEND_READY | M3 | 【调整】 |
| AC-FE-02 | 任一 FRONTEND_READY 页面 | 具备页面规格、API Intent、UI Intent、字段映射、请求响应示例及已部署依赖 | M3 | 【直引】 |
| AC-FE-03 | 检查开发包 | 不含凭证或真实个人数据；含连接说明、示例配置与 CORS 说明 | M3 | 【直引】 |
| AC-CAP-01 | 同一版本面向 v2.42 环境编译 | 使用该环境 Profile 编译与校验，差异在 Diff 中展示 | M4 | 【调整】 |
| AC-ENV-01 | 同一版本编译到 TEST 与 UAT | 环境无关对象 UID 一致；REFERENCE 条目按各环境发现结果解析 | M4 | 【直引】 |
| AC-DATA-01（S2 部分） | 导入另一部署后以相同构建上下文重新编译 | 输出哈希一致 | M2 | 【调整】 |
| BP-AC-01 | Patch 直接修改某条目 UID | REJECTED | M1 | 【新增】 |
| BP-AC-02 | Patch 触及草稿阶段范围外的分节 | REJECTED | M1 | 【新增】 |
| BP-AC-03 | 冻结后草稿又被追加 Patch，以旧哈希提升 | 提升被拒绝 | M1 | 【新增】 |
| BP-AC-04 | 仅修改描述与翻译 | 变更级别 Cosmetic，修订号递增 | M1 | 【新增】 |
| BP-AC-05 | 改变既有条目的 Disposition | 变更级别 Major | M2 | 【新增】 |
| BP-AC-06 | 低于阈值的决策无确认记录 | 冻结报告 BLOCKING | M2 | 【新增】 |
| BP-AC-07 | 被拒绝 Patch 中分配过的 UID | 状态 RETIRED，后续分配不会复用 | M2 | 【新增】 |
| BP-AC-08 | 两个项目在同一环境 CREATE 同一 UID | 所有权冲突 BLOCKING | M2 | 【新增】 |
| BP-AC-09 | ProgramRule 条件语法错误或引用未知 businessKey | 静态校验 BLOCKING | M2 | 【新增】 |
| BP-AC-11 | UID 池为空时提交含新 CREATE 条目的 Patch | 以可重试错误 `UID_POOL_EXHAUSTED` 拒绝；补充池后重试成功，UID 来自 DHIS2 生成接口 | M2 | 【新增】 |
| BP-AC-12 | 以 VALIDATION_REPORT 与 DHIS2_IMPORT_REPORT 调用问题查询并按 `level=WARNING` 筛选 | 只返回两类报告中的 WARNING，字段结构一致，级别字段为 `level` | M2 | 【新增】 |
| BP-AC-13 | 读取某环境的绑定 | 每条绑定含 `observedHash` 与 `discoveryResultId`；一次调用可返回多个环境 | M2 | 【新增】 |
| BP-AC-14 | 草稿中新增两个条目、修改一个条目后调用草稿 Diff | 返回两条新增、一条更新（字段级） | M2 | 【新增】 |
| BP-AC-15 | 项目有打开草稿时按项目查询活动草稿 | 返回草稿 ID、基准版本、阶段与哈希；无草稿时返回空 | M1 | 【新增】 |
| BP-AC-16 | 读取一次环境绑定编译的依赖图，并按某 DHIS2 类型筛选再读一次 | 节点与边与元数据包中的依赖一致；两次读取中同一节点的入度、出度、孤立状态相同（均按完整图计算）；节点只含 Registry 条目 | M2 | 【新增】 |
| BP-AC-17 | 基线编译尚未完成（状态非 COMPLETED、为 HANDOFF 构建或未产出图）时读取依赖图 | `NOT_FOUND`，`details.reason = NOT_GENERATED`；构建 ID 从未分发时 `reason = NOT_EXIST` | M2 | 【新增】 |
| BP-AC-18 | 读取开发包文件清单与其中一个文件 | 清单含路径、类型、大小、哈希（按路径排序）；文件内容哈希与清单一致 | M3 | 【新增】 |
| BP-AC-19 | 以 METADATA_PACKAGE 产物 ID 调用开发包文件清单 | 抛出 `ARTIFACT_TYPE_MISMATCH`，`details` 含 artifactId、期望类型与实际类型 | M3 | 【新增】 |
| BP-AC-20 | 以 `../x`、`.` 段、空串、`/a`、`a\b`、`%`、`#`、含 NUL 或 DEL 的路径读取开发包文件 | 均为 `NOT_FOUND` 且 `reason = NOT_EXIST`，与不存在的合法路径结果相同 | M3 | 【新增】 |
| BP-AC-10 | UAT 中某 REFERENCE 对象按 code 无匹配 | 环境绑定编译报告 BLOCKING；人工设置绑定后重新编译通过，版本号不变 | M4 | 【新增】 |

---

## 12. 里程碑分工

| 里程碑 | S2 交付内容 |
| --- | --- |
| M1 | 分节 Schema、版本、草稿、Patch 校验（不含映射冻结规则）、冻结与提升、变更级别判定、影响分析（分节级）、Fake 实现 |
| M2（MVP） | Registry 全部 Disposition、UID 与 code 分配与冲突检查、环境绑定、所有权冲突、决策完整性、Tier 1 描述器、依赖图、基线与环境绑定编译、Metadata Compiler、三层校验、本地表达式校验、Diff、复现、导出与导入、影响分析（条目级） |
| M3 | OpenAPI、UI Spec、Fixture、Package Compiler，开发包完整性检查，交付模式升级 |
| M4 | 多环境绑定的人工设置流程、按不同 Profile 重编译与差异 |
| M5 | Tier 2 / 3 描述器、DataStore Compiler、前端起步工程生成、从既有元数据反向构建 Blueprint |

---

## 13. 未决问题

无。（“从既有元数据反向构建 Blueprint”属于 M5，其需求在 M5 启动前另行补充。）

---

## 附录 A：分节、阶段范围与来源阶段

| 分节 | 可修改的草稿阶段范围 | 问题来源阶段 |
| --- | --- | --- |
| project | 仅初始化与交付模式升级（SYSTEM） | REQUIREMENTS |
| requirements | REQUIREMENTS | REQUIREMENTS |
| domain、useCases、actors | DOMAIN | DOMAIN |
| organizationModel、accessModel、architecture、mapping、registry、rules、environmentConstraints、generationPolicy | MAPPING | MAPPING |
| fixtures | MAPPING（模式 A）、PAGE_DESIGN（模式 B） | 同左 |
| pages、apiIntents、uiIntents | PAGE_DESIGN | PAGE_DESIGN |

经已批准 CR 执行时，草稿阶段范围为 CR 最早受影响阶段及其后全部阶段对应的分节。

## 附录 B：变更级别判定细则

| 变更 | 级别 |
| --- | --- |
| 修改 description、displayName、shortName、formName、翻译、注释 | Cosmetic |
| 新增 Registry 条目（CREATE / REUSE / REFERENCE）且不改变既有条目 | Minor |
| 新增页面、API Intent、UI Intent、规则、指标 | Minor |
| 修改 Fixture 规格 | Minor |
| 交付模式 A → B | Minor |
| 修改既有条目的 dhis2Type、uid、disposition、controlledFields | Major |
| 修改领域实体、关系、生命周期 | Major |
| 修改 Program 结构（Stage、属性归属、Tracker / Event 类型） | Major |
| 修改 accessModel | Major |
| 修改既有 API Intent 的 resource、method、参数或返回字段语义 | Major |
| 删除任何条目或将其置为 DEPRECATED | Major |

## 附录 C：S2 产生的审计事件

`bp.blueprint.initialized`、`bp.patch.submitted`、`bp.patch.rejected`、`bp.draft.reset`、`bp.draft.frozen`、`bp.version.created`、`bp.env_binding.set`、`bp.build.completed`、`bp.build.failed`、`bp.validation.completed`、`bp.handoff.checked`、`bp.project.imported`、`bp.project.data_deleted`。

## 附录 D：源文档引用表

| # | 文档 | 类型 | 引用范围 | 处理方式 |
| --- | --- | --- | --- | --- |
| 1 | 总体 SRS V1.4.0 | 总体需求规格 | 5、6、9.1、9.3～9.4、10、12、13.1～13.4、15.5、17、22.3～22.4、22.7～22.8、25.1 | 抽取改写 |
| 2 | 总体 SRS V1.4.0 | 总体需求规格 | 9.3 映射规则、10.3 UI Intent 要素、17.1 开发包目录结构、12.4 元数据包结构 | 章节引用 |
| 3 | 总体 SRS V1.4.0 | 总体需求规格 | 20.4 Fixture 数据 | 注释引用（仅生成部分） |
| 4 | 00 总体架构与集成约定 V1.3.0 | 共享知识库 | 全文 | 全文引用 |
| 5 | S0 公共基础库 SRS V1.0.2 | 子系统需求规格 | 公共 API 概要 | 章节引用 |
