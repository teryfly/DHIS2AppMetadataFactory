# CHANGELOG — S5 Factory UI

版本号即 `package.json` 的 `version`；界面消费的契约版本另注在每一条里（S5IF-G11 的追溯方式）。
破坏性变更（改变既有界面承诺的语义）提升主版本。

## 未发布 —— 阶段问题改成整页问卷（契约 1.19.0；包版本仍为 1.13.0）

`package.json` 的版本未改，仍是 **1.13.0**；`contract/openapi.json` 与生成的类型同步到 **1.19.0**（147 条路径）。

### 新增

- **阶段问题从一题一答改成整页问卷**（UI-Q-004、CP-Q-005）。此前每个问题各提交一次，模型在第一题
  答完后就继续往下跑，后面的题是对着一个已经走开的 Agent 答的；建议也只能选一条或写一段字，模型
  给出的多个候选没有地方并排比较。现在阶段页把该阶段全部 `OPEN` 问题放在一张卡片里：
  - 逐题显示严重程度、单选/多选标记、阻断/选填标记与影响对象；建议显示文本、理由、是否改变 UID、
    需重新生成的产物，`recommended=true` 的标「推荐」。
  - `QuestionView.answerMode = MULTIPLE` 用复选框、`SINGLE` 用单选按钮，每题都有「其他」自由填写；
    单选时二者互斥，多选时自定义文本可以与所选建议同时提交。
  - 阻断问题未答、或「其他」文本为空时，该题标红，整页提交按钮不可用；跳过阻断题不再依赖操作者自觉。
  - 整份问卷一次 `POST /api/v1/questions/answers` 提交，S1 在一个事务里记录全部答案并**最多 resume
    一次**；幂等键的 intent 含请求体摘要，改过的答案是新动作，不是旧提交的重放。
  - 响应里的 `questions` 是 resume 后仍开放的问题；界面失效问题、阶段、审批与允许动作查询，把下一轮
    直接渲染在同一张卡片里，并提示新问题数或「已全部回答」——不再要求刷新页面或再猜一次读取。
  - 运行与作答携带 `Accept-Language`，新一轮问题使用当前界面语言；已存在的问题文本不改写。
  - 已提交的题离开问卷，界面不提供修改入口（UI-Q-003）；问卷草稿只存在组件状态，离开页面即丢弃。

### 补齐历史

- **已登记的模型可以改，而且只发改动过的字段**（UI-SET-005a、S5IF-GOV06）。此前模型列表只有登记与
  启停，改一个单价要重新登记整行；而登记路径会把没给的 `endpoint`/`apiKeySecretRef` 写成空值，覆盖
  一旦被清掉就再也回不来。现在「编辑」带着这一行存进去的值打开，提供方与模型标识锁死；PATCH 只包
  含改动的字段，`endpoint` 与 `apiKeySecretRef` 永不出现在差量里；一个字段都没动时保存按钮不可用，
  保存后列表重新读到那一行。启停开关与编辑共用 `governance.model.enable` 这一个动作。

## 1.13.0 — 上传终于能落库、上传的文档点得开、提供方能测连接（契约 1.16.0）

### 修复

- **业务输入上传在真实部署里从来没有成功过**。`api/upload.ts` 的 XHR 绕过类型化客户端，也就绕过了
  它注入的两个头：`X-CSRF-Token` 缺失时 S1 对 cookie 会话的每个写请求回答 403 CSRF_FAILED
  （`rest/middleware.py`，同一份 multipart 加上头就是 201）；`X-Operator` 原样发出时，中文操作人名
  会让 `setRequestHeader` 抛出，请求根本没发出——现在走 `client.ts` 的 `operatorHeader()`（CP-API-002）。
  桌面端把 `upload.error` 渲染在行内，所以操作员至少看得见一句"权限不足"，看不到的是这两条都是
  客户端的错。文件的头部注释本来就在列举"重新挂上的头"，唯独漏了 CSRF——漏写的那一行正好是没人看的注释。
- **已上传的文档在审阅列表里点不开，点开也读不了**。`inputs/InputsView.tsx` 的文件名是一段纯文本，
  而 `/projects/:id/artifacts/:id` 与下载链接早就存在；现在文件名就是入口，并带上 `?name=`，因为
  产物记录本身不保存上传文件名（`artifact_view()` 给类型、媒体类型、大小与哈希）。
  `ArtifactReview.tsx` 过去把任何字节都用 `res.text()` 读成字符串再塞进 `<pre>`：一份 .docx 于是是一屏
  替换字符，看着像文档坏了，而不是一台设备解不了它。现在按媒体类型判断（`lib/media.ts`），
  判不了就按扩展名，非文本一律给出类型、大小与「下载」，而且**不再下载**那些不打算渲染的字节。

### 新增

- **提供方「测试连接」**（S5IF-GOV20）。治理页的提供方表格每行一个按钮，问一次 `GET /models`（S3 的
  `health()`，5 秒预算，不产生调用）。这一步的意义是把 `model endpoint unreachable: ConnectError`
  拆成三种可修的问题：地址没人填、密钥被供应商拒、模型比请求超时慢——以前只有等一次解析失败、再去翻
  模型调用表才能分清。

### 契约

`contract/openapi.json` 与生成的类型同步到 **1.16.0**（S1 新增 `POST /governance/providers/{name}/test-connection`，
路径 143 → 144）。

## 1.12.0 — 审批页不再停留，断线不再白补拉（真实缺陷修复）

### 修复

- **批准/驳回后 GatePage 停在旧状态**。`qk.gate(gateId)=['gates', id]` 与 `['workflows', wf, 'gates']`
  是两棵键树：`useApproveGate`/`useRejectGate` 只失效后者，SSE 的 `gate.*` 分支同样不碰详情键，
  于是审阅人按完按钮，页面还在念 `WAITING_APPROVAL`（包括别人批的 Gate——事件到了也不刷新）。
  两条 mutation 与 `gate.*` 事件现在都失效 `['gates', gateId]`；`mutations.test.tsx` 把这条断言钉住。
- **`stream.reset` 与断线重连补拉是空操作**。`projectWide()` 返回裸 `[projectId]` 前缀，而所有查询键
  都以域词开头（`['projects', …]`、`['workflows', …]`），前缀匹配不到任何东西——UI-RT-003 承诺的
  "整项目重取"其实什么都没取。改用 `queryKeys.ts` 里早已备好、却零使用的 `projectRootKeys`，
  并补上门槛细节键（gates/stages/deployment-runs/artifacts/questions/executions）。
