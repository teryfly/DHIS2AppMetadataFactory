# CHANGELOG — S2 公共库 API（`factory_blueprint.api`）

版本号即 `pyproject.toml` 的 `project.version` 与 `factory_blueprint.__version__`。
不兼容变更提升主版本，旧主版本至少保留一个里程碑（`00` 文档 6.1）。
`0.2.0` 是依据 `S2_Blueprint与构建_SRS.md` V1.1.2 → V1.1.4 补上三条 7.5 读取的一轮；
期间对五个问题作出的裁决定为本文件的最终形状（`0.2.0` 尚未发布给任何调用方，故不另起版本号）。

## 0.2.0 — 依赖图读取与开发包文件读取（依据 SRS V1.1.4 BP-TD-009、BP-HO-004；上位 00 文档 V1.2.4）

SRS 7.5 表把三条函数标为「外部 I/O（读产物内容）」，而 S2 此前没有任何一条兑现：依赖图只有
按版本的边读取（`get_object_relationships`），开发包则任何人都只能自己解 ZIP。本轮把产物
格式收在 S2 里，调用方只看到 Pydantic 模型，并由此成为依赖图的唯一提供方（CP-UI-020）。

### 破坏性变更

无。三条函数、七个模型与四个替身预设位全部是新增；任何既有公共函数的签名与语义未变
（`get_object_relationships`、`get_package_manifest` 与 `PackageFile.role` 一律不动）。

### 新增

- **`get_dependency_graph(build_id, filters=None)`**（BP-TD-009，验收 BP-AC-16/17）：按构建 ID
  返回 `DependencyGraph`——`GraphNode`（businessKey、DHIS2 类型、UID、Disposition、是否托管、
  是否入载荷、**入度、出度**）、边复用既有 `ObjectRelation`（引用方、被引用方、引用字段）、
  `orphans`、`unresolved_registry_refs`（`UnresolvedGraphReference`）。基线编译与环境绑定编译
  都适用。依赖图是**元数据对象图**：节点只含 Registry 条目，页面／API Intent／UI Intent／决策
  之间的追溯关系属于来源追溯，不属于它（BP-TD-006）。
- **完整图算法**（BP-TD-009 第 3 项）：入度、出度、孤立与断点由编译器在完整图上算出并写进产物，
  `GraphFilters(dhis2_types, business_keys)` 只裁剪返回的节点与边——同一节点在筛选视图与完整
  视图里的度数与孤立状态相同（BP-AC-16）。两维度各自收窄（AND）、集合内任一命中；保留的节点
  带来它的一跳邻接边，边的另一端可以落在视图之外；`None` 或空集合不收窄。
- **`list_package_files(artifact_id)`** 与 **`read_package_file(artifact_id, path)`**
  （BP-HO-004，验收 BP-AC-18/19/20）：`PackageFileListing` 给出 `PackageFileEntry` 的路径、
  `kind`（`PACKAGE_FILE_KINDS`：DOCUMENT／CONTRACT／UI_SPEC／REGISTRY／EXAMPLE／FIXTURE／
  I18N／TRACEABILITY）、字节大小与内容哈希；`PackageFileContent` 给出文本内容、媒体类型、大小
  与哈希。清单与单文件读取由同一份字节算出哈希，二者不可能不一致。
- **错误各有归属**（BP-HO-004 第 1、2、4 项，BP-TD-009 第 5 项）：构建或产物不存在 →
  `NOT_FOUND` 且 `details.reason = "NOT_EXIST"`；构建存在但未产出依赖图 → `NOT_FOUND` 且
  `reason = "NOT_GENERATED"`；产物类型不是 FRONTEND_PACKAGE → `ARTIFACT_TYPE_MISMATCH`，
  `details = {artifactId, expectedType, actualType}`；`path` 为空、含 `..` 段、以 `/` 开头、
  含反斜杠、`%`、`#` 或任何控制字符 → `NOT_FOUND` 且 `reason = "NOT_EXIST"`，与「路径不存在」
  完全不可区分。传错类型是调用方的错误，所以不伪装成「还没生成」；「项目尚无开发包」由 S1 判定
  （CP-UI-021），S2 不表达。已清除的产物仍返回 `ARTIFACT_PURGED`，不折叠进 NOT_FOUND。
- **`factory_blueprint.testing.FakeBlueprintApi`** 四个预设位：`builds`（替身分发出去的构建）、
  `graphs`（按 build id 预设的依赖图）、`packages`（按 FRONTEND_PACKAGE artifact id 存
  `{路径: 字节}`）、`non_package_artifacts`（存在但不是开发包的 artifact id → 实际类型）。
  构建一经分发就记入 `builds`，而 `graphs` 默认为空，所以「编译过了但还没有依赖图」是替身的
  **默认**状态，S1 不写任何预设就能测 BP-AC-17。替身调用生产的 `narrow_graph`、
  `package_file_kind`、`content_hash`、路径校验与同一批错误，不复实现，因此 BP-AC-18 的哈希
  一致性与 CP-UI-021 的类型错误映射在替身上同样可测。
