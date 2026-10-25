# CHANGELOG — S5M 移动优先控制台

版本号即 `package.json` 的 `version`；界面消费的契约版本另注在每条里（与 S1 `docs/CHANGELOG.md` 同一约定）。
破坏性变更（改变既有界面承诺的语义）提升主版本。

## 未发布 —— 阶段问题改成整页问卷

`package.json` 仍为 **1.14.1**，本段只记录代码；界面消费的契约已到 **1.19.0（147 条路径）**。

### 新增

- **整页问卷**（SRS UM-Q-002～004）。`/questions/:id` 以当前问题的阶段为范围，取
  `GET /workflows/{workflowId}/questions` 中同一 `stageId` 且 `status=OPEN` 的全部问题，按 `HIGH` 优先、
  同级按文本排序，同屏逐题作答；页头显示「已答 n/m 题」。题型由 `QuestionView.answerMode` 决定：
  `SINGLE` 渲染单选、`MULTIPLE` 渲染复选；建议项按 `recommended` 标「推荐」，`changesUid` 与
  `regenerates` 各给提示；「其他」展开自由文本（单选时只发 `customText`，多选时可与所选建议一起发）。
- **一次提交，服务端 resume 一次**（S1 CP-Q-005）。底部只有一个「提交全部回答并重新生成」，请求体是
  `POST /questions/answers` 的 `AnswerQuestionsBody.answers[]`（每题 `questionId` + `suggestionIds[]` +
  可选 `customText`，空答不发送）。响应 `QuestionnaireAnswerView.questions` 非空就是下一轮，页面以服务端返回的列表原地替换并滚到顶；为空则提示「已全部回答」并回阶段/待办。**不再逐题调用回答接口**，也没有
  5 秒延迟提交与撤销条；未点提交就离开页面不发任何回答。
- **阻断校验与计数**：任一 `blocking` 题没有有效答案（单选未选、多选为空、选「其他」但文本为空）时
  提交按钮按住；页头与底部各提示「还有 n 个阻断问题未回答」，每题下给「这是阻断问题，必须作答」。
  按钮能否使用仍先问 `allowed-actions` 的 `question.answer`。

### 变更

- **契约同步到 1.19.0 / 147 条路径**（`contract/openapi.json` 与 `src/api/generated`），
  `src/test/contractVersion.test.ts` 同时钉住版本与路径数。新增的问卷类型：`AnswerQuestionsBody`、
  `QuestionnaireAnswerItem`、`QuestionnaireAnswerView`、`QuestionnaireAnswerResult`；`QuestionView` 新增
  `answerMode`，`SuggestionView` 的 `recommended` / `changesUid` / `regenerates` 进入界面。
- **i18n**：问卷相关键（`questions.*`）包括 `questionnaireTitle`、`questionnaireHint`、`answeredCount`、`singleHint`、
  `multipleHint`、`recommended`、`other`、`otherLabel`、`otherPlaceholder`、`missingBlocking`、
  `missingBlockingHint`、`submitAll`、`submitAllHint`、`nextRound`、`backToStage`、`changesUid`、
  `regenerates`、`blockingTag`，中英成对。
- **已知未跟上**：`scripts/mock-s1.mjs` 尚未实现 `POST /questions/answers`，`tests/e2e/clickBudget.spec.ts`
  的「回答一个问题」仍按旧的 `.qn__sugg` + 5 秒延迟提交编写；本轮只改文档，未动替身与测试。

## 1.14.1 —— 需求条目保存后列表真的跟着变；替身开始照 S1 的规则拒绝

### 修复

- **保存完条目，页面上看不到自己刚写的那条**。`qk.blueprintEntries()` 无参数形态把 `sections` 拼成了
  一个**空字符串元素**，于是写操作要失效的那把键是 5 段、页面实际用的键也是 5 段但末段不同——TanStack v5
  的 `invalidateQueries` 没有通配符，只拿"更短的过滤键"去前缀匹配更长的查询键（`partialMatchKey`），
  这条失效因此一条都不命中，界面停在写之前的那次读取上。现在无 `sections` 时就是 4 段前缀，并把这条
  不变式写进 `src/api/keys.test.ts`。真机验收时它是第一个被看到的症状。
- **`test` 哨兵去掉**（承 1.14.0 的需求条目页）。它想防的是"读后写之间别人加了条目"，做法是断言
  `/requirements/items/{n-1}/businessKey` 等于排序后的最后一条——但 `entries` 是按 businessKey 排序返回的
  （`readmodel.py`），数组第 n-1 位是谁界面无从知道。真实草稿上验：一次正常的删除被挡成
  422 `SCHEMA_ERROR`（`BP_PATCH_MALFORMED`）。新增改为 `add /requirements/items/-` 追加；改与删仍在提交前
  重读一次再整段替换，窗口只剩"重读到落库"这一趟往返，S2 那边没有可用的乐观并发前置条件（要读得到数组
  位置才谈得上，那是契约变更）。

### 变更

- **替身开始按 S1 的规则说话**（`scripts/mock-s1.mjs`）。以前它把所有没匹配上的 POST 一律答
  `{accepted:true}`——上传缺 CSRF 头也能"成功"，这正是 1.14.0 那个缺陷藏了这么久的原因。现在
  `authOn` 杆杆下缺 `X-CSRF-Token` 的写请求回 403 CSRF_FAILED，登录下发可读的 `factory_csrf` cookie；
  并且补了 `blueprint/entries`（含 `_section`/`_path`）、`POST /stages/{id}/patches`（缺 operations → 422、
  重复 businessKey → 200 `REJECTED` + `issues[]`）、`section-schemas/requirements`，上传的行真的进入材料列表。
- **`clickBudget` 的上传用例不再兜底**：去掉 `waitForEvent('filechooser').catch(() => null)` 与随后的
  `input[type=file]` 直接塞文件——主按钮打不开系统选择器不是"流程长了"，是坏了；现在它必须打开，并且
  断言进度条消失后屏幕上留下「已上传：…」、材料列表里出现该文件。

### 契约

不变（仍是 1.16.0 / 144 条路径）。

## 1.14.0 —— 需求上传真的能落库，需求条目可以在手机上逐条写

### 修复

- **「需求上传」进度条走完就消失、列表里什么都没有**。真相是上传从来没成功过，而界面把失败藏了
  起来，四处独立缺陷叠在一起：
  1. `api/upload.ts` 的裸 XHR 没带 `X-CSRF-Token`——本机部署是 cookie 会话，S1 对每一个不带该头的
     写请求回答 403 CSRF_FAILED（`rest/middleware.py`）。同一份 multipart，加上头就是 201。
  2. 同一个文件把 `X-Operator` 原样发出，中文操作人名让 `setRequestHeader` 直接抛异常，请求根本没发出；
     现在走 `operatorHeader()`（CP-API-002）。
  3. `InputsView.vue` 的 `catch {}` 是空的，注释写着"toast 由 upload rejection 负责"，但
     `uploadInput` 不是 TanStack mutation，全局 onError 永远不会触发。现在 `pushError` + 行内原因。
  4. `FuiUpload.vue` 的 `defineExpose(...)` 写在 `</script>` 之后——SFC 会整段忽略，组件什么都没暴露，
     主按钮 `uploader?.pickFile()` 抛 TypeError，只有下面三个磁贴能用。
  进度条报告的是**请求体发送**进度，403 也会走到 100%，而它是屏幕上唯一的反馈元素；现在
  「正在上传 / 已上传：文件名 / 上传失败：原因」三态留在页面上，空列表按钮也接上了同一个动作。
- **列表刷新不再挡在解析前面**：改成失效 `qk.inputs`，不再 `await refetch()`（那次读取一抛错，
  后面的解析请求就根本没发出）。

### 新增