- **Gate 六问里有两问渲染同一份数据**：`GatePage.tsx` 的"发现了什么"与"还有什么不确定"都读
  `warnings.map(w=>w.message)`。现在两问接 `GET /gates/{id}/explanation`（契约里一直有，界面从未调过）：
  found 读绑定对象，uncertain 读未决问题，读不到时退回警告清单。
- **多人审批进度在桌面永远不可见**：`requiredApprovals`/`approvals` 从未被读取。审批区现在给出
  「已记录 x/y」与逐条决策（人名 · 中文决策词 · 时间）。
- `StageView` 的策略拒绝横幅把 `rejectedArtifacts` 直接 `.join(', ')`——契约里它是对象数组，
  屏幕于是念 `[object Object]`；改取对象内的产物 id。
- 部署控制台把"尚未评估"渲染成失败：`s.connection?.ok ? 'PASSED' : 'FAILED'` 里 slot 为 null 也算失败。
  连接与校验两行改三态，null 显示 `PENDING`。
- **「打开阶段」弹的两条错误**（S1 侧另记）。其一是真故障：`GET /stages/{id}/chat` 在真实提供方链路上
  必然 500，界面于是念"服务内部错误，请提供关联 ID"。其二是把合法答案当故障：项目的首个设计阶段没跑，
  就没有草稿可读，S1 以前回裸 404 —— 而 `ManualAuthoring`/`EntriesCard` 早就按"无草稿"渲染，唯一后果是
  一条没人能处理的弹窗。S1 这次按 CP-UI-022 回 `reason=NOT_GENERATED` + `requiredStage`，
  `blueprintDraftOptions` 据此解析成 `null`（不是抛错，也不进全局 toast），其余 NOT_FOUND 照旧上报。
- **聊天面板不再挂在没有对话的阶段上**：它此前对任何当前阶段都挂载，而 AI-CHAT-001 把对话限定在四个
  设计阶段，工作流停在 DISCOVERY / 编译阶段时那条读取只会再弹一次"对象不存在"。挂载条件改读
  `hasDialogue(stageType)`（与 S1 `domain/enums.py:DIALOGUE_STAGE_TYPES` 同集合）。

### 新增

- `useWorkflowEvents.test.tsx` 两条：`stream.reset` 必须失效到项目根与工作流根（旧断言恰好固化了
  那个不匹配的前缀）；`gate.approved` 帧必须达到 `['gates', gateId]`。
- mocks 补 `GET /gates/:id/explanation`。
- `queries.test.tsx` 两条（走 `createQueryClient` 的 error bridge）：S1 回引导式 404 时草稿读取解析为
  `null` 且**不**触达 `onApiError`；没给 `reason` 的 NOT_FOUND 仍然要弹。
- `stage-chat.test.tsx` 一组 `hasDialogue` 断言：四个设计阶段为真，DISCOVERY/BASELINE_COMPILE/HANDOFF
  与 undefined 为假。

## 1.11.0 — 中文界面按 §2 术语表统一（承 1.10.0，同一裁决的第二遍）

### 变更

- **35 条中文文案**把后端词换成了界面词：`Blueprint`→**蓝图**、`Registry`→**登记表**、`Gate`→**审批节点**、
  `Prompt`→**提示词**、`Patch`→**修改提案**、`Secret 引用`→**引用 / 凭证引用 / 密钥引用**。英文列不动——
  `Gate`、`Blueprint` 在英文界面本来就是正确的词；问题只在于它们混在中文句子里，读起来像一个没人教过
  的专名。依据是 `requirements/S5M_移动优先前端_SRS.md` §2 那张表（本轮升为**两端共用**，V1.4.2，并补了
  `Prompt`、`Secret` 两行），移动端早已按它写，桌面端这次对齐。
- 几处顺手改成人话：`secrets.refHelp` 直接写「只接受引用（env://… 或 db://…），不接受值本身」，把用户真正
  要填的形式写在句子里；`noPromptsBody` 原来一句里同时出现 `Prompt` 和「提示词包」，现在统一；
  `secretsOnlyBody` 的「保险库」统一成界面其余部分用的**凭证库**；`MAPPING_FROZEN_BY_GATE` 从「已被已批准的
  Gate 冻结」改成「已被已批准的节点冻结」，免得「审批」在一句里出现两次。
- `env://…`、`db://…`、`API key`、`UID`、`Webhook`、`DHIS2` 保留：前者是要用户照抄的字面量，后者是没有中文
  叫法的产品名/缩写。

### 新增

- `messages.test.ts` 第三条守卫：**中文值里出现 `Blueprint|Registry|Gate|Prompt|Patch|Secret`（含复数）即红**，
  只管 `zhCN`，英文值不受约束。改前它报出 24 条，改后归零。

## 1.10.0 — 界面不再念内部编号

### 变更

- **95 条用户可见文案重写**（`src/i18n/messages.ts`，en 47 + zh-CN 48）。屏幕上有两类词一直不属于操作者：
  子系统的编号（`S1`…`S5-M`，它们是 `requirements/` 的坐标）与需求编号（`UI-REV-003`、`CP-PRJ-003`、
  `S5IF-BP17`、`FR-SEC-00x`、`DX-ENV-001`、`BP-REG-010`）。前者一律换成操作者自己的工作语言——服务端不点名
  就句子自足，必须点名时说「服务端」；三项能力叫「蓝图编译 / 需求解析 / 环境执行」；后者退回**同一个键上方的
  注释**，可追溯性留在代码里而不是屏幕上。同一批句子里夹着的 `PROJECT_EXPORT`、`PHI_PII`、`RESTRICTED`、
  `DISCOVERY`、`Monaco`、`M4` 一并处理；`env://…`、`db://…`、`FACTORY_CREDENTIAL_MASTER_KEY` 是**要用户
  输入或去改的字面量**，保留。每条文案的事实承诺不变（"不会回传值"、"清单来自构建记录而非浏览器解析"、
  "替身产出不可交付"这些句子都还在，只是换了主语）。
- **`GET /setup` 的 `providers` 不再原样插进文案**（`src/features/setup/SetupView.tsx:47,50`）。后端把三个端口
  叫作 `S2`/`S3`/`S4`、把接线叫作 `real`/`fake`，此前「开始使用」第一屏读出来就是
  `S1 报告：S2:real · S3:real · S4:real`——新操作员看到的第一个"事实"。现在过 `capabilityEntry` 映射。
- 步骤名「子系统接入」→「能力接入」（en `Subsystem wiring` → `Capabilities`）。
- 审计表的 `sourceSubsystem` 与产物详情的 `creatorSubsystem`（`AuditView.tsx:126`、`ArtifactReview.tsx:218`）
  过去把后端值直接打印，现在过标签映射，`'—'` 守卫留在外面，空值不会被映射成任何词。

### 新增