- **`dependency-graph.json` 格式扩展**（S2 内部格式，见「已知取舍」）：新增顶层
  `schemaVersion`、`orphans`、`unresolvedRegistryRefs`，节点新增 `inDegree`／`outDegree`。

### 已知取舍

- **产物字节变了，快照需评审**（BP-NFR-07；BP-AC-16 要求节点与边与元数据包中的依赖一致）。
  重录 `tests/snapshots/products.json` 后 6 个钉住值改变、2 个新增：
  - `baseline/dependency-graph.json`、`environment/dependency-graph.json` —— 图多了度数、
    `orphans` 与 `unresolvedRegistryRefs`；
  - `baseline/metadata-baseline.json` —— 同一份图嵌在它的 `dependencyGraph` 键下；
  - `baselinePackageHash`、`environmentPackageHash` —— 包哈希覆盖全部文件；
  - `environment/metadata_manifest.json` —— `compiler_version` 升到 `0.2.0` 后进入兼容性记录
    （BP-NFR-08）；
  - 新增 `baselineGraph`、`environmentGraph` —— 把公共读取的答复本身钉进快照，与文件哈希并排，
    这样「读取与产物不一致」在评审里被发现，而不是在集成时。
  `metadata.json`、`views/*`、`README.md`、`change-summary.json` 未变。
- **`compiler_version` 升到 `0.2.0`**（`config.py`），因为编译器输出变了。代价：S2 0.1.0 时期
  持久化的构建记录在新代码下 `reproduce()` 会报 `consistent=False`——这正是它该说的，旧记录是
  旧编译器写的。`package_template_version` 保持 `0.1.0`，开发包模板本轮未变。
- **`ARTIFACT_TYPE_MISMATCH` 只在缺位时补登记**。SRS V1.2.4 的 FD-COM-001a 把这个码归给 S0，
  BP-HO-004 同时规定「S2 不得自行登记该错误码」；但当前交付的 S0 `error_catalog` 还没有它，
  抛出未登记的码会让 retryability 与 S1 的映射失去依据。因此 S2 的登记改为**已存在则跳过**：
  `registries.S0_PENDING_ERROR_CODES` 单独列出这类「S0 拥有、S2 暂代补位」的码（owner 记为 `S0`，
  不可重试），`bootstrap._pending_error_codes()` 只在目录里查不到时才注册。初始化顺序是 S0 先、
  S2 后，所以新版 S0 上线后 S2 自动不写任何东西，也不会覆盖 S0 声明的所有者与可重试属性
  （`test_initialize_never_restamps_a_code_s0_declared` 用一个可重试的探针值证明这点）。
  **待办：S0 补丁版本（含 FD-COM-001a 者）发布后删除 `S0_PENDING_ERROR_CODES` 里的这一项**，
  代码内已用 `TODO(S0 >= 0.2.0 …)` 标注。
- **图上的断点不是校验问题**。`unresolved_registry_refs` 只表示「元数据字段引用的目标
  businessKey 不在 Registry 中」，是图上的标注；跨分节的引用问题（pageKey、intentKey、
  decisionId）以校验问题为准（BP-VAL-030）。校验器侧的对应集合是
  `patch/businesskey.unresolved_references`（规则 `BPV-BP-002`），两者重叠而不相等，不得并列
  成两份问题清单（UM-CAP-002）。
- **参数名**：V1.1.2 的 7.5 表曾写 `package_artifact_id`，而 S1 与 S4 代码里该名已指交给 S4
  提交的 `METADATA_PACKAGE`；V1.1.3 已把表改成 `artifact_id`。可列出的类型集中在
  `artifact_reads.LISTABLE_PACKAGE_TYPES`（当前只有 `FRONTEND_PACKAGE`），后续里程碑接元数据包
  时不改公共签名。
- **S1 的自算对象图须移除**：S1 的 `object_graph` 曾自行由 Registry 与边计算节点、度数与
  `orphan`，并从筛选后的边集算孤立。CP-UI-020 现在规定 S2 是唯一来源、S1 只做 camelCase 投影，
  且端点新增 `blueprintVersion`（取该版本最近一次基线编译的构建）。S2 侧本轮已提供度数、
  `orphans`、`unresolved_registry_refs` 与按构建的读取；S1 侧的清理不在本仓库范围内。

## 0.1.0 — M1…M3 基线

Blueprint Schema 与版本、工作草稿与 Patch、变更级别与影响分析、Metadata Registry 与 UID
池、类型描述器、基线与环境绑定编译、三层校验、对象级 Diff、OpenAPI／UI Spec／Fixture／
Package 编译器、开发包完整性检查、导出与导入。