- **需求条目页（`/projects/:id/requirements`）**。上传+解析只是需求的一种来路；这里让操作员直接
  写：新增一条、改一条、删一条，写进蓝图草稿的 `requirements` 段，和解析产出的条目是同一份数据。
  写入走 `POST /stages/{stageId}/patches` 的 RFC 6902 操作。为什么是整段数组替换而不是按下标改：
  S2 的读模型按 businessKey 排序、`_path` 是逻辑键地址，而补丁引擎只认数组下标或 `-`
  （`patch/rfc6902.py`），界面上没有任何途径知道条目在数组里的位置——按下标写会改错条目。
  新增只发一个 `add /requirements/items/-`（追加，绝不会覆盖别人这期间加的条目），改与删才在重读之后
  整段替换。（原先还带一个 `test` 哨兵做并发保护，1.14.1 去掉了：它断言的是"数组第 n-1 个是谁"，
  而这个位置信息界面根本读不到，实测反倒把一次正常编辑挡成 422。）
  表单的取值上限读服务端发的分节 Schema（`blueprint/section-schemas/requirements`），本机的常量
  只是它没到时的兜底。阶段关闭后 S1 不再发 `patch.submit`，页面据此收起全部写入口并请操作员走变更请求。
- **提供方「测试连接」**（S5IF-GOV20）。治理页每个提供方一行加一个按钮，问一次 `GET /models`：
  地址写错、密钥被拒、模型比请求超时慢，这三种以前在界面上都是同一句话。
- `lib/media.ts`：判断一份产物是不是能在手机上按文本渲染；`.docx` / `.pdf` 不再被读成乱码，
  而是明确说明并用 S1 的 `?download=true` 取原件。`api/queries.ts` 的 `useArtifactContent` 因此
  多了 `enabled`——不该渲染的文件不下载。

### 契约

`contract/openapi.json` 与生成的类型同步到 **1.16.0**（144 条路径）。

## 1.13.1 — 登录不再弹"权限不足"：壳层的搜索上下文不再无条件追问幽灵项目

### 修复

- **`AppShell` 用 localStorage 里的 `lastProjectId` 无条件发起 `workflow` + `registry` 两个读**。
  操作员上次开着的项目被删（或对自己不再可见）之后，这两个请求在每个屏幕、每次 refetch 都拿那个
  id 去问 S1——S1 对非成员隐藏存在性，403 是它正确的回答（不是权限模型坏了，平台管理员照常看得到
  全部在册项目）；坏的是客户端：全局读错误监听把这条每一次都播成一条"当前操作人无权执行此操作"
  的 toast，症状就是**登录即弹权限不足**。这个读本来只为顶部搜索面板存在，代码注释也写着
  "asked only while the panel is open"——实现却没过这道门。
- 现在两重门禁：搜索面板打开才问；而且 `lastProjectId` 必须在**当前这个人**的项目列表里，
  不在就当作没有上下文（查询根本不发）。搜索提示语与 `searchAll` 的项目参数同步改用这个校验后的 id。
- **阶段页的两条弹窗**（S1 侧另记）。其一是真故障：`GET /stages/{id}/chat` 在真实提供方链路上必然 500。
  其二是把合法答案当故障：项目还没跑过首个设计阶段就没有草稿，S1 以前回裸 404，全局错误监听把它播成
  一条"请求的对象不存在"。S1 现在按 CP-UI-022 回 `reason=NOT_GENERATED` + `requiredStage`，
  `useBlueprintDraft` 据此解析为 `null`（`ManualAuthoring`/`InputsView` 本来就按无草稿渲染），
  没给 `reason` 的 NOT_FOUND 照旧上报——`notGenerated.ts` 里那条等 S1 补 `details.reason` 的兜底注释
  从此只对真正没带 reason 的资源成立。
- **聊天面板不再挂在没有对话的阶段上**：`StageView.vue` 以前对所有阶段无条件挂 `StageChat`，而
  AI-CHAT-001 把对话限定在四个设计阶段，停在 DISCOVERY / 编译阶段时那条读取只会再弹一次"对象不存在"。

## 1.13.0 — 取消单次 Agent 运行有了入口；e2e 产物不再覆盖生产包

### 新增

- **取消 Agent 运行**。端点一直都在（`POST /workflows/{wf}/agent-runs/{run}/cancel`），缺的是寻址：
  SSE 进度帧里的 `agentRunId` 以前直接丢掉，`actions.ts` 的 `agent.cancel` 是个从没被引用过的死键。
  现在进度帧把 run id 存进 progress store（后到的帧不带 id 也不抹掉先到的），运行中的阶段卡片在
  S1 说可以取消时出现红色「取消 Agent 运行」，按下先弹确认——文案与桌面端同一句。
- `progress.test.ts` 三条：帧带 id 才存得下、缺 id 的帧不抹掉它、乱序帧不回滚；
  `mutations.test.ts` 把取消请求钉在 `/workflows/{wf}/agent-runs/{run}/cancel` 这条路径上。

### 修复

- **提交后横幅不消失**：网络失败置起 `commitPending` 之后，没有任何代码在重试成功（或任何一次被答复）
  之后把它放下——"结果待确认"会挂到刷新为止。settled 即清除。
- **审批页两处裸枚举**：Gate 状态徽章直接念 `WAITING_APPROVAL`，审批时间线直接念 `APPROVED/REJECTED`。
  走 label map 出中文（`lib/labels.ts` 新增 `gateStatusLabel`/`decisionLabel`）。
- **`?approve=1` 会被重放**：待办卡片带参数进审批页、watch 打开批准面板，但参数留在 URL 里——
  SSE 触发重取时 watch 再跑一遍，把已经关掉的面板弹回来。现在参数在打开那一刻消费掉（`router.replace`）。
- **「导入报告」死按钮**：`reportArtifactId` 缺失时点击毫无反应。现在按钮 disabled。
- **`build:harness` 不再覆写生产 `dist/`**。它以前直接写 `dist/`，`test:e2e` 跑完必须记得
  `npm run build` 换回来，忘了就把 harness 版（含 `/dev/json-editor` 路由）拷上线。现在它输出
  `dist-e2e/`，playwright 的 preview 也读 `dist-e2e/`——与桌面端 `build:e2e` 同一套隔离，
  `dist/` 从此只有生产构建写它。

## 1.12.0 — 中文界面按 §2 术语表统一（承 1.11.0，同一裁决的第二遍）

### 变更

- **9 条中文文案**：`Blueprint 版本`→**蓝图版本**、`Prompt`→**提示词**、`Secret 引用`→**引用 / 凭证引用 /
  密钥引用**（字段标签写全名，正文里用「引用（env://… 或 db://…）」把要填的形式直接给出来）。本控制台的
  `Gate`/`Registry`/`Patch` 早已是「审批节点 / 登记表 / 修改提案」，这次是桌面端对齐过来，不是这里改名。
- `errors.SECRET_UNAVAILABLE` 从「Secret 引用无法解析」改成**「这条引用解析不出来」**——原句是把错误码的
  英文名词直译了一遍，操作员读到的是"Secret"这个他没学过的词，而不是"你填的那条引用打不开"这个事实。

### 新增

- `parity.test.ts` 一条守卫：**`zh-CN` 的值里出现 `Blueprint|Registry|Gate|Prompt|Patch|Secret`（含复数）即红**，
  `en` 不受约束（英文界面用这些词是对的）。同文件里 `leafValues` 从两个测试内部提到模块级，免得第三份
  遍历器各写一遍。
- SRS 侧同步：`requirements/S5M_移动优先前端_SRS.md` §2 升为 **V1.4.2**，那张术语表明确写成"对 S5 与 S5-M
  同时生效，左列是契约名、右列才是屏幕上的字"，并补 `Prompt→提示词`、`Secret→凭证引用／密钥引用` 两行。
  桌面 `user-guide.md` §6.1 加了一段用词说明，并把引用了旧界面标签的三行（「待审批 Gate」「按 Registry」）改对。

## 1.11.0 — 界面不再念内部编号（与 S5 1.10.0 同一裁决）

### 变更