- **`src/lib/labels.ts`**：`capabilityLabel` / `modeLabel` / `capabilityEntry`。写在 `switch` 里而不是
  `Record` + 模板键，是因为 `keysUsed.test.ts` 只认字面量 `t('…')` 调用——模板键在它眼里不存在，一个拼错的键
  会把 `capabilities.blueprint` 这样的字符串原样甩到屏上。**未知取值回落原 token，绝不回空串**：契约里
  `providers` 是 `{[key: string]: string}`（`schema.d.ts:6991`），一套新部署可以带着本文件没见过的键来，
  空行读起来像"没有这项能力"，而真相是"不认识这个标签"。
- `messages.ts` 里 `capabilities` 一块（en/zhCN 同次编辑，`export type Messages = typeof en` 让单边加键变成
  编译错误）。
- **两条守卫**（`src/i18n/messages.test.ts`）：① 遍历两个语言的全部叶子值，命中子系统编号、需求编号、
  `M1…M5`、「子系统」/subsystem、以及那批句子内部词即失败；② 每个键的 `{{占位符}}` 多重集在两种语言里
  必须一致。此前只有"括号语法"检查（`{{x}}` vs `{x}`）和 `gate.requiredApprovals` 一条抽查——**单边漏改一个
  占位符是全绿的**，而它的症状正是那句注释写的"顶栏读成 `expires {at}`"。正则必须带边界：`DHIS2` 里就含 `S2`。
- **语言切换会重算**的一条断言：步骤表 `useMemo` 的 deps 里有 `t`，这件事此前没人钉。
- 随附 `使用说明.md`（仓库根）：面向使用者的操作说明，按任务组织，零子系统编号、零需求编号、零命令行。
  `user-guide.md` 里三处引用界面标签的句子（「子系统接入」、排障表教人去找 `S3:fake`）按新名字改写，其余
  S1–S4 用词保留——那是操作员的行话。S5-M SRS §2 那条"详情页显示系统代码"随之改成"只显示业务枚举取值"
  （V1.4.1）。

### 不在这一条里

产物类型（`BUSINESS_INPUT`/`ENV_PACKAGE`）、Gate 类型、阶段状态徽章这类**直接打印的业务枚举**没有标签化，
中文值里未翻译的 `Blueprint`/`Registry`/`Gate`/`Prompt`/`Secret` 也照旧——那是更大的一遍，不在"子系统编号"
这条范围内。

## 1.9.1 — 消费的契约版本从此有地方住了（S5IF-G11）

### 新增

- **`src/test/contractVersion.test.ts`**：断言 `contract/openapi.json` 声明的版本与路径数就是签入的
  `src/api/generated` 所依据的那一版（现在：**1.14.0 / 143 条路径**）。
  这一条补的是一个具体事故留下的空白：生成文件是**签入并被编译**的，所以 vendored 契约落后于 S1 实际服务的版本时
  `tsc` 依旧全绿 —— 差别只是"这个字段在这里不存在"，症状要等到运行时才看得见。`install.sh` 已经会在构建前
  比对生成文件；这个测试钉住另一半：**版本号**本身。两处数字各写一份是故意的 —— 手改 vendored 文件可以带着
  正确的版本串换来错误的面。S1 侧对同一个号有 `tests/api/test_openapi_contract.py::test_document_version_and_servers`，
  哪天契约提版而这里没动，红的是两端各自最不该忘的那两个人。
- 纯测试改动，不进 bundle（`tsc --noEmit` 与 `vite build` 都不编译 `*.test.ts`，项目里也没有把版本号注入前端的
  `define`/`pkg.version`），所以**不要求重新部署**。

## 1.9.0 — 变更申请可以自己声明要作废哪些对象（契约 1.14.0，依据 CP-CR-005、总体 SRS 15.6）

### 新增

- **新建变更申请的对话框增加"本次请求要作废的对象"**。定向删除是 15.6 的三种恢复策略之一，而授权只可能来自
  请求本身：S2 的影响集里没有删除字段，S1 也就不再有任何地方能说出"这次要退役哪几个对象"——此前
  `deletionCandidates` 只能由影响分析回填，真身下于是恒为空，`delete-objects` 永远拿不到可提交的载荷。
  这一栏留空即与旧行为一致（不做任何定向删除）；填了不是 DEPRECATED 的名字会被 S1 当场 422，
  提示里带着对不上的键名（`details.notDeprecated`），不会像过去那样等到编译期被静默剔除。
- 影响面板本来就显示 `deletionCandidates`，所以现在"授权了什么"在同一屏上看得见。
- `src/api/mutations.test.tsx` 增加一条：创建请求的 body 必须带上 `deletionCandidates`。

## 1.8.0 — 一次被答复的拒绝不再把这个动作永久钉死（契约 1.13.0，依据 CP-API-003、UI-REQ-001、UI-BR-04）

### 修复

- **`useInvalidatingMutation` 现在在服务器答回一个拒绝后释放幂等键**。S1 对已完成的键**逐字节重放首次响应**，
  而同键换一个改过的请求体则是 422 `IDEMPOTENCY_CONFLICT`（CP-API-003）。也就是说"被答复的拒绝"已经把这把键
  用掉了：攥着它不放，同一个按钮此后每次点击都回到那条旧结论，操作者在这一页里再也做不动这件事——
  键存在 sessionStorage 里，连刷新页面都解不开，只能另开一个标签页。此前键只在 `onSuccess` 释放，
  所以只有 COMMIT 一条路走对过（`useCommitRun` 一直在非网络错误时释放）。
- 两条路径现在共用同一个判据 `keepsIdempotencyKey`：**只有没人给出答复的才保留键**——网络失败
  （请求可能已经落地，UI-REQ-004 要的是"结果待确认"而不是自动重提），以及 `REQUEST_IN_PROGRESS`
  （服务器还在处理这一条本身；这时换新键等于同时跑两遍同一个动作，CP-GATE-018 对 COMMIT 明令禁止）。
- **intent 由变量带进来的钩子补上释放**。`patch:{stageId}`、`answer:{questionId}`、`run:{workflowId}`、
  `parse:{artifactId}`、change request 的每一步——它们把 intent 塞进 `mutatingHeader(intent)`，
  却没有在 helper 上声明 `intent:`，于是连成功都不释放键。helper 现在从变量上把它读回来。
  （在 `mutationFn` 里用 `actionIntent(...)` 现拼的那批不受影响：它们把请求体的摘要折进了 intent，
  改过的请求本来就是另一把键。）
- `src/api/mutations.test.tsx` 新增四条：422 与 409 之后键必须换，`HttpResponse.error()` 与
  `REQUEST_IN_PROGRESS` 之后键必须不变。

## 1.7.0 — 产物内容有一个真的下载地址，而且它是否存在由 S1 说（契约 1.8.0，依据 CP-UI-016、UI-BR-01、FR-DATA-008）

### 新增

- **产物详情的「下载」**（CP-UI-016）。此前两个控制台都没有产物内容的下载入口：内容就地读进预览，
  `artifact.download` 在 ACTION 表里躺成一个死键，而 S1 侧的 `GET /artifacts/{id}/content?download=true`
  会按 `download=true` 给出带文件名的 `Content-Disposition`（`routes/catalog.py:156`）——地址本来就为浏览器准备着，
  缺的只是一条链接。现在它是 `<a href>`，字节由浏览器自己流式接走，不经过 React 状态。
  能不能显示这条链接仍然只问 allowed-actions：被拒（例如项目已归档）时出现的是 S1 给的原因，
  不是一个点了拿 410 的链接；`contentPurged` 为真时链接不出现——FR-DATA-008 之后已经没有可下载的东西了，
  上面那条横幅把这件事说过一次，这里不再重复一遍。
- **`common.download`**：这一轮"下载"从各页的私有措辞升成公共词条。桌面不做"清除内容"——S5 的 SRS 没有
  这一条（`UI-*` 里只有移动端的 UM-P-04 与第 6.2 节确认矩阵提到它），做一个界面没有要求的破坏性按钮不是补齐。

### 修复

- **e2e 的两个 project 跑的是同一份用例**。`chromium` 与 `a11y` 用同一台 Desktop Chrome、同一视口，
  且都没有 `testMatch`/`testIgnore`，于是 12 条主流程与 30 屏 axe **各跑两遍**——报告里连抄了几轮的
  "84 passed" 其实是 42 条。各加一个过滤器后 42 项 1.5 分钟跑完，覆盖不变，`--project=a11y` 也才真的
  能单独用。两个配置文件（`playwright.config.ts`、`eslint.config.js`）都不进 Vite 构建图，线上字节与
  这次改动无关。
- **`dist-e2e` 没被 lint 忽略**。e2e 的构建产物换目录之后（`dist-e2e/`，为了不覆盖 `dist/`）它落进了
  `eslint .` 的扫描范围，其中 `mockServiceWorker.js` 带着一条自己用不上的 `eslint-disable`，于是每次 lint
  都多出一条与源码无关的 warning。ignores 里把它和 `dist` 同等对待——构建产物不是被审的对象。

## 1.6.0 — 六张裸表有了同一个状态壳，开发包先问 S1 再决定要不要去读（契约 1.8.0，依据 UI-BR-01、UI-REQ-002、UI-WB-006、CP-NFR-09、CP-UI-021/022）

### 新增

- **`AsyncShell`**：桌面版的状态壳（加载 / 被拒 / 为空 / 内容），对应移动端早已统一的 `FuiAsync`。
  接上六张以前"只有表头"的表——成员、受限授权、生产豁免、角色、账号、部署。它们把请求失败和"这里
  还没有东西"渲染成同一个东西：一张空表；操作员看不出是没数据还是没读到。壳的三条承诺是同一套：
  错误带 `correlationId`，重试按钮只在 S1 说这条可重试时出现（UI-REQ-002 一直是 toast 的规矩，页面级
  读取以前没人守），空态给一句解释和一个能把人带去做那件事的链接。
- **开发包页先看 `frontend_package.download`**（UI-BR-01）。这份包存不存在，S1 早在 allowed-actions
  里答了（未生成时是 `PACKAGE_NOT_GENERATED`，`domain/actions.py:695`）；上一轮页面接上了端点，可用性
  却还是从 404 的 `details.reason` 反推的——先发一次注定失败的请求，再把失败读成指引。现在门控关着就
  不发那个请求；门控因为**别的原因**关着（归档项目会把整条转发列表 withhold 成 `PROJECT_ARCHIVED`）时，
  念的是 S1 给的原因，而不是承诺一个永远不会再跑的阶段。404 那一条留着：门控是缓存的，读的一侧仍可
  能落后，两个 arm 各有一条测试钉住。
- **错误码目录补齐 24 条**（CP-NFR-09）。`DX_*`、`DHIS2_*`、`AI_PARSER_*`、`SECRET_IN_USE`、`CSRF_FAILED`
  这些 S1 能回答、界面却没有句子的码，以前落到抛出方写给日志的英文原文上。守护在
  `S1/tests/unit/test_console_error_catalogue.py`：它读四个目录（两端 × 两语）与
  `factory_control.errors` 能发出的码对账——控制台从内部看不见这种缺口。

### 修复

- **`actionReason.PACKAGE_NOT_GENERATED` 上一轮被写进了英文目录，值却是中文**，而中文目录里一个都没有
  （所以英文界面的原因栏里会冒出一句中文）。同一轮还把这条 reasonCode 顺手抄进了 `errors` 目录——它是
  allowed-actions 的原因码，不是 S1 的错误码，放在那里让 `errors` 看起来比实际更全。三处一并改正。

## 1.5.0 — 列表按 S1 的分页读，产物有了自己的地址，开发包这一节终于有人调它（契约 1.8.0，依据 UI-REV-001、UI-IA-001、UI-OPS-002/003、UI-NFR-06、S5IF-G04、CP-UI-021/022）

### 新增

- **开发包（Handoff）一屏**。`GET /api/v1/projects/{id}/frontend-package/files`（CP-UI-021）此前
  141 条路径里躺着、两个控制台一次都没调用过——S1 把 S2 的清单原样转发，界面却没有任何地方读它。
  现在项目工作区多一节「开发包」：逐文件列出 `kind / 大小 / 哈希`，就地读一个文件，下载走
  `?download=true` 的原生链接（浏览器流式下载，比把整个文件搬进 React 状态更诚实）。文件里的
  `kind` 用的是 S2 的词，**不**换成构建包的 `role`：两份包回答的不是同一个问题，把词汇并起来就把
  这个区别藏掉了。完整性检查不在这里复述——它是 HANDOFF 阶段的产出，结论记在该阶段的校验与审批上。
  没有开发包时 S1 回答 404 + `details.reason=NOT_GENERATED` + `requiredStage`（CP-UI-022），界面据此
  给出「由 {stage} 阶段生成」并一步跳过去，而不是一个空白页或一条失败的请求。
- **产物有自己的地址**（UI-REV-001）：`/projects/{id}/artifacts/{artifactId}`。此前"在看哪个产物"是
  组件里的一个 `useState`，于是审核结果无法引用——审阅意见里写"从上往下第三个"不是一条凭据，而
  被 URL 指着的产物是可以抄进工单的。详情头部现在把那行地址写出来并给「复制该产物链接」；血缘里点
  一个来源也走同一个地址，不再是悄悄换掉面板内容。