- **34 条用户可见文案重写**（`src/i18n/locales/en.ts` 与 `zh-CN.ts` 各 17）。子系统编号（`S1`…`S5-M`）与
  需求编号（`FR-SEC-003/004`、`CP-WF-020`）从屏幕上退场，编号本身退回同一个键上方的注释。必须点名服务端时
  用「服务端 / the server」——这不是新词，`start.problemsTitle` 早就是「服务端发现的配置问题」，这一轮把
  桌面侧拉齐到同一句话。
- **「我的 → 关于」不再让 App 自报家门为「S5-M 移动优先前端」**（`me.aboutDesc`）：改成「元数据工厂的移动版
  控制台，页面数据全部来自服务端」。这是全仓唯一一处把 S5-M 自己的编号印给用户看的地方。
- **`/start` 的 `{which}` 不再插原值**（`src/features/start/startSteps.ts:51,52`）。后端 `/setup` 的
  `providers` 键是 `S2`/`S3`/`S4`、值是 `real`/`fake`，此前替身模式那行会读成「S3 正在跑替身」。现在过
  `capabilityLabel` / `modeLabel` / `capabilityEntry`，未知标签回落原 token。
- 步骤名「子系统接入」→「能力接入」；`notImplemented.body` 去掉"里程碑"这种交付排期的说法，改成「这个能力
  在当前版本里还没有开放——服务端返回的是「未实现」」。
- 产物列表卡与详情页图节点的 `creatorSubsystem`（`ArtifactsView.vue:36`、`ArtifactDetailView.vue:73`）过映射。
  S5M 原本不显示 `sourceSubsystem`，审计页无需改。
- 契约里 `providers` 是 `{[key: string]: string}`（`schema.d.ts:6991`），无界；回落原 token 是**必须**的，
  空串会读成"没有这项能力"。`labels.ts` 里 `providerClassLabel` 的那段历史（把 i18next 的 `defaultValue`
  传给 vue-i18n，未知类别直接空白）就是这一条的来源。

### 新增

- `src/lib/labels.ts` 追加 `capabilityLabel` / `modeLabel` / `capabilityEntry`，与既有 `envClassLabel`、
  `providerClassLabel` 同一个形状（映射表 + 原值回落），**函数里不写中文字面量**——`i18nLiterals.test.ts`
  扫 `src/lib`，硬编码中文在编译期就该红。两个 locale 文件同时加 `capabilities` 一块（本仓没有 S5 那种
  编译期形状平价，只有运行时 `parity.test.ts`）。
- `src/i18n/parity.test.ts` 两条守卫：① 遍历两个语言的全部叶子值，命中子系统编号、需求编号、`M1…M5`、
  「子系统」/subsystem 与那批句子内部词即失败；② 每个键的 `{占位符}` 多重集在两种语言里必须一致（vue-i18n
  是单花括号，**不能照抄桌面的 `{{x}}`**，抄了这条守卫就成摆设）。正则带边界，否则 `DHIS2` 里的 `S2` 会满屏误报。
- `startView.test.ts` 一条**语言切换后重算**的断言：步骤表是 computed，标签若在模块加载时求一次，
  中文操作者会在中文句子里读到 `Requirement parsing`。
- `startSteps.test.ts` 改钉 `i18n.global.locale`（本仓既有约定）并按英文名断言，同时把 S1 的 `S3` 与
  `openai-compatible` 两类标签各自断一次——原值回落这条分支此前没人覆盖。
- `tests/e2e/startGuide.spec.ts` 的步骤条文案跟着改名；`不可交付` 那半句**原样保留**（UM-SET-002 要的就是
  这四个字，替身模式绝不能被读成"已就绪"）。

### 不在这一条里

`node.type`（`BUSINESS_INPUT` 之类）、Gate 类型、阶段状态徽章仍是后端原值；中文值里未翻译的
`Blueprint`/`Registry`/`Gate`/`Prompt`/`Secret` 照旧。SRS §2 的"浅色小字显示系统代码"已按本裁决改为
"业务枚举取值"（V1.4.1），这些枚举仍在屏上显示，内部编号不再显示。

## 1.10.1 — 消费的契约版本从此有地方住了（S5IF-G11）

### 新增

- **`src/test/contractVersion.test.ts`**：断言 `contract/openapi.json` 声明的版本与路径数就是签入的
  `src/api/generated` 所依据的那一版（现在：**1.14.0 / 143 条路径**）。本机是这条断言存在理由的活案例：
  契约 1.10.0 给 `POST /projects` 加了 `codePrefix`，本控制台的 vendored 副本没跟上，`vue-tsc` 却全绿 ——
  直到按新契约重新生成，`NewProjectView.vue` 里那句 `codePrefix: ''` 反而当场编译不过。
  `install.sh` 已经会在构建前比对生成文件并中止；这里钉的是另一半：**版本号**本身。
  S1 侧对同一个号有 `tests/api/test_openapi_contract.py::test_document_version_and_servers`，S5 侧有同名测试。
- 纯测试改动，不进 bundle（`vue-tsc --noEmit` 只做类型检查，构建里也没有注入版本号的地方），
  所以**不要求重新部署**。

## 1.10.0 — 变更申请可以自己声明要作废哪些对象（契约 1.14.0，依据 CP-CR-005、总体 SRS 15.6）

### 新增

- **新建变更申请的面板增加"要作废的对象"**。定向删除的授权只能来自请求本身：S2 的影响集没有删除字段，
  S1 的 `analyze` 过去还把行上的候选覆写成那份永远为空的分析结果。留空即不做任何定向删除；
  填了不是 DEPRECATED 的键名会被 S1 当场 422 并列出对不上的名字，而不是等编译期静默剔除——
  那样操作者会以为对象已经退役，而它还在实例上。
- `src/api/mutations.test.ts` 增加一条：创建请求的 body 带上 `deletionCandidates`。

## 1.9.0 — 一次被答复的拒绝不再把这个动作永久钉死（契约 1.13.0，依据 CP-API-003、UM-NET-001、UM-BR-04）

### 修复

- **`write()` 现在在服务器答回一个拒绝后释放幂等键**。S1 对已完成的键**逐字节重放首次响应**，同键换一个
  改过的请求体则是 422 `IDEMPOTENCY_CONFLICT`（CP-API-003）——所以"被答复的拒绝"已经把这把键用掉了。
  攥着它，同一个控件此后每次点击都回到那条旧结论，而键存在 localStorage 的意图签名上，刷新也解不开。
  UM-NET-001 说的"同一动作的重试复用该键"讲的是**没人给出答复**的那次尝试，不是被明确拒绝的这一次。
- 与 S5 同一判据 `keepsIdempotencyKey`：网络失败保留键（UI 侧仍是"结果待确认"，不自动重提），
  `REQUEST_IN_PROGRESS` 也保留——服务器还在处理这一条本身，换新键就是第二次执行同一个动作。
- `src/api/mutations.test.ts` 新增四条：422 与 409 之后键必须换，`fetch` 直接失败与
  `REQUEST_IN_PROGRESS` 之后键必须不变。

## 1.8.0 — 搜索能跳到的四样东西、下载与清除各是各的、导入不再只有桌面有（契约 1.8.0，依据 UM-NAV-004、UM-REV-001、UM-COMP-003、UM-P-04、UI-PRJ-004、FR-DATA-008、CP-UI-016）

### 新增

- **顶部搜索的四类命中与最近访问**（UM-NAV-004）。这条以前只做了一半：搜索框只过滤项目名，
  「最近访问」写进 localStorage 却没有任何组件读它。现在一个词会给出**项目、阶段、环境、对象**四组结果，
  每组跳到自己的地址（`/projects/{id}`、`/stages/{id}`、`environments/{key}`、
  `/projects/{id}/graph?businessKey=…`），空输入时列的是真正记下来的最近 10 个对象（项目行把 id 还原成名字）。
  阶段与对象在 S1 里没有跨项目的列表端点，所以这两组搜的是"你最近打开的那个项目"，面板上把这句话写出来——
  一次搜不到不能读成"哪儿都没有"。依赖图那一屏现在会读 `?businessKey=` 并选中那个节点，
  否则"跳转"只跳了一半。