- **`npm run check:budget`**（UI-NFR-06 的门禁，与移动端的 UM-NFR-02 同一套算法）：读 `dist/index.html`
  实际引用的入口 + modulepreload + 样式 + 同源 public + 内联块，算 gzip 后与 `budget.config.mjs` 里的
  上限比，并落一份 `docs/reports/bundle-budget.{json,md}`。豁免是按名字写在配置里的，每次运行都打印。

### 变更

- **每个界面一块 chunk**。`routes.tsx` 原来是 35 个静态 import：首屏要为一条从没去过的路由买单。
  现在除外壳（AppShell、SessionGate、登录那一进一出）外全部 `lazy()`，外层一个 `<Suspense>`。
  顺手发现一个更隐蔽的：`vite.config.ts` 的 `manualChunks: { flow: ['@xyflow/react'] }` **把图库提进了
  入口的静态图**，于是每屏首帧都要下 57.7 KB gzip 的 React Flow，而全应用只有依赖图那一屏用它。
  去掉这条手工分块后首屏 **246.7 KB → 189.4 KB**，门禁线是 200 KB。
- **长列表按 S1 的分页读，并且把"读了多少"说出来**（S5IF-G04）。审计是游标（`limit`+`cursor`，
  `useInfiniteQuery` + 加载更多 + 「已列出 x / 共 N 条」）；产物列表同样走游标；账号列表按 offset
  翻页；通知的 `mine` / `unreadOnly` 是**服务端筛选**，顶栏的未读角标取 `total`。原来角标是把这一页
  里 `read` 为假的条数当未读总数——对有 200 条未读的人，它说的是 50。
- 界面补漏：`接口`（`api` / `api/doc`）与`开发包`都进了项目工作区的导航条。上一轮加这两页时只加了
  路由，导航里进不去，正是本仓库一直批评的"实现了但没有入口"。

### 修复

- `handoff.notGeneratedBody` 一开始写的是单花括号 `{stage}`：i18next 只替换 `{{name}}`，单括号会原样
  渲染到屏幕上。这条由 `messages.test.ts` 的那条"不许出现裸 {placeholder}"守住了。
- 新增的 a11y 扫描把模式 B 项目的开发包也扫了：`<a>` 里套 `<Button>` 是嵌套可交互元素，改成一条
  普通链接（同时它本来就是浏览器更擅长的下载）。

### 测试

- 单元 334 条（`features/review/handoff.test.tsx` 7 条：产物地址、复制链接、血缘跳转、开发包清单、
  按文件读取只在被打开时才发请求、NOT_GENERATED 引导、导航入口；`features/paging.test.tsx` 6 条：
  审计游标续页与不续页、通知两个筛选各自发出、未读角标用 `total`、账号翻页用 `offset`）；
  端到端 84 条（a11y 扫描按屏拆成逐条用例，多出 `artifacts/{id}`、`handoff` 与模式 B 的开发包三屏）。
  实测：`tsc` 0、`eslint` 0 问题、`vitest` 334/334、`playwright` 84/84、`check:budget` 达标。


### 修复

- **「启用/停用」按钮不发请求**。`usePatchUser(userId)` 把 id 绑在 hook 创建的那一刻，而账号页只有一个
  表格，于是它拿的是占位空串：每一次停用都在向 S1 要一条不存在的路由（`PATCH /api/v1/users/`）。
  这行代码从来没有被任何测试按过——不是"通过了但没生效"，而是**界面上就没有这条路径**。
  现在 id 随调用走（`patch.mutateAsync({ userId, body })`），并由 `admin.test.tsx` 断言请求落在
  `/api/v1/users/USR-ada` 上、body 就是 `{status: 'DISABLED'}`。
- 顺手补上第二件事：**显示名与邮箱建好之后改不了**。S1 的 `PATCH /users/{id}` 一直允许改这两项
  （本轮活体探测就是这么改过探针账号的名字），界面却没有入口。现在账号行上有「编辑」，
  展开一张预填好的卡片；**登录名不在可改之列**——它是审计与审批记录里的那串，改它等于改写历史。
- **「授予角色」的幂等键只算了账号，没算角色**。`mutatingHeader(intent)` 对同一个 intent 返回同一个
  `Idempotency-Key`（`client.ts:61`，这正是 UI-REQ-001 要的），而这里的 intent 是
  `PUT /users/{user_id}/roles` + 用户 id：给同一个人授第二个角色时键与第一次相同，而 S1 对
  "同一个键、不同的请求体"回答 **422 `IDEMPOTENCY_CONFLICT`**（`middleware.py:195` 起，
  文案是"相同幂等键已用于不同的请求内容"）——第二个角色因此**授不出去**，操作者看到的是一条与"我刚刚
  做了什么"无关的冲突。活体探测验到了这一点（第三个角色没有落在列表里，换一个键就落上了）。intent 现在带上请求体的摘要，
  新增的用例连着授两个角色并断言两次的键不同；同一个键留给真正的重试（同内容 → 重放第一次的响应）。
- **授权面板原来只能授组织级角色**：`projectId` 在 mutation 的入参里、在列表的回读里
  （`ROLE · 项目 id`）、也在撤销的请求里，唯独没有地方能指定。现在「项目范围」是一个下拉：
  **组织级（不限定项目）** 加上当前可见的项目。刻意不是自由文本——一个只能从别的屏幕抄进来的 id，
  提供的唯一功能就是猜错，而猜错现在会被 S1 判为 404。
- **端到端跑完会把线上界面变成替身**。`test:e2e` 的 webServer 过去执行 `VITE_ENABLE_MOCK=1 npm run build`，
  而 `VITE_ENABLE_MOCK` 是**构建期**烘进 bundle 的值（`config.js` 只能改运行期那一半），产物写在 `dist/`
  ——部署脚本 `--skip-build` 拷的正是这个目录。本轮真的发生过：`:1583` 当时发的入口文件里烘着
  `VITE_ENABLE_MOCK:"1"`，操作员看到的是一个对着 fixture 跑的控制台，而单测与生产构建全绿，症状上
  和"后端坏了"完全不同。现在 e2e 走 `npm run build:e2e`，产物在 **`dist-e2e/`**，`dist/` 只归生产构建；
  preview 也补了 `--strictPort`，端口被占时直接退出而不是滑到 4174。
  核对上线产物的办法同时改对了：查 `grep -c 'VITE_ENABLE_MOCK:"1"' dist/assets/index-*.js` 应为 `0`，
  原来那句 `grep -o 'enableMock:[!01]*'` 读到的是运行期默认对象，两种构建里都长一样，证明不了任何事。
- **`install.sh` 现在拒绝落地一个测试构建**。拷静态产物之前先查入口 chunk 里的标记（S5 查
  `VITE_ENABLE_MOCK:"1"`，S5M 查 `dev/json-editor`），命中就中止并指出该跑哪条命令。这一层必须自己挡：
  烘进 bundle 的值运行期切不掉，拷错目录不报错，只会安静地把线上界面变成读 fixture 的控制台。
  `dist-e2e/` 从不出现在部署路径里，它只被 e2e 的 webServer 读。本轮部署已走这道闸（`install_exit=0`，
  `install.sh` 的拷贝与 `S5/dist` 逐文件 sha256 相同，线上入口标记数为 0）。

### 新增

- **建号时"首次登录要求改口令"变成勾选**，默认仍然勾上（管理员发出去的是初始口令）。
  此前这里写死 `true`，而移动端写死"不提供该字段"（即为 `false`）——同一个动作在两个控制台得到
  两个结果，两边都还没有开关。CP-IAM-005 ③ 把这个标记定为报告位，所以两种默认都不拦任何人，
  但"谁说了算"应该是操作者而不是界面。
- 「需改口令」的说明文字挪到勾选框下面：它解释的是这个勾选项，不是口令输入框。

### 变更

- 契约升到 **1.7.2**：`SessionView` 关于 `csrfToken` 的说明改对了（该值只由登录类端点给出，刷新后
  走可读的 `factory_csrf` cookie）。只有描述与版本号变化，140 条路径与所有结构一字未动；
  两份 vendored 文档与生成的类型都已重新生成。

## 1.3.0 — 操作者可以自己改口令了（契约 1.7.1 未变，依据 UI-AUTH-001、FR-SEC-001、FR-SEC-005）

### 新增

- **`ChangePasswordDialog.tsx`**（顶栏入口）：本人改自己的口令，走 `POST /users/{me}/password`。
  这条端点一直允许本人调用、不需要 `user.manage`（`rest/routes/identity.py:321-323`），但桌面端**从来没有
  任何地方能调到它**——`useSetUserPassword` 的唯一调用点是管理员重置别人的口令。于是被 S1 标记
  `mustChangePassword` 的账号在这里只有一条路：找管理员。现在两件事都补上了：入口，以及
  `session.mustChangePassword` 为真时顶栏下方的一条提示——一句话说清这个账号还在用初始口令，
  条上就是同一个入口。CP-IAM-005 ③ 把这个标记定为**报告位**（服务端不因此拦任何调用），
  所以"这颗 ⚠ 是什么意思"只有界面能说；只给一个符号，等于让操作者自己猜。
  面板要求两次输入一致（这是唯一一个改完必须重新敲一遍的值），最短 8 个字符的门槛与服务层同源，
  值不进任何存储，成功失败都立刻清掉。
- **改完就把自己登出，并且说出来**。S1 在写入口令时吊销该账号的全部会话
  （`identity/service.py` → `sessions.revoke_for_user`），**包括正在用的这一条**。
  `dropSession()` 因此把 CSRF 值与缓存的会话读数一起丢掉，并置 `lapsed`——那是本应用表示
  "会话不是你自己退出的"的既有信号，`SessionGate` 用它把操作者带回登录页并给出原因。
  没有这一步的话，界面会继续显示你的名字，而每一次写都在被拒。

### 变更

- 顶栏那个新按钮一开始带了个 `aria-label`，读出来是「修改自己的口令」而屏幕上写的是「修改口令」——
  可及名称与可见文字不一致正是 WCAG 2.5.3 的失败项，单测就是按可见文字找它才暴露的。现在去掉 label，
  以可见文字为准。
- `useSignOut` 的收尾逻辑抽成 `dropSession(qc)` 供两处复用：会话结束之后的清理动作如果两份代码各写一遍，
  它们一定会在某一天不一样。
- 被标记 `mustChangePassword` 的账号现在在顶栏下面有一条提示（同一个入口，一句话说明），而不只是入口按钮
  上一个 `⚠`。CP-IAM-005 ③ 把这个标记定为报告位——服务端不因此拦任何调用——所以"这是什么意思"只有界面
  能讲；符号是要求操作者自己猜。
- 替身登录之后给出的角色从 `ENGINEER` 改成 `SOLUTION_ARCHITECT`：前者 S1 根本不发（`identity/roles.py`
  的 `BUILT_IN_ROLES` 里六个名字都没有它），而替身的作用就是照 S1 的形状给数据——一个假角色名教给界面的
  是一套服务端会在边界上拒绝的词汇。

### 门禁

- 单测 +3（`src/components/changePassword.test.tsx`）：写入的地址点名"问的那个人"、两次输入不一致时不给提交、
  改完之后 CSRF 归零且会话被标记为已结束、以及整个过程中文档里查不到那个值。

## 1.2.1 — 三个永远灰掉的页面、一个填不了的本地模型（契约 1.7.1，依据 UI-BR-01、UI-ADM-002/003、S5IF-SEC01、S5IF-GOV02）

这一轮全是审出来的：界面承诺与契约事实不一致的地方。没有一条是新增能力，每一条都在堵"看起来正常、实际不能用"的洞。

### 变更

- **三个动作名的拼法 S1 从未发出过**（`project.member.manage`、`artifact.grant.manage`、`production.exemption.manage`）。
  按 UI-BR-01，`allowed-actions` 里没有这一条 = 控件不可用且不给理由，于是成员、受限授权、生产豁免三个页面对
  平台管理员也永远是灰的——症状完全像"权限不够"，只有把控制台的动词表和 S1 的词表放在一起比才看得出是拼错了。
  现在用 `member.manage` / `restricted_grant.manage` / `production_exemption.manage`；守护测试搬到 S1
  （`tests/unit/test_action_vocabulary.py`），任何一边自造动词都会让 S1 的构建失败。
- **本机推理服务可以不填 Key**（契约 1.7.1 把 `apiKeySecretRef` 改为可省）。目录里 `local` 那几条（Ollama、
  LM Studio、llama.cpp）默认不鉴权，而表单原先坚持要一个引用：唯一的出路是编一个不存在的 `env://名字`，
  登记通过、第一次调用必失败。现在空值就发空值，字段下的说明写的是"不需要鉴权就留空，别编"。
  编辑路径不跟着放松：PATCH 声明 `min_length=1`，清空的那一格不上线（缺席才是"不改"）。