- **产物详情的「下载」是一个真地址**（UM-REV-001、CP-UI-016）：`GET /artifacts/{id}/content?download=true`，
  浏览器按 S1 给的 `Content-Disposition` 存文件；「复制链接」是另一件事（把可引用的页面地址抄走）。
  能不能下载仍然只问 `allowed-actions` 的 `artifact.download`（UM-BR-01）：被拒时出现的是 S1 的原因，
  而不是一个点了会拿到 410 的链接。内容已清除时这条链接干脆不出现。
- **清除内容**（FR-DATA-008 + UM-P-04 与第 6.2 节的确认矩阵）：不可逆且删数据的动作要**输入产物 ID**才按得下去，
  gate 在 `artifact.purge`；清除后记录与哈希还在，字节取不回来，页面按这个事实显示。
- **项目导入**（UI-PRJ-004 的成对另一半）：项目设置页以前只有导出——手机上导出去的东西，只能回到电脑前恢复。
  现在填一个导出产物 ID 就发 `POST /projects/import`，可用性来自平台作用域的 `project.import`
  （导入新建的是项目本身，S1 也把这个名字放在平台清单里），受理后项目列表里真的出现新项目。

### 修复

- **会滚动的区域走不到**（WCAG 2.1.1）。产物详情页这轮第一次进逐屏 axe 扫描，立刻报
  `scrollable-region-focusable`：原始内容块限高 60vh 又 `overflow:auto`，却不可聚焦——键盘用户拿到一段
  看不完的字节。按同一问法清了一遍，移动端共 8 处渲染 JSON 的 `<pre>` 都有这个问题（内容、来源追溯、
  能力档案、用量、Gate 的"为什么"、提交回执、开发包文件、影响报告），全部改为 `role="region" tabindex="0"`
  并各带 `aria-label`。之前 17 屏没报，是因为替身给那些区域的内容都不足一屏。
- **「下载 / 复制链接」这个按钮只复制链接**。UM-REV-001 要的是两件事，页面上是一个写着两件事的按钮：
  操作员为拿字节按下它，得到的是剪贴板。现在拆成两个各说各的控件。
- **同一屏上两个「复制链接」**。产物页新加的复制链接与内容块自带的复制按钮撞了名，而后者复制的是屏幕上的
  文字不是地址。内容块改口「复制内容」（`common.copyContent`）：可访问性树里两个同名不同事的控件，
  屏幕阅读器读不出来，strict-mode 断言也会因"命中两个"直接失败。
- **替身补上三条契约里有的读写出路**：`GET /artifacts/{id}`（以前落到通用空信封，于是产物详情页在
  harness 里从来没被真正渲染过一次）、`POST /artifacts/{id}/purge`（要 X-Operator，无请求体无幂等键，
  与 S1 一致），`POST /projects/import`（202 + 新项目 id，且新项目真的进列表）。`_control` 增加
  `artifact-purged` / `artifact-intact` / `imports-reset` 三个杠杆——purge 是单向事实，
  `reuseExistingServer` 会把上一轮的结果漏给下一轮。

## 1.7.0 — 选择组收编成一个组件，44 × 44 第一次有人量（契约 1.8.0，依据 UM-BR-01、UM-UX-001、UM-TECH-001、CP-NFR-09、CP-UI-021/022）

### 新增

- **`FuiSegmented` 与 `FuiCheckChips`**（UM-TECH-001）。单选/多选两组按钮以前散在 5 个页面里，各自一份
  `__segs/__seg` 样式：尺寸不一（治理段曾是 34px、闸门段是 40px），选中态也不一致。现在只有一个外观文件
  `src/fui/chip.css`，两个组件按 `<style scoped src>` 共用它；`role="group"` + 每块 `aria-pressed` 是
  这组控件**实际**的行为（每块都能 Tab、点一下就选中），没有借用它们并不实现的 radio 语义（方向键、
  焦点漫游）。
- **`tests/e2e/tapTarget.spec.ts` + 独立的 `tap-target` 工程**（UM-UX-001）。这一条以前靠人眼：本轮把六件
  低于 44px 的控件提上去之后，没有任何东西说"下一件不可以"。现在 17 个界面在 360px 视口下逐个量可点件
  的实框，短于 44 就红。三条排除写在文件里：没有框的元素（Vant 把真 `<input>` 藏在标签行后面）、
  被套在更大控件里的图标/开关（WCAG 2.5.8 的等价目标豁免）、句子内的行内链接（一段话不能按 44px 行高排）。
- **开发包页先看 `frontend_package.download`**（UM-BR-01）。"这份包存不存在"是 S1 的答案
  （`domain/actions.py:695`，未生成时带 `PACKAGE_NOT_GENERATED`），以前页面是从 404 的 `details.reason`
  反推的——于是先发一次注定失败的请求，再把失败结果读成指引。门控关着就不发这个请求。
- **运行控制台的「重新检查漂移」改挂门控**（UM-BR-01，§9.3 的遗留项）。它以前是一个裸 `FuiButton`：
  运行已关闭时 S1 会回 `NO_OPEN_RUN`，而界面照按不误。现在它走 `FuiActionButton`，动作名
  `deployment_run.drift_check` 按 run 的 `targetId` 解析，替身也补答这一条。

### 变更

- **10 处选择组换成新组件**（环境表单 4 处 + 提交窗口的星期多选、治理页的放宽级别与数据类别、
  模型/提供方表单、闸门警告处置）。顺带删掉 5 份本地样式。项目列表的「显示已归档」原来是一个开关 +
  旁边一行同样的字：现在是一个两态分段控件，一次点击只有一个名字。
- **实框低于 44 × 44 的六件按 `--fui-tap-min` 补齐**：顶栏两个图标按钮（20×20 → 命中框 44×44，字形大小不
  变）、Vant 输入框（高 24 → 44，整行跟着长）、`FuiButton` mini（宽 30 → 44）、开发包下载链接（宽 24 → 44）、
  治理预算作用域按钮（高 40 → 44）。
- **错误码目录补 23 条**（CP-NFR-09）。`DX_*`、`DHIS2_TASK_FAILED/LOST/TLS_INVALID/UNREACHABLE/VERSION_UNSUPPORTED`、
  `AI_PARSER_*`、`SECRET_IN_USE` 等在 S1 能回答、而界面没有对应句子的码，以前会把抛出方写给日志的英文原文
  直接摊在中文界面上。文案取 S1 自己的目录，不另编一套。守护见 `S1/tests/unit/test_console_error_catalogue.py`。

### 修复

- **`.envf__seg.is-on` 引用的 `--fui-primary-soft` 这个令牌并不存在**，回退是 `--fui-surface-2`（浅色主题下与
  未选中底色几乎一致）：环境表单里"哪一项被选中"只剩一条边框颜色可分辨。收编后统一为边框 + 底色 + 字重。
- **闸门处置按钮的 40px**（`.gt__segbtn`）——上一轮把别的六处提到令牌时漏了这一处，几何门禁一跑就抓出来了。
- **`FuiAsync` 的 `#fallback` 槽以前到不了屏幕**。它把页面的内容当**默认槽**传给 `FuiGuidedEmpty`，而那边
  只在 `intent.kind === 'error'` 时渲染默认槽——于是 `notGenerated` / `notExist` 两种引导态下，页面想补的那
  一行（例如"项目已归档，处于只读状态"）根本不会出现。现在两端都用命名槽：`FuiAsync` 往下传 `#fallback`，
  `FuiGuidedEmpty` 声明一个任何引导态都渲染的 `<slot name="fallback" />`，并由 `FuiGuidedEmpty.test.ts` 钉住。