- **MSW 替身里那三个动词也一起改**（`src/mocks/db.ts` 的平台清单、工作流清单与角色表）。名字改了而替身没改，
  测试会照样绿——因为替身就是测试读的那份回答——但它教的是 S1 从不发送的拼法。现在两条清单都按
  `member.manage` / `restricted_grant.manage` / `production_exemption.manage` 回答；豁免那条在工作流清单里
  保持 `available: false, reasonCode: PERMISSION_DENIED`，这正是 `admin.test.tsx` 钉住的形状。
  这三个权利**同时**出现在两条回答里不是冗余：S1 的 `project_actions()` 会按项目过滤它们（CP-IAM-002），
  所以"某人只管这一个项目的成员"是真状态，而平台回答里没有项目可过滤。
- **`useSaveSecret` 不再发 `Idempotency-Key`**。这条路径整段排除在回放表之外（表逐字存响应体，而这里的响应体
  正是那个长期有效的引用），头会被接受、记录、然后忽略；契约 1.7.1 撤掉声明后，生成的类型直接拒绝发送它——
  类型检查在这里就是一次契约审计。可重复性来自 `PUT` 这个动作。
- **系统设置页的保存按钮改走 `AllowedActionButton`（`settings.manage`）**。此前它只看本地表单状态，
  等于这一个控件自己决定自己可用；UI-BR-01 不容许例外。这一条在 S1 那边才有意义：`PLATFORM_ACTIONS`
  原本写的是 `settings.update`（从审计事件 `cp.settings.updated` 的后缀抄来的），而 `PUT /settings`
  按 `settings.manage` 授权——按 UI-BR-01 只能读服务端给出的键，于是这一格在**有权限的人**那里也是灰的。
  S1 本轮把词表里那条改成 `iam.SETTINGS_MANAGE`。
- **凭证录入面板是一处可展开 disclosure，就得按 disclosure 来**：`aria-expanded` / `aria-controls`，
  展开时把焦点放进第一个输入框。之前键盘操作者按下"录入新凭证"之后焦点还留在按钮上，面板出现了也看不见。
- **`/setup` 两个凭证字段的语义分开之后**（`masterKeyConfigured` = 有这一行，`storeAvailable` = 长到能用），
  引导页在模型与环境两屏上对 `storeAvailable=false` 给的是"这套部署没配主密钥，请改用 `env://`"的警告条，
  而不是让保存失败后再猜原因。

### 兼容性

- 契约 1.7.0 → 1.7.1 是修订号：`SecretView` 字段集合不变（本轮否掉了一个新加进去的 `keyFingerprint`——
  每次加密取独立 salt，它标识的是一个密文块而不是一把主密钥，跨行不可比，答不出任何问题却是一份白给的
  离线试猜靶子；该列留在库里，`decrypt()` 用它区分 `keyMismatch` 与 `corrupt`）。
- 三份契约文件（S1 `docs/` 与两个 `contract/`）现在由 S1 的一条测试要求等于进程当前生成的文档，
  重新导出与随附的 codegen 不再靠人记得。

### 门禁

- `npm run verify` 全绿；axe 审计覆盖含 `/getting-started` 的 10 个屏。

## 1.2.0 — 引导页一步一屏，凭证在界面里录（契约 1.7.0，依据 UI-ADM-002、UI-SET-001…006、S5IF-SEC01…03、S5IF-GOV07）

### 新增

- **`CredentialField.tsx`**（`features/secrets/`）：凡是有 secretRef 的地方，旁边多一个「录入新凭证」。
  面板里是 `type="password"` + `autoComplete="new-password"` 的一次性输入，Basic 认证把用户名与口令
  组成 `user:password` 一个值（S4 的鉴权要的就是这个形状），提交 `PUT /secrets/{name}` 后把响应给的
  `db://名字` 回填进引用字段。**值不写任何浏览器存储、不进 URL、请求返回后（成功或失败都）立刻从组件
  状态清掉**；旁边的下拉列已录入的引用（数据来自 `GET /secrets`，那里只有名字与时间）。
  可用性只来自 `allowed-actions` 的 `secret.manage`；主密钥缺失时服务端回 503，界面说的是
  「这套部署没配 `FACTORY_CREDENTIAL_MASTER_KEY`，请改用 `env://`」，而不是「保存失败」。
- **厂商目录进了提供方表单**：`GET /governance/provider-catalogue` 的条目做成下拉起点，选中即带出
  接口地址、线协议与常用模型名；地址与模型名**仍可自由输入**（私有网关、微调、改名的部署是常态）。
  目录只列 OpenAI 兼容厂商 —— 这一版 S3 只有一个协议实现，列 Anthropic/Gemini 就是给一个存进去必失败的地址。
- **`/getting-started` 改一步一屏**（新 `SetupSteps.tsx`）：顶部步骤条（当前一步 `aria-current="step"`），
  下面一次只展开当前这一步，需要登记的就在这一屏填完，填完自动走下一步。纯函数分支表 `setupSteps`
  原样保留在 `SetupView.tsx`，总览页继续用同一张表 —— 两处不可能各说一套。
- **`FormShell.tsx`**：同一份表单既能在管理页当模态、也能在引导页内嵌（`embedded`），不为引导另写一套。
  环境表、提供方表、模型表分别从 `EnvironmentsView.tsx` / `GovernanceView.tsx` 抽成可复用件，
  引导页渲染的就是管理页那一份。
- 读模型与失效：`qk.secrets`、`qk.providerCatalogue`、`useSaveSecret/useDeleteSecret/useSecrets`；
  提供方写入现在也会失效 `qk.setup`（此前漏了，引导页第一屏会滞后一步）。

### 变更

- **`adapter` 不再是可输入的选项**，显示为只读常量 `openai_compat` 并写明「本版本只实现这一种协议」——
  S3 的 `provider_for()` 根本不看这个字段，让用户填它是给一个填了没用的输入框。
- `setup.secretsOnlyBody`「值始终留在保险库里」改成实话：说清两种真能用的后端（`db://` 加密库、
  `env://` 环境变量），`vault://`/`secret://`/`kms://` 是保留语法、没有后端，下拉与提示里都不再出现。
  同一轮把另外两句一起改了（是**看着线上截图**找出来的，不是读代码找出来的）：
  `governance.keyRefHint`「密钥的值不会离开服务器的环境」与 `environments.referenceRequired`
  「必须以 vault://… 开头」——前者在界面录入之后已经不成立，后者把三个没有后端的前缀列为合法答案。
- 引导页第 1 屏（`子系统接入`）**不给表单**：那是 S2/S3/S4 真身与替身的选择，属于部署配置，
  浏览器里没有任何控件能改变它；给一个能填的东西就是骗人。模型那一步同时给「登记提供方」与「登记模型」
  两个入口，因为模型必须挂在已存在的提供方之下。两个入口的可用性都走 `AllowedActionButton`。
- 步骤标题不再在 locale 里写死序号（模板自己加 `N · `），中文环境里曾出现过「1. 1 · xxx」的双编号。

### 兼容性