## 1.6.0 — 手机上也能用组织账号登录了，长列表开始说实话（契约 1.8.0，依据 UM-AUTH-001、UM-REV-007、UM-UX-001、UM-NFR-05、S5IF-G04、CP-UI-021/022）

### 新增

- **组织账号登录（SSO）**。桌面端从 M1 就有这一半，移动端一个字都没有：`authMode` 为 `both` 的部署在
  手机上只给一个口令框，于是整个部署围着转的那个账号在离开桌子就没法用。现在 `/login` 在
  `oidc`/`both` 下多一个「使用组织账号登录」，浏览器带着 S1 给的 `client_id / redirect_uri /
  response_type / scope / state / nonce` 离开，回落到新增的 `/auth/callback` 换会话。
  `state` 按 S1 的要求**作为查询参数回传**（它拿这个参数与签发时写下的 HttpOnly cookie 比），
  `code` 只用一次——回调路由被再进一次不会把同一份凭据再交出去一次。
  出去之前把「操作员原本要去哪」存在 sessionStorage，回来接着去；这条路径只认站内路径，
  `//host` 与带协议的绝对地址一律丢弃（一个登录页最容易做成了开放重定向）。
- **`/auth/callback` 是 `bare` 的**：外壳那条「没有会话就去登录页」的观察会在这一次交换完成之前把
  人从页面上拽走，把一次有效登录报成失败。
- **开发包一屏**（`/projects/{id}/handoff`，CP-UI-021），与桌面端同一份事实：S2 自己的清单与 `kind`
  用词，按文件读取，下载是浏览器自己的 `?download=true` 链接；没有开发包时走 UM-REV-007 的引导态。
- **`tests/e2e/a11y.spec.ts` + 独立的 `a11y` 工程**（UM-NFR-05）。桌面端从 M1 就在跑 axe 的
  WCAG 2.1 AA 扫描，移动端一次都没有——而它比桌面端更需要。17 个界面，只把 critical/serious
  当门禁；四个测量工程不重复跑它（那是无障碍问题，不是几何问题）。

### 变更

- **长列表按 S1 的分页读，并把"读了多少"写在屏幕上**（S5IF-G04）。审计走游标（`limit`+`cursor` +
  加载更多），账号列表原来固定 `limit: 100` 且不报告 `total`，通知列表按 offset 累加页面并按 id 去重
  （同一窗口重取不会把行复制一遍），消息角标与「未读: N」都改成 S1 的 `total`。
  `qk.notifications` 现在带筛选条件：一个不含 `unreadOnly` 的缓存键会让角标读到整份列表。
- **触控目标补齐 44 × 44（UM-UX-001）**。`FuiButton` 的 small 档是 36px、`FuiFilterBar` 的 chip 36px、
  结论卡展开 32px、原文视图的复制/展开 28px、Toast 的复制 28px、治理页的 seg 34px——六处都在
  `--fui-tap-min` 之下，而这一条是要求不是建议。

### 修复

- **冷启动深链被丢掉**。外壳在路由解析完成之前就判断"没有会话，去登录页"：此时 `route.fullPath`
  还是 `/`、`route.matched.length === 0`，于是操作员要的那一屏蒸发——最坏的一种是
  `/auth/callback?code=…`，授权码就在 query 里，跳走之后什么也换不到。改法：`onMounted` 先
  `await router.isReady()`，`toSignIn()` 对 `bare` 与未解析位置都直接返回。这条不是 OIDC 专属的：
  任何账号部署下的冷启动深链（含从消息里点进某个 Gate）都受影响，只是新增的回调页把它逼了出来。
- **登录屏把自己的拒绝原因擦掉**。字段 watcher 顺手清了服务端返回的失败状态，而失败处理又会清空口令格
  ——清空本身是一次"编辑"，于是原因刚出现就被自己抹掉。改法：编辑只清本地提示，失败原因留给下一次
  尝试开始时清（`login()` 本来就清）。这条同样是端到端逼出来的，不是读代码读出来的。
- **插值分隔符**。这个控制台实际解析的是 `{name}`，**`{{name}}` 不替换**。本轮新写的计数文案按桌面
  （i18next）习惯写成了双括号，真机上整棵子树渲染成空注释；顺着查发现**既有的**
  `audit.chainRange` / `audit.chainBroken` 一直是原样显示 `{{from}}` 的——链校验那句结论从来没有
  真正插值过。locale 全量回单花括号（zh/en 同步）。
- **开关对读屏没有名字**（axe `aria-toggle-field-name`，serious）。五处 `FuiSwitch` 只把文字放在
  控件**旁边**（`ProjectsView` 的「显示已归档」、`ModelSheet` 的三个 `FuiCell`、`FuiSchemaField` 的
  boolean 分支），Vant 的 switch 不是 `<label>` 的隐式关联对象，读屏里就是一个没有名字的开关。
  现在五处都带 `:label`，用的是屏幕上那句可见文案。
- 长列表的翻页从"自己攒数组"改成 `useInfiniteQuery`（`useAuditFeed` / `useNotificationFeed` /
  `useUserFeed`）。自己写的 pages 数组在写操作失效缓存时会把已在屏幕上的那一窗**再接一遍**；分页游标
  这件事交给查询库，视图只读 `pages` 与 `hasNextPage`。被取代的 `useAudit` 一并删掉，不留第二条路径。
- SSO 失败的原因**不再与口令失败同格**（`oidcFailure` 与 `signInFailure` 分开）：口令被拒时下面那句
  「连续五次会锁定」是对的，而服务方 503 时它是噪音——那句话 S1 并没有说。

### 测试

- 单元 300 条（新增 `stores/oidc.test.ts` 7 条：入口只在部署有那扇门时出现、参数逐个转发、
  返回路径只认站内、提供者不可用不借用口令提示、`state` 以查询参数回传、同一份 code 只交一次、
  交换被拒不等于登录成功；`features/review/handoff.test.ts` 3 条：清单与 S2 的 `kind`、
  下载是真链接、NOT_GENERATED 走引导态）；端到端 253 条，含替身上一次完整的组织登录往返
  （含 `state` 不匹配的拒绝与提供者自己拒绝），证明的是界面行为；S1 的 state/cookie 比对与
  会话签发由 S1 的 pytest 负责。实测：`vue-tsc` 0、`eslint` 0 问题、`vitest` 300/300、
  `playwright` 253/253（含 17 屏 axe 扫描）。


### 变更

- **`/login` 取代登录弹窗**。弹窗有两个毛病：它把操作员正在看的那一屏压在模态下面，而那一屏引用的
  读数此刻全在回答失败；以及它的反馈天然无处安放——toast 会落在遮罩之下，操作员点了登录什么也看不到。
  页面自己承担三件事：**说明为什么会到这里**（"你的会话已经结束（超时，或口令被修改）"，
  而不是让人猜是崩了）、**说清下一次会去哪**（登录后回到原来那一屏）、**不留下能回退去的死页面**
  （跳转用 `replace`，过点那一屏不进历史）。
- **`?next=` 只认站内路径**：`next` 必须以单个 `/` 开头，`//host` 这种协议相对写法一律丢弃回首页。
  一个登录页最容易被做成的漏洞就是把用户带来的地址直接当跳转目标。
- 登录页**不带任何导航**（底部 tab、侧栏、顶栏的身份与搜索都收起来）：没有会话的人点这些只会一次次
  被送回登录页，那正是"点了没反应"的另一种形态。
- **「权限管理 → 账号与会话」里那份内嵌的登录表单删掉了**，换成一句"当前没有会话"+去登录的按钮。
  同一个应用里两份登录表单，迟早变成两套措辞、两套反馈。

### 新增

- `SignInView.vue`（页面）、`SignInFeedback.vue`（进行中 / 失败原因 / 锁定说明，两处入口共用同一措辞）、
  `stores/auth.ts` 的 `sessionLapsed`：显式退出与被过点区分开，前者不该说"会话已结束"。

### 修复

- **登录失败原先根本没有可见的落点**：`doLogin` 的 `catch {}` 只留一句"toast"的注释，而 toast 在
  模态下面。现在原因、"正在登录…"、"已登录：{姓名}" 都在页面上说，失败后口令清空、账号名保留。

## 1.4.0 — 账号可以在手机上停用与改名了（契约 1.7.2，依据 UI-AUTH-003 直引、UM-BR-01、FR-SEC-001）

### 新增

- **账号行上多了「停用 / 启用」与「编辑账号」**。这两件事 S1 一直都允许（`PATCH /users/{id}` 的
  `status` 与 `displayName`），桌面端有停用、这一端一个都没有——而 S1 没有删除端点，**停用就是
  唯一的退场方式**：能建号却不能收号的控制台只能进不能出。补上 `usePatchUser`，卡片操作区加两个按钮，
  改名走一张底部卡片（预填当前值）。
- **登录名不在可改之列**，界面上也不给这个入口：它是审计与审批记录里写的那串，改它等于改写历史。
- **建号时的「首次登录要求改口令」开关**，默认关。此前这一端从不提交该字段（即为 `false`），
  桌面端则写死 `true`，两边都没有选择权。CP-IAM-005 ③ 把它定为报告位，两种默认都不拦人，
  但这个决定应该由操作者做。
- **分配角色的面板里加了「项目范围」**。`roleForm.projectId` 一直在脚本里，模板却没有地方能填，
  于是从手机发出的每个角色都是组织级的——而同一行明明会显示 `角色 · 项目 id`，说明读得到却写不出。
  CP-IAM-002 的限定授予这一半，此前在手机上根本走不通。现在是一排可选项：**组织级（不限定项目）**
  加上当前可见的项目，**不是自由文本**——一个只能从别的屏幕抄进来的 id，提供的唯一功能就是猜错，
  而猜错现在会被 S1 判为 404。项目列表就是项目页读的那一份，不新开端点。

### 修复

- **替身不发 `user.manage` 之外，`PATCH /users/{id}` 也没有实现**：新按钮在端到端里会把请求发给一条
  落到"回显请求体"分支的路径，测试会拿着一个替身并不模拟的语义通过。现在替身真的改那行数据
  （显示名、邮箱、状态），并且**停用时把该账号的会话收掉**——与 S1 一致，本轮活体探测刚验证过这条。
- **替身没有实现角色那一组**：`GET /roles` 回答空清单，"分配角色"面板里因此一个可选项都没有；
  `PUT`/`DELETE /users/{id}/roles` 落到"回显请求体"的兜底分支，测试即使点下去拿到的也不是 S1 的形状。
  现在替身发出 S1 的六个内置角色，并真的增删那条记录——**收回是删除而不是打标**
  （`identity/service.py:revoke_role` 用 `session.delete`），所以"列表变短"才是诚实的回答；
  把这件事在代码里也写了一句（`role_view` 的注释）：`UserView.roles` 里不该有 `revoked: false`，
  因为每一行都是活的，加了反而暗示"可能不是"。
- **`npm run test:e2e` 不再依赖"你还记得先跑 build:harness"**。上一轮 68 条 `graph.perf`/`jsonEditor`
  失败就是这么来的：`/dev/*` 路由只在 harness 构建里注册（`src/router/index.ts:63`），
  而 `dist/` 里放的是生产包，症状是每条用例 20 秒的选择器超时，读起来像性能崩了。
  脚本现在自己先构建；两条 spec 在等不到入口时也会直接说"该跑 `npm run build:harness`"。

### 变更

- 契约升到 **1.7.2**：`SessionView` 关于 `csrfToken` 的说明改对了（值来自登录响应与可读 cookie，
  `GET /auth/session` 不返回它——本机活体读回的键表已确认）。仅描述与版本号变化，结构与 140 条路径
  一字未动，生成的类型已重新生成。

## 1.3.0 — 会话过点能自己登录回来，`help` 真的画出来了（契约 1.7.1 未变，依据 UM-NET-002、UM-P-01、UM-TECH-001、UM-AC-01）

### 修复

- **替身不发 `user.manage`，于是"权限管理 → 用户"这一整屏在端到端里从来没被点过**。
  `scripts/mock-s1.mjs` 的 `allowed-actions` 清单里没有这一项，而界面按 UM-BR-01 把"清单里没有"
  当作不可用——新建/授角色/重置口令全是灰的。`m4Absence.spec.ts` 只检查这些页面*不该存在*的东西，
  因此没有任何一条测试因为"按钮点不动"而失败：账号管理在移动端是**没被证明**，不是通过了。
  现在替身发放这一项（与 `user-guide.md` §6.6 的说法一致），并补一条真的建号的用例：
  登录名与姓名分别填写，写成功后列表按姓名把账号读回来，初始口令不留痕。
- **`FuiField` 从来没有 `help` 这个 prop**，而四处调用点都在往它身上写 `:help="t('…')"`。
  这个组件是把属性透传给 Vant 的 `Field`，Vant 没有 `help`，于是那句话落到 DOM 上变成一个
  没人读的 `help="…"` 属性——**四处提示在真机上根本不存在**，包括本轮刚加的"本机服务这栏留空"，
  以及凭证名字那格的说明。现在 `FuiField` 声明 `help`，并在输入格下面渲染一行次要文字
  （与各个 sheet 里 `ps__hint` 同一个形状）。组件级修一次，四个调用点同时活了。
  **加这层包装本身差点造成更坏的事**：包装 div 成了模板的唯一根节点，而 Vue 的属性透传是给根节点的，
  于是 `v-model`、`label`、`type` 与监听器全落在了 div 上，Vant 什么都没收到——表现是向导的"下一步"
  永远不亮、控制台一句错误都没有。修法是 `inheritAttrs: false` + 显式 `v-bind="$attrs"`，
  并由 `src/fui/fieldContract.test.ts`（不 stub 本组件）把"透传"这件事本身钉住：
  功能测试全都把 `FuiField` 打成桩，这类错误它们结构上就看不见。
- **会话过期后移动端不会把登录面板拉起来**。`needsLogin` 过去只从 `/auth/session` 的读结果推导，
  而没人去重读它：空闲 30 分钟之后，界面继续显示你是谁，而每一次写都被服务端拒
  （`UM-NET-002` 要的恰恰是这一条可见）。现在 `stores/auth.ts` 有 `markLapsed()`，
  `attachSessionLapseWatch()` 订阅查询与变更两个缓存，见到 401 就把会话收成"未登录"——
  保留 `authMode`（把它一起清掉会变成 `off`，那正是唯一会让登录面板消失的答案），
  并按 `logout` 已有的方式丢弃缓存而不重新去问。`api/queryClient.ts` 同时**不再为读上的 401 弹 toast**：
  会话过点是**一个**事件，一屏红 toast 会把它说成二十个问题。

### 变更

- **改自己口令之后明确登出**：S1 写口令时吊销该账号全部会话（`sessions.revoke_for_user`），
  所以改完必然要重新登录。面板现在把这件事写在字段下面，成功后立刻 `pushSuccess` 说明并让
  `auth.refresh()` 去问服务端"现在是谁"（答案是没有谁），界面自己回到登录态——
  而不是留下一个说不清的 401 让操作者猜。
- **改口令的入口不再只给被标记的账号**。`auth.mustChangePassword` 为真时才显示那一格，
  等于把"想换口令的普通人"推去找管理员；现在只要有会话就给出入口，标记的那句照旧额外显示。
- **用户列表说得出谁被锁了**：`locked` 早就在契约里（`UserView`），替身也早就回它，界面只把它用作
  红色调——于是一个被锁的账号读起来是**红色的 "ACTIVE"**，像配色出 bug。现在锁定时直接写「已锁定」，
  这也是本轮决定的那条路径：登录的回答继续不区分（否则就成了账号枚举通道），
  而"谁被锁了"这件事由 `GET /users` 说出来给管理员看。