- 契约 1.7.0 只增不改：`/secrets` 三条 + `provider-catalogue` + `/setup.credentials`。既有字段零删改。
- 凭证值的去向与 S1 一致：不进幂等回放表（`/secrets` 在排除名单里）、不进 422 响应体、不进审计摘要。
  前端也不把它写进 `sessionStorage` 的幂等 intent —— 过去的 intent 会带一份请求体摘要，
  对凭证字段来说那是一把留在浏览器里的可离线破解指纹，`useSaveSecret` 已改为只按路由与名字 mint 键。

### 门禁

- `npm run verify` 全绿：270 项单测（含 `credentialField.test.tsx` 6 项、引导一步一屏 11 项）、
  typecheck、eslint、production build。MSW 替身加了 `/secrets`（只存名字，绝不回显值）与目录端点。
- Playwright（e2e + a11y）：本轮引导页 DOM 换成了步骤条 + 内嵌表单，用例需随后重跑确认。
  跑法照根目录 `user-guide.md` §11 的坑：先 `ss -ltnp | grep -E '4173|8787'` 确认没有上一轮遗留的
  preview/mock 进程，跑完用生产构建把 `dist/` 放回去。

## 1.1.0 — 装机有了这条路本身：从配置到第一次提交（契约 1.6.0，依据 UI-SET-001…005、UI-PRJ-002、UI-ADM-001/003）

### 新增

- **`/getting-started`「开始使用」**（`features/setup/SetupView.tsx` + 纯函数 `setupSteps`）。
  按 `子系统接入 → 环境 → 模型 → 首个项目 → 阶段/审批/提交` 呈现一条就绪清单，每一项给三件事：
  S1 实际报告的事实、不做完会怎样、一个直达对应界面的动作。事实只来自 `GET /api/v1/setup`
  （S5IF-SET01）与 `allowed-actions`；分支表脱离 DOM 单测。
- **`GET /setup` 的读模型**（`api/queries.ts:setupOptions`、`api/queryKeys.ts:qk.setup`）。
  S1 未授权的小节回 `null`，界面写「你无权查看」，不写 0 —— 0 会被读成「什么都没登记」。
- **总览页顶部的未完成提示**（`SetupSummary.tsx`）：与清单同一张分支表，两处不可能各说一套。
- **模型登记与启停界面**（`POST /governance/models`、`PATCH /governance/models/{ref}`）。
  此前 `governance.model.register` 在 `allowed-actions` 里一直有答案，只有界面没有；
  表单不提供服务地址与密钥引用 —— 那两样 S1 从提供方那一行带出来。
- 空状态：环境表与治理表在无数据时给出说明与跳转（原来是一张看起来像坏了的空表）。
- 环境登记/编辑成功后显示「先做一次连接测试再跑校验阶段」（这句话在 locale 里躺了几轮，从未被渲染）。

### 变更

- **新建项目处处由 `project.create` 门控**（总览页头、向导末页）。`POST /projects` 的守卫用的就是这个
  名字，S1 1.6.0 起才在平台 `allowed-actions` 里回答它；界面在此之前只能"发一次看看会不会 403"。
- **向导环境一步不再是死路**：校验失败会渲染出原因，零环境时给出「先去登记环境」的入口；
  原先 `form.trigger(['targets'])` 失败但错误无处显示，"下一步"点了没反应。
- 向导补上 `translationLanguages` 的输入（UI-PRJ-002 列了这一项，此前只存在于 schema 与请求体里）；
  「至少一个非生产环境」的校验改成真校验（原先 `:42` 只检查 `targets.length >= 1`，与服务端 `CP-PRJ-002` 不一致）。
- 概览页不再把未运行的阶段说成「尚无 Agent 结果」，阶段清单每行给一个「打开阶段」的入口（UI-PRJ-003）。
- 侧栏补上选中态：`.stage-item[aria-current='page']` 是样式表的选择器，而 `aria-current` 原本被放在
  内层 `<span>` 上，等于没有选中态。
- 硬编码文案进 locale：`Loading…`、语言切换的 `aria-label`。
- 清单条目的标题不再自带序号：`<ol>` 已经编了号，标题里的「1 ·」让每一行读成「1. 1 · 子系统接入」。
  序号从两种资源的五个标题里去掉，编号交给列表本身。
- **中文浏览器的第一屏真的是中文**（`i18n/index.ts`，UI-APP-002）。原先把语言检测交给
  `i18next-browser-languagedetector`，而它需要 `nonExplicitSupportedLngs: true` 才认 `zh`、`en-US` 这类
  标签 —— 这个开关会让精确标签的**查找链塌成 `["en"]`**：`i18n.language` 是 `zh-CN`，`<html lang>` 与
  `Accept-Language` 也都是 `zh-CN`，可每一句文案都从英文资源取。线上量到的是登录页整屏英文。
  现在只有一处规则：`stores/language.ts` 先读 `damf:language`、再把浏览器标签归一（`zh*` → `zh-CN`，
  其余 → `en`），i18next 的 `lng` 直接用它，检测器与那个开关一起删掉（依赖也移除了）。
  `i18n/boot.test.ts` 四种入口各测一条：`zh-CN`、裸 `zh`、`en-US`、以及"存过的选择压过浏览器"。
- **`X-Operator` 超出 Latin-1 时先百分号编码再发**（`api/client.ts:operatorHeader`，CP-API-002）。
  Fetch 标准限制 header 值为 ISO-8859-1，一个中文操作人名会让浏览器在 `Headers.set` 就抛 `TypeError`，
  于是**每一次写操作**都表现为「无法连接服务器，请检查网络」。姓名本来就装得下的走原样，一字不变；
  解码在 S1 门口做（`rest/middleware.py:decode_operator`），审计落的是 `张三` 而不是转义串。

### 兼容性

- 对 1.5.0 的 S1：`GET /setup` 404 → 清单显示"读不到配置状态"并可重试，其余界面不受影响；
  `project.create` 不在 `allowed-actions` 里 → 新建项目按钮按不可用处理（UI-BR-01 的"缺失即不可用"）。

### 门禁

`features/setup/setup.test.tsx`（分支表 + 页面）、`features/projects/wizard.test.tsx`（零环境断点、
非生产规则、设计基准唯一、翻译语言进请求体）、`features/governance/governance.test.tsx`（模型登记与启停
的动词/路径/请求体）、`features/environments/environments.test.tsx`（空状态与登记后的提示）。
`i18n/keysUsed.test.ts` 保证新增文案在中英两边都存在。

## 1.0.1 — 首轮：M1–M4 界面与两端一致性（契约 1.4.0 → 1.5.0）

见 `../S5M/docs/CHANGELOG.md` 同期条目与 `../user-guide.md` §6/§8。