### 门禁

- 新增 `src/stores/sessionLapse.test.ts`（4 条）：`markLapsed()` 保留 `authMode`、清空身份、把
  `needsLogin` 拉起来；重复调用不再二次生效；缓存里的 401 会驱动它、403 不会。
  这三条是"界面自己决定我还在登录"这一类错误唯一能钉住的地方。

## 1.2.1 — 四个按钮其实是死的（契约 1.7.1，依据 UM-BR-01、UM-ADM-001/004、UM-SET-001…006、S5IF-SEC01、S5IF-GOV02）

审出来的两轮：一轮对着契约读代码，一轮对着 `UM-BR-01` 读每一个会改东西的按钮。第一条改的是"看着在、点了没反应"。

### 修复

- **`EnvironmentFormSheet.vue` 少了 `FuiButton` 的 import，`GateView.vue` 少了 `FuiActionBar`**。
  `<script setup>` 的模板在**运行时**按名字解析组件：漏 import 不报错、不阻断构建、`vue-tsc` 也是绿的，
  Vue 只在没人看的控制台里留一行 `Failed to resolve component`，并把那个标签当成不认识的原生元素画出来。
  后果是环境表单的「加一条凭证」「加一个提交窗口」「删凭证」「删窗口」四个按钮，以及 Gate 页底部整条
  批准/驳回操作栏，在真机上是不响应的空壳。现在补回 import，并加守护测试
  **`src/test/componentResolution.test.ts`**：模板里每个 PascalCase 标签必须在同一文件里有绑定
  （组件引用自己的文件名——`OpenApiSchemaTree` 的递归——是 Vue 的合法隐式绑定，单独放行），
  同时断言扫描真的覆盖了全部 SFC，否则这条检查会悄悄退化成扫了个空集。
- **凭证录入不再自造引用**：保存之后如果响应里没有 `ref`，现在报错并把这条凭证丢掉，而不是把空串
  写进引用框——一个空的引用只会让下一次保存变成一个没人看得懂的 422。
- **`ACTION` 表补齐 8 个动词**（预算设置、阶段对话、用户/项目成员/产物授权/生产豁免管理、系统设置），
  与 S1 发出的名字逐字对齐。守护测试在 S1：`tests/unit/test_action_vocabulary.py` 同时读两个控制台的表，
  任何一边自造动词、或桌面端有而移动端没有，都会让 S1 的构建失败——这类错误在界面上长得和"权限不够"一模一样。
- **`useSaveSecret` 不再发 `Idempotency-Key`**：契约 1.7.1 撤掉了这条路径上的声明（它整段排除在回放表外），
  桌面端的生成类型已经会拒绝发送；移动端没有生成类型，就照契约改掉，别留一个只会被忽略的头。

### 变更

- **凡是要改东西的控件都过 `FuiActionButton`**（UM-BR-01）：环境表单与提供方表单的提交、治理页的预算与放宽、
  访问页的六个用户管理控件、开始页每一步的表单入口。此前这些按钮只看本地表单状态，等于这几屏自己解释可用性规则。
  纯本地动作（切换、展开、关闭）继续用普通按钮，代码里写明了为什么不需要服务端裁决。
- **e2e 替身的 `/secrets` 分支从来没生效过**（`scripts/mock-s1.mjs`）。它写的是
  `path.startsWith('/api/v1/secrets/')`，而这个 handler 在开头就把 `/api/v1` 剥掉了，所以条件永远为假：
  凭证写入落进了后面那个"把请求体原样 echo 回去"的兜底分支——也就是说，替身对 `PUT /secrets/{name}`
  的回答是**把刚录的密钥回显给浏览器**，正是这个端点最不允许出现的行为，而 `GET /secrets` 的列表也因此是空的。
  上一轮这条 e2e 之所以是绿的，是因为组件自己拼了 `db://<名字>`；本轮把那个自造引用改成"响应没给引用就是失败"
  之后，同一条 spec 立刻红了——这不是界面回归，是替身一直在骗它。现在两条分支按 `/secrets/` 匹配，
  回答与 S1 同形（`{name, ref, algo, createdAt, updatedAt, rotatedAt, createdBy}`，不含值）。
- **替身也一起对齐**：`scripts/mock-s1.mjs` 的工作流回答里那个 `project.member.add` 改成 `member.manage`
  （保持 `available: false, reasonCode: FEATURE_NOT_ENABLED`，正是 CP-UI-023 描述的形状）。替身发明一个
  名字，e2e 照样绿——它读的就是这份回答——而它教的是 S1 从不发送的拼法。这条现在由 S1 的
  `tests/unit/test_action_vocabulary.py` 一起检查：控制台的动作表与两个替身的 `action:` 字面量，
  都必须出现在 `allowed-actions` 能给出的那七个动作组里。
- **本机推理服务允许不填 Key**（契约 1.7.1 `apiKeySecretRef` 可省）：目录里 `local` 的条目默认不鉴权，
  坚持要一格只会让人编一个不存在的 `env://名字`。编辑时清空的那一格不上线——PATCH 声明 `min_length=1`，
  而缺席才是"不改"。
- **i18n 守护测试的正则原先漏掉 `$t(`**，也就是模板里的绝大多数调用（`t(` 匹配到了脚本里的手写调用，
  `$t(` 一个没看）。修正后覆盖 55 个文件 730 处引用，缺键的屏从此过不了构建。
- 无障碍：可展开面板补 `aria-expanded` / `aria-controls` 并把焦点送进第一个输入框；`FuiSwitch` 接受
  `label` 并绑到 `aria-label`；分段按钮与开关的最小热区统一走 `var(--fui-tap-min)` 而不是各处写死。

### 兼容性

- 契约 1.7.1 是修订号，字段集合与 1.7.0 一致（含被否掉的 `SecretView.keyFingerprint`，见 S1 CHANGELOG）。

## 1.2.0 — 引导一步一屏，凭证就地录入（契约 1.7.0，依据 UM-ADM-001/004、UM-SET-001…006、S5IF-SEC01…03、S5IF-GOV07）

### 新增

- **`CredentialField.vue`**（`features/secrets/`）：引用输入框旁边的「录入新凭证」。Vant `Field`
  `type="password"` + `autocomplete="new-password"`，Basic 认证把用户名与口令组成 `user:password`
  一个值，提交 `PUT /secrets/{name}` 后回填 `db://名字`。值不落任何浏览器存储、不进 URL，
  请求返回后无论结果都清掉；下面的下拉列已录入的引用（`GET /secrets` 只回名字与时间）。
  按钮走 `FuiActionButton platform` + `ACTION.secretManage`，可用性仍只来自 `allowed-actions`；
  503（没配主密钥）与别的失败分开说：前者写「改用 `env://` 引用」，不是含糊的「保存失败」。
- **`/start` 改一步一屏**：顶部一条可横滑的步骤条（当前一步 `aria-current="step"`），下面一次只展开
  当前这一步并就地打开它需要的登记表单；纯函数 `startSteps.ts` 的分支表原样保留，`StartCard.vue`
  继续给总览形态。`advance()` 只对「这一屏就能做完」的步骤开面板，其余照旧跳转 ——
  项目向导与工作台是页面，为了「留在本页」把浏览器历史弄成假的东西不值。
- **表单抽成可复用件**：`governance/ProviderSheet.vue`、`governance/ModelSheet.vue` 从 `GovernanceView.vue`
  拆出，治理页与引导页渲染同一份（两份表单=两套「什么算合法登记」，服务器规则一动就有一套先错）。
- 读模型与失效：`qk.secrets`、`qk.providerCatalogue`、`useSecrets/useSaveSecret/useDeleteSecret`；
  `useSetup` 在任何写入后失效，引导页因此不会滞后。

### 变更

- **模型那一步给两个入口**（登记提供方 + 登记模型）：模型必须挂在已存在的提供方之下，只给一个
  入口等于让操作者用一次 422 自己发现顺序。子系统那一步**不开**提供方表单 —— 那是 S2/S3/S4 真身与
  替身的选择（部署配置），浏览器里没有任何控件能改它。
- **`adapter` 从输入框变成只读常量** `openai_compat`：S3 的 `provider_for()` 不看这个字段，
  留一个能填的框就是给用户一个填了没用的输入框。厂商目录（`GET /governance/provider-catalogue`）
  只列 OpenAI 兼容厂商，地址与模型名仍可自由输入。
- 环境详情里的引用**去掉 `🔒` 前缀**：那是一个引用名，不是被遮起来的值；给它戴个锁的样式是在暗示
  这里藏了什么东西（S5IF-G09 禁止掩码式呈现）。
- `environments.secretsOnlyBody` 一类文案改为写实际后端的名字（`db://` 加密库 / `env://` 环境变量），
  `vault://`、`secret://`、`kms://` 不再作为可选项出现 —— 它们没有后端。同一轮对着**线上截图**又找出两句同样不成立的：
  `governance.keyRefHint`「密钥的值只留在服务器环境里」（界面录入之后不再成立）与
  `environments.referenceRequired`「必须以 vault://… 开头」（把三个没有后端的前缀当成合法答案）。
- vue-i18n 的插值踩过一次坑：`{ref}` 写成 `{{ref}}` 会连大括号一起显示出来（桌面 i18next 才是双花括号），
  `secrets.saved` 与 `governance.adapterFixed` 两条已改回单花括号，zh/en 同步。

### 兼容性

- 契约 1.7.0 只增不改；`/secrets` 不进幂等回放表，前端也不再把请求体摘要混进 `sessionStorage` 里的
  幂等 intent —— 对凭证字段那是一把可离线破解的指纹，`useSaveSecret` 只按路由与名字 mint 键。

### 门禁

- `npm run verify` 全绿：270 项单测（含 `credentialField.test.ts` 5 项、`startView.test.ts` 8 项）、
  `vue-tsc` 类型检查、zh/en 文案 parity、生产构建。
- **首屏预算没动**：`check:budget` 实测 138.4 KB / 200 KB（CSS 37.1 KB），懒加载块 57 个。
  引导相关全部走懒加载路由，新增的登记表单各自成块：`ModelSheet` 9.02 kB（gzip 3.11 kB）、
  `EnvironmentFormSheet` 12.41 kB（gzip 4.00 kB）。
- `scripts/mock-s1.mjs` 加 `/secrets`（只存名字，绝不回显值）、`/governance/provider-catalogue`，
  `/setup` 的 `credentials` 段在 `mode=bare|configured` 两档都有。
- Playwright（`build:harness && test:e2e`）：引导页 DOM 已换，用例需随后重跑确认。

## 1.1.0 — 装机引导与新建项目的诚实门控（契约 1.6.0，依据 UM-SET-001…005、UM-UX-003、UM-PRJ-001）

### 新增

- **`/start`「开始使用」**（`features/start/StartView.vue` + `startSteps.ts` + `StartCard.vue`）。
  与桌面 `/getting-started` 同一批事实、同一张分支表：子系统是真实还是替身、`validate_runtime()`
  的配置问题、登记了但从未通过连接测试的环境、模型与项目计数。`startSteps.ts` 是纯函数，脱离视图单测。
  入口三处：≥1024px 左栏、工作台顶部卡片、「我的 → 开始使用」。**不占底部五个标签之一**
  （`meta.tab` 的联合类型保持封闭，第六个标签在一屏上读不动）。
- **模型登记与逐行启停**（`POST /governance/models`、`PATCH /governance/models/{ref}`）。
  表单不提供服务地址与密钥引用：那两样由 S1 从提供方那一行带出来。
- 每一次写操作成功后一并失效 `qk.setup`（`api/mutations.ts:write`），清单不会滞后于刚做的那一步。

### 变更

- **新建项目的三个入口（工作台、项目列表、向导末页）一律由 `project.create` 门控**。
  此前 `ACTION.projectCreate` 定义了却没有一个调用点，用户填完三步才吃到 403（违反 UM-BR-01）。
- **向导第 3 步不再是空白卡**：无环境时给出原因与「去登记环境」的跳转。
- 环境页空数据时给出可点的创建入口与说明；登记/编辑成功后提示「先做连接测试再跑校验阶段」。
- 清理：`zod` 是声明未用的依赖、`wizard.envsHint` / `wizard.language` / `langDefault` /
  `governance.soonBudget` / `soonPrompts` / `home.goTodo` 都是定义未渲染的死键，一并删除；
  审计的「在电脑上继续」文案此前被复用在评测区，现改用各自的句子。
- 停用环境的不可恢复提示保持原样（`environments.disableBody`）：S4 没有重新启用的端点，
  界面不能承诺做不到的事。
- **`tests/e2e/helpers.ts:seedSession` 之前把操作人姓名按裸字符串写进 localStorage，而 store 用
  `readJSON` 读**（`lib/storage.ts`）。解析失败回落成空串，于是**移动端的每一个门控按钮在整条 e2e
  里都是禁用的**，只是过去的用例没碰到门控入口，所以没人发现。随着新建项目入口改为按 `project.create`
  门控，这个测试基建的缺陷立刻显形（预算用例卡在 60 秒超时）。现在两边都按 JSON 写入，并在注释里写明理由。
- **`FuiChecklist` 的一行改成网格**（`fui/FuiChecklist.vue`）。原先是 flex 一行：390 px 手机上长说明把
  右侧按钮挤到没有宽度，中文按字换行，「打开治理」竖成一列一个字，标签「子系统接入」也断成三行。
  现在是 `22px | 1fr | auto` 三列：图标跨两行，标签与按钮共用第一行，说明独占第二行的整个文字列。
  这一改动同时影响用同一个图元的其它行（漂移重查等），它们本来也只有"短按钮 + 长说明"这一种形状。
- **`X-Operator` 超出 Latin-1 时先百分号编码再发**（`api/http.ts:operatorHeader`，CP-API-002）。
  Fetch 标准把 header 值限制在 ISO-8859-1，Chromium 对中文操作人名直接在 `Headers.set` 抛 `TypeError`，
  所以修好上面那条 `seedSession` 之后立刻露出来的就是它：预算用例的每一步写操作都返回「无法连接服务器」。
  装得下 Latin-1 的姓名走原样，ASCII 调用方（含 `curl -H`）不受影响；解码在 S1 门口做。
- `scripts/mock-s1.mjs` 新增 `GET /setup`、治理路由与 `_control?mode=bare` 冷启动档；`bare` 的覆盖
  必须 spread 在字面量**之后**，否则会被自己的默认值盖掉（第一版就犯了这个错，表现为引导清单永远说「一切就绪」）。

### 兼容性

- 对 1.5.0 的 S1：`GET /setup` 404 → 页面显示「暂时读不到配置状态」并可重试；
  `project.create` 未被列出 → 新建项目按不可用处理（UM-BR-02「缺失即不可用」，绝不臆测）。

### 门禁

`features/start/startSteps.test.ts`（分支表：替身、未测试、只有生产、无权查看、被拒的 create、全就绪）、
`api/mutations.test.ts`（模型登记的动词/幂等意图、启停把引用放在路径里）。
`i18n/parity.test.ts` 的第 4 项自动覆盖新的 `start` 命名空间：静态 `t('a.b')` 在中英两边都必须存在。
Playwright 侧 `tests/e2e/startGuide.spec.ts`（UM-AC-20/21）在 `_control?mode=bare` 下验证：工作台顶出未完成的那一步、
清单五点能跳到位、向导停在环境一步给出原因与入口；配置齐全后引导卡收起。
首屏预算仍达标（`check:budget`，清单视图懒加载，不进首包）。

## 1.0.0 — M1+M2 移动优先 + M3/M4 读写补齐

见 `../user-guide.md` §7 与本目录 `docs/adr/`、`docs/reports/`。
