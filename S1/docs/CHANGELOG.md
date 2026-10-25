# CHANGELOG — S1 公共 REST API

版本号即 `docs/openapi.json` 的 `info.version`（S5IF-G11）。破坏性变更提升主版本。
`0.2.0` 是命名与形状统一的一轮；`1.0.0` 是 C 类条目全部裁决后的一次成契约交付；
`1.1.0` 是对照修订后的 SRS V1.1.0 逐条复核的一轮。

## 1.19.0 —— 问题改成整页问卷、一次作答（契约新增 1 条路径：146 → 147）

设计阶段的问题原先只能一题一答，答案只能是一个建议或一段自定义文字。模型其实已经能给出多个候选，
但控制台没有地方让人一次看完，也没有「多选」这种业务答案；一题一答还会让 Agent 在第一题之后
就继续往下跑。现在：

- **`POST /api/v1/questions/answers`（CP-Q-005）** 一次提交同一阶段的整份问卷，先在一个事务里
  记录全部答案，再最多 resume 一次；响应带回 resume 之后仍在开放的问题，因此第二轮问题可以
  立刻显示成新的问卷，不需要客户端再猜一次。
- **`QuestionView.answerMode`（`SINGLE`/`MULTIPLE`）与 `SuggestionView.recommended`**：模型在
  `questions[*]` 上声明单选还是多选，并在建议答案里标出推荐项；`QuestionAnswerView` 增加
  `suggestionIds`/`suggestionTexts`，多选答案不再被压成一条字符串。
- **同一次运行内的多轮问题不再撞 id**：S3 从第二轮起用 `Q2-1` 这类带 attempt 的本地 id，
  避免 S1 按 `(agentRunId, sourceLocalId)` 去重时把新问题静默丢掉。
- **语言随界面走**：`POST /stages/{id}/run|rerun` 与 `.../questions/.../answer` 从
  `Accept-Language` 取当前界面语言，经 `AgentRunRequest.language` 送到 S3；S3 把语言指令放进
  每轮重建的 system message，续跑的轮次不会沿用上一轮的语言。chat / explain 同样携带。
- 迁移 `0005_cp_question_answer_mode` 为 `cp.questions.answer_mode` 增加默认 `SINGLE`，历史问题
  按单选的旧行为继续可答。

## 1.16.0 —— 手工提案能连续编辑、解析能重试、提供方能测连接（契约新增 1 条路径：143 → 144）

移动端要"在线新增/编辑需求"，桌面端要"重试解析"，两边都撞到同一类后端缺陷：幂等键写成了"对象 + 尝试次数"，
于是同一个对象第二次提交不同内容必然 422。第三件事是操作员根本没法验证提供方配得对不对。

- **手工提案的幂等键交还给提交者**（`services/steps.py:_manual_patch_key`、`rest/routes/workflow.py:submit_patch`）。原先是
  `patch:{阶段}:{尝试次数}`，S2 的摘要覆盖 operations、S0 一见同键不同摘要就抛冲突，所以一个阶段尝试
  只能落第一条手工需求——第二条、以及"改回来"都是 422 IDEMPOTENCY_CONFLICT。
  第一次修正把内容摘要放进键里，线上验出它自己的一处错判：**删掉一条再加回同一条**时草稿又回到之前那个
  哈希，内容＋草稿哈希完全相同，于是这次新动作被当成重放、返回第一次的结果，界面写着「已保存」而条目没回来
  （`test_real_s2.py` 里那条 delete-then-re-add 断言就是为它写的，去掉这段会红）。
  现在以调用方带来的 `Idempotency-Key` 为准——两端都是一次动作一个键、被答复即作废（CP-API-003），这才能区分
  「这条请求没送达，请重来」和「同样的内容我这次是当新动作再写一遍」；没有该头时（curl、测试客户端、旧控制台）
  退回 `patch:{阶段}:{尝试}:{内容＋草稿哈希摘要}`，仍能让一个阶段尝试连续编辑、并让同键同草稿的重放只生效一次。
- **解析的幂等键带上这次提交的身份**（`services/inputs.py:parse_input`）。原先 `parse:{材料}` 把一个输入
  永久钉在第一次的答案上：修好提供方之后再点「解析」，要么 422，要么原样回放上次的失败。现在控制台的
  `Idempotency-Key`（一次动作一个，被答复即作废）参与键的构成，重试真的会重跑；HTTP 层的重复点击仍由
  `IdempotencyMiddleware` 拦住。
- **`POST /governance/providers/{name}/test-connection`（S5IF-GOV20）**。S3 的 `health()`（`GET /models`，
  5 秒预算）早就存在但没有端口、没有路由、没有按钮，于是"地址写错 / 密钥被拒 / 模型比请求超时慢"三种
  问题在界面上都是同一句 `model endpoint unreachable`。现在三者能分开问出来，且探测不产生任何调用费用、
  不回显密钥引用（AI-LLM-005）。契约因此从 143 条路径变成 144 条，`info.version` 提升到 1.16.0。
- 测试：`tests/service/test_real_s2.py` 钉住"同一阶段尝试里第二条不同内容的提案是 PROPOSED、相同内容重放
  不挪草稿哈希"；`tests/api/test_s5if_endpoints.py` 钉住探测的 200 语义（连未注册的提供方也是答案，不是错误）。

## 1.15.0 之上 —— 真实提供方链路上的两个"打开阶段"报错（该轮自身不动契约；本轮的提升见上面 1.16.0）

点「打开阶段」弹两条错误：一条 500，一条"请求的对象不存在"。两条都不是操作者做错了什么。

- **`GET /stages/{stageId}/chat` 在真实部署里必然 500**。S1 的端口声明 `get_chat -> list[dict]`
  （`ports/protocols.py`），内存 fake 也返回 list，于是整套测试全绿；而 S3 的
  `factory_intelligence.api.get_chat` 返回 `runtime/chat.py` 的 `Transcript` —— 一个**普通
  `@dataclass`**，turns 挂在 `.turns` 上。`adapters/delegating.py` 的 `_coerce` 只认识 pydantic 模型、
  dict 与 list，dataclass 原样穿透，`services/chat.transcript` 于是去迭代一个不可迭代的对象。
  修法是适配器做这一次形状翻译（`IntelligenceAdapter.get_chat`），端口声明不变——与
  `tests/unit/test_registry_refs_shape.py` 钉住的是同一类漂移。
- **`GET /projects/{projectId}/blueprint/draft` 把"还没有草稿"说成了"对象不存在"**。首个设计阶段没跑
  就没有草稿（该项目在 `bp.blueprint_draft` 里 0 行、REQUIREMENTS 仍 READY），S1 回的是裸 404；同文件
  其它四处"尚未生成"早就用 `reason=NOT_GENERATED` + `requiredStage`（CP-UI-022）。现在这条也补上，
  两端据此当空态渲染，而不是弹一条要人去找管理员的错。

### 为什么测试没抓到

`FACTORY_PROVIDER_MODE=fakes` 下 fake 直接返回端口形状，`get_chat` 的契约只在真实链路（`real`）里成立。
`tests/unit/test_registry_chat_transcript_shape.py` 用真实的 `Transcript` 喂适配器钉住形状，
`tests/service/test_real_s3.py` 再走一遍 REST（S3 装不上则 skip，与 `test_real_s2.py` 同规矩）；
引导式 404 的 `details` 由 `test_real_s2.py` 那条"跑阶段前 404"的断言钉住。

## 1.15.0 — 模型有了真正的编辑动词：PATCH 只写被点名的列（依据 S5IF-GOV06、UI-SET-005a、AI-LLM-003、S5IF-G04）

`PATCH /api/v1/governance/models/{ref}` 从前只收 `{enabled}`，与它声明的 `model.enable` 一个动作。界面因此没有
"改一个已登记模型"的入口：唯一能改整行的办法是重新 `POST`（upsert），而那条路径会把没给的 `endpoint` 与
`apiKeySecretRef` 按缺省写成 NULL——静默清掉 AI-LLM-003 的模型级覆盖；更糟的是 S1 的读路径把 NULL 的模型地址
回填成提供方的地址，界面再把那个值当覆盖发回去，**覆盖一旦设上就再也撤不掉**。重新登记也记不下改前是什么：
审计里只有一条与"新建"同类型的事件，没有 `before`。

- **请求体改成差量**：`capabilities`／两个单价／`applicable_agents`／`enabled`／`endpoint`／`apiKeySecretRef` 全部可选，
  `extra="forbid"`，一个字段都没点名 → 422（服务端拒绝一个空 PATCH，界面据此把"没改动"按住保存按钮）。响应从
  `{ref,enabled}` 换成整行 `ModelView`。
- **S3 侧新增 `update_model`**：按 `provider/model` 找到行，**只赋值被点名的列**；`capabilities` 在存储的载荷上按键
  合并，所以只改窗口不会把另外两个开关打回默认。审计事件记下 `before` 与 `after`，并标出这是编辑动作。
- **授权不动词表**：沿用 `governance.model.enable`——提供方 PATCH 复用 `provider.enable` 是同一手，动作表、
  `test_action_vocabulary.py` 与两端的 `actions.ts` 因此都不必改。
- **删掉两个契约里没有的名字**：`EnableRequest` 与 `RefEnabledView`（后者是路由早就不再返回的形状）。
- **路径数不变**：143 条，`CONTRACT_PATHS` 不动；提升次版本是因为 PATCH 的请求与响应形状都变了。
- 顺手：`CapabilitiesView` 的默认值从 `maxContextTokens=0` 改成 8000。0 的窗口在选择器里筛掉一切请求，
  而"登记时没填 capabilities"以前正好落到它——登记成功、模型永远不可用，且没有一处说为什么。

## 契约 1.14.0 之下 —— 运维与治理的一轮（本轮不动契约：路径与模型零变化，`/readyz` 以 `include_in_schema=False` 注册，故不提升 `info.version`）

### 修好的是"装着但没接上"的那一类：配置解析了、没人读；端点在白名单里、没有路由；任务写在包里、没有进程跑它

- **`damf-worker` 有了 systemd 单元**。`install.sh` 现在渲染两个单元（`damf-api` + `damf-worker`，同一个
  `EnvironmentFile`），`start.sh` 对两者启停/状态/日志（`UNIT=damf-worker ./start.sh --logs` 看一个，默认两个都打）。
  八个周期任务（审计链回填、事件清理、投递重试、Gate 催办……）第一次真的在跑。顺序是硬的：worker 必须等
  api 过了 `/healthz` 再起——迁移没有并发锁，两个进程同跑 `upgrade` 只在库已是 head 时才是无害空操作。
- **`/readyz`**（`rest/app.py`）：拿运行时自己的 unit of work 真执行一条语句，数据库不可达回 503。
  `/healthz` 的 `status:"ok"` 从来是常量，它没证明过 PostgreSQL 活着——负载探测器不该拿到那个 200。
  它不进 OpenAPI 文档：基础设施探针不是任何控制台可以调的操作，143 条路径的钉数不变。
- **`FACTORY_SSE_CHANNEL` 两侧统一了**。以前 NOTIFY 写死常量、LISTEN 读配置：改这个值等于把实时推送
  静默切断，只剩 `sse_poll_seconds` 兜底轮询假装一切正常。现在两侧同读一个值，且频道名必须是
  PostgreSQL 标识符——`validate_runtime` 在启动就拒绝非法名（以前它让 LISTEN 任务在无人 await 的
  协程里抛 `ValueError` 无声死掉，CP-EVT-005）。
- **`APP_ENV=production` 不再让进程起不来**：bootstrap 按 `is_production` 分支，生产只挂 memory 后端
  （`env://` 引用按 FD-SEC-001 的设计预期失败，凭证走 `db://`）。S0 的 `install_development_backends()`
  本身没动——它的 production 拒绝是对的，改它要波及 S4 的十几个 conftest。
- **`FACTORY_DX_*` 活了**：S4 `initialize()` 现在调 `configure(None)` 从环境重载设置。以前模块级
  `_SETTINGS` 是 import 期的裸默认、`configure()` 全库无人调用——整套 DX 旋钮（超时、重试、批大小、
  异步导入阈值）被静默钉死。`load_settings()` 顺手补了未知键防御（历史上 `_INT_KEYS` 列了而字段没有的
  环境变量只会继续被忽略，不会让进程在启动时 TypeError）。
- **两个配置键接了线、一个删了**：`FACTORY_NOTIFICATION_MAX_ATTEMPTS` 真正决定投递重试上限（以前硬编码 5）；
  `FACTORY_AUDIT_LINK_INTERVAL_SECONDS` 真正决定链回填节奏（间隔以前在 import 期冻进 `JOBS`，现在
  `job_interval()` 每次调度时解析）；`FACTORY_TEMPORAL_RETENTION_HOURS` **删除**——retention 是 Temporal
  namespace 的属性，而代码库里根本不存在 namespace 创建逻辑，留着一个没人读的键就是假配置。
- **enforced 模式下审计不再是 "system"**：中间件顺序（TrafficLog→RequestContext→Session，后加的在外层）
  让 `RequestContext` 用 `X-Operator` 绑 S0 操作上下文时会话主体还没写进来，靠 contextvar 读 operator 的
  审计事件全部记成 "system"。SessionMiddleware 现在在认出主体后**再绑一次**（S0 的 bind 是叠加式
  `with_()`，最内层为真），enforced 下会话主体压倒声明式头名。顺带清掉 `middleware.py:167` 那个两支同值的
  死三元。
- **`factory-worker`/`factory-api` 的命令行不再污染配置单例**：`--port` 以前原地改 `lru_cache` 的
  Settings，同进程之后每个 `get_settings()` 消费者都看到假端口；现在 `model_copy` 出局部副本。

### S3 侧（契约之外的治理行为）

- **未激活的 Prompt 现在是拒绝，不是空跑**（AI-REG-002）。以前 `current_activation` 返回 None 会被
  换成一个空 body 的占位 Prompt 继续跑：上下文里没有任何 authored 指令，运行照常计费，产出的 JSON
  只有信封约束——`activation.py` 的 docstring 一直声称"运行时会拒绝"，这次是让实现追上那句话。
  `start_agent_run` 返回不建 run 行的 FAILED 结果（拒绝理由点名 agent，BLOCKING issue 引 AI-REG-002，
  错误码 `AI_PROMPT_NOT_ACTIVATED`），`resume`/`stage_chat`/`explain` 三条旁路抛同一错误码——旁路
  能用占位跑等于绕过治理。三个占位构造器删除。新机器接真实 S3 后需要先跑激活链：
  `S1/scripts/activate_prompts.py`（评测→回归→激活，幂等）。
- **`review.explain` 升 v2**：v1 正文引导模型"stage rules 允许时把变更放进 patch"，而 REVIEW 的
  spec 是 `EXPLAIN_ONLY`、`accepts_patch()` 恒 False——运行时永远不会提交它，提示词在教模型做一件
  注定不做的事。v2 明说 explain-only、`patch.operations` 恒空，并补上它唯一缺失的 `## The answer`
  五键信封段。v1 文件保留（已激活版本哈希不动）。
- **`_similar` 不再吞损坏的 discovery 产物**：以前 `json.loads` 失败静默给空列表——恰是 `similar.py`
  自己声明要避免的答案（"没有相似物"在棕地里最贵）。现在解析统一走 `parse_discovery_result`，读不出
  就降级为空上下文**并发一条 WARNING issue**（AI-AG-011）：相似样例是辅助上下文，不该失败整个 run，
  但审阅者必须看得见这轮没带复用证据。
- **stage rules 只注入一次**：以前 system 消息与独立 STAGE_RULES 段各一份，同一文本付两遍 token，
  还让 eval 与 runtime 的 system 半段漂移（`eval/runner.py` 从不拼 stage rules）。builder 的独立段
  保留（带 per-segment ref 与出处），`render.system_message` 删参。
- **阈值口径统一为"below"**：判定与 SRS 从来都是严格 `<`，只有注入给模型的句子写 "at or below"。
- **截断有了像样的反馈**：`finish_reason=length` 时纠错消息现在明说"上一次回答被 max_output_tokens
  截断，JSON 不完整，请压缩输出"，而不是让模型猜 schema 错在哪；`default_max_output_tokens` 4000→8000
  （五键信封带 patch operations 的场景 4000 确实不够）。
- **记录在案、本轮未修的三条**：用户回答在 resume 时硬编码按 INTERNAL 分级（继承 run 分级会把
  RESTRICTED 传染给恢复的上下文、可能被策略拒读，需要独立的"回答分类"设计）；每次模型调用新建
  httpx.Client（provider 工厂本身 per-call，复用要动生命周期，收益 <5%）；激活时验证过的
  `model_list` 运行时并不消费（选模走 Model Registry 的 per-agent 优先级——闸门的这一半是纸面的，
  与 AI-REG-003 的语义要一起裁）。

## 1.14.0 — 定向删除有了入口、跨项目列表按成员关系过滤、外发上限到了 S3 不再塌成兜底（依据 CP-CR-005、FR-CMP-009、BP-CMP-026、DX-EXE-038、FR-SEC-004、DX-ENV-001、AI-POL-005、总体 SRS 15.6 与 20.2）

1.13.0 让 R1 把候选清单交给编译，但那只是这条路的**后半段**：前半段没有任何地方能说出"这次要作废哪些对象"。
`CreateChangeRequestBody` 只有 reason 与 affectedBusinessKeys；`analyze()` 又把行上的候选**覆写**成
`analysis.deletion_candidates`，而 S2 的 `ImpactSet`（`dto/patch.py`）**根本没有这个字段**——真身下它恒为 `[]`，
于是"分析一次就把操作员声明的东西抹掉"。S1 自己的替身反倒**发明**了一条规则（businessKey 以 `:deprecated`
结尾才算候选），正因如此这条断处在 fakes 下从未暴露：假的一方有，真的一方没有。

### 变更（请求体新增字段）

- **`POST /api/v1/projects/{project_id}/change-requests` 请求体接受 `deletionCandidates`**（字符串数组，缺省空）。
  授权写在请求自己身上，与总体 SRS 15.6"定向删除：通过 Change Request 删除…对象"同一读法；
  `ports/dto.py` 原本也记着这一句（"候选清单是 S1 自己的判断，S2 的影响集不提供"），只是没有任何入口去表达它。
- **命名当场校验**（`services/change_requests.py:_declared_deletions`）：项目必须已有 Blueprint 版本，且每个被点名的
  businessKey 在当前版本的 Registry 里处置为 `DEPRECATED`，否则 422 `VALIDATION_ERROR`，
  `details.notDeprecated` 列出对不上的名字。这里防的不是越权——BP-CMP-026 在编译期还会再核一遍引用与关联数据——
  而是**一句永远不可能兑现的声明**：非 DEPRECATED 的名字会被编译期静默剔除，界面显示删除执行过、
  对象却还在实例上。项目还没有版本时同样拒绝，因为"要作废哪个版本里的对象"无从回答。
- `analyze()` 不再覆写：影响报告与行上保留的都是请求自己的声明，S2 的影响集只回答阶段/对象/页面/产物类型。
- 随之删掉 `ports/dto.py:ImpactAnalysis.deletion_candidates`（已无人读），并把 S1 替身里那条发明出来的规则去掉——
  替身必须与真身同形，否则它就是在替缺陷作证。

### 验证

`tests/service/test_flows.py` 两条：一条走完"建请求（带候选）→ 分析 → 批准 → start → 重开设计线 → 跑完 Run"，
断言编译收到的候选就是请求点名的那一条，并断言影响报告里它没被抹掉；另一条断言非 DEPRECATED 与不存在的名字
被 422 拒掉、DEPRECATED 的被接受。CR 创建在两个控制台都补了输入项（S5 1.9.0、S5M 1.10.0）。

### 变更（行为，响应形状不变）

- **`GET /api/v1/projects` 与 `GET /api/v1/deployment-runs` 现在按成员关系过滤**（FR-SEC-004，
  S1 SRS 3.10 自己列的"剩余：按成员关系过滤"就是这一条）。两条路径的 URL 里没有项目，
  于是过去只能问组织级那一句——然后**无论答复是什么都把全部项目交出去**。隔离靠的是
  "没有哪个非管理员角色会在组织级被授予 `project.view`"这个巧合，而不是代码。
  现在分成两种答复（`rest/deps.py:visible_project_ids`）：组织级持有 `project.view` 的人（平台管理员，
  或被组织级授予该角色的实施者）仍看到整个组织——那正是管理界面该有的视野；只有项目级授权的人
  只看到自己所属的项目，`total` 与行数一致（过滤在查询里做，事后裁剪一页会让 `total` 数到看不见的项目，
  翻页也会重复或漏掉）。一个项目级成员在这两个页面上过去拿到的是 **403**：他连自己刚建的项目都列不出来。
  过滤对 Run 同样成立，Run 只经 workflow 关联项目，所以 join 在查询里。
- **`environment.*` / `governance.*` / `secret.manage` 维持在组织级答复，不改动作名、也不从项目角色里删**
  （这条裁决写明在此，避免下一轮再猜）。附录 B 给的是一个动作名词表，而 SRS 3.10 说的是"组织级与项目级
  授权"两级——这些动作的对象（环境、Provider、模型、凭据）为整个 Factory 共有，URL 里没有项目可问，
  所以按组织级答复是对的。**同一个角色名可以两种范围并存**：部署可以把 DHIS2_IMPLEMENTER 授在组织级
  （那个人给全组织登记环境、录凭据），也可以授在项目级（同一个人在项目里执行 Run）。
  因此既不能为这两个页面去造"组织级专用动作名"（附录 B 没有这个名字，造出来只会让两个控制台的
  ACTION 表与 `allowed-actions` 的回答集合各说一套），也不能把它们从项目角色表里删掉——那会连带取消
  前一种合法授权。守卫的例外清单里这几条的理由就是"组织级对象"。

### 契约版本与三份副本（S5IF-G11）

- 导出脚本 `scripts/export_openapi.py` 的写法是 `json.dumps(..., indent=2, ensure_ascii=False, sort_keys=True)`。
  三份副本（`S1/docs/openapi.json`、两个控制台的 `contract/openapi.json`）现在按**同一写法逐字节一致** ——
  此前手工复制没带 `sort_keys`，两份 vendored 副本只是"内容相同、键序不同"，任何按文本 diff 的核对都会误报。
- `tests/api/test_openapi_contract.py::test_document_version_and_servers` 把版本号钉成断言（本条提版时要同时改它，
  这是故意的：**提契约的人必须显式承认自己在提契约**）。
- 两个控制台各新增 `src/test/contractVersion.test.ts`，钉住自己 vendored 副本声明的版本与路径数
  （S5 1.9.1 / S5M 1.10.1）。生成文件是签入并被编译的，所以副本落后于现实时类型检查依旧全绿 ——
  本机真的因此漏过一次 1.10.0 的 `codePrefix`。

### 修复（跨子系统；契约不变）

- **环境上配的外发上限到了 S3 一律塌成 `LOCAL_ONLY`**。S4 的保护文档字段名是 `llmDataPolicy`
  （`DX-ENV-001`），S1 原样转发（`services/steps.py`），而 S3 的归一化器只认
  `dataExportPolicy` / `policy` / `exportPolicy` 这几个包装名与 `allowedClasses` 一类列表 —— 于是**每一份**
  环境文档都落进它自己的兜底分支，操作员把上限调到 `APPROVED_EXTERNAL` 也换不来一次外部调用能成：
  登记一个 `PRIVATE_NETWORK` 的提供方，设计阶段每一次都会被策略拒掉，界面上只剩一条 fail-safe 警告。
  复现原文：`unrecognised keys ['environmentClass', 'llmDataPolicy', 'outsideCommitWindowReason', 'rank',
  'refusedWhen']` → `falling back to LOCAL_ONLY (fail-safe)`。修法两半，缺一不可：
  ① S3 认 `llmDataPolicy` / `maxProviderClass` 这一类**上限**写法；② 上限按"该类别**及一切更严格者**"展开 ——
  `ProviderClass` 的定义就是"数据最远能到哪"（`总体 SRS 20.2`），读成恰好一个类别会让更宽的天花板反过来
  **拒掉**更严格的本地模型。`PHI_PII` 无论如何都为空（`AI-POL-005`），取值不认识时仍然落兜底并给出
  `unknown provider class`。
  接缝只有 S1 能钉：S3 不得 import S4（00 文档第 5 章），S4 不解析策略，而 S1 把三个包装在同一个 venv 里 ——
  故新增 `tests/service/test_real_s4_deletion.py::test_the_ceiling_s4_states_is_understood_by_s3`
  与 `test_a_wider_ceiling_admits_the_stricter_classes`。
- 顺带清掉 S3 里 4 处静态检查错误，其中一处是真缺陷：`factory_intelligence/testing/fakes.py` 的返回标注写了
  不存在的 `dto.RegressionView`（该名字在文件里是以裸名导入的），靠 `from __future__ import annotations`
  才没在运行时炸，`get_type_hints` 一调就现形。
- **删除候选的形状两端各钉一份**：`S2/tests/unit/test_compile_rules.py` 断言发射出去的行就是
  `{businessKey, dhis2Type, uid, reason}`，`S1/tests/service/test_real_s4_deletion.py` 用同一形状走通删除门。
  为什么必须两端都钉：DEPRECATED 对象不进 `manifest.objects`，S4 那条"按 businessKey 回填 uid"的路**够不到**它们，
  所以行里没有 uid 就只能被 `InputArtifactInvalidError` 拒掉 —— 单侧改动会静默把这条链断在中间。

### 仍待办（本轮说清楚、没动手）

- Run 摘要不回那份包的产物 id（`S5IF-FLD05` 只有 `snapshot` 与 `execution` 两个槽位），所以界面一键删除还需要
  S1 先把包 id 放进摘要；目前只能从 `GET /stages/{BIND_COMPILE}` 的 `outputArtifactIds` 取（`GET /workflows/{id}`
  的紧凑阶段视图不带这个字段）。运维侧的取法与理由写在 `user-guide.md` §8.5 与 §11.27。

## 1.13.0 — 清除要有回执、次级键要查重、CR 授权的删除候选要送到编译（依据 FD-ART-010、S5IF-FLD05、BP-VAL-010、CP-CR-005、DX-EXE-038、FR-TST-002）

### 变更（响应形状，新增字段）

- **`ArtifactSlot` 新增 `contentPurged`（布尔，缺省 false）**，`services/runs.py:_artifact_slot` 读产物行的
  `content_purged`。1.12.0 给槽位配上 status，是为了不再给控制台递一条点下去 410 的下载链接；但
  `ArtifactStatus` 只有 DRAFT/PROPOSED/APPROVED/SUPERSEDED —— **清除不是一个状态**（FD-ART-010 把它记成行上的
  布尔，S0 读已清除的字节时抛 `ArtifactPurgedError`）。于是 `status: "APPROVED"` 的槽位既可能是"能下载"
  也可能是"字节不在了"，而这两种情况在界面上必须是两个答复。同一个模型也用在 `summary.snapshot` 上，一并生效。
- 两个控制台的生成类型与随包契约同步重生成。**`install.sh` 现在会在生成类型与 `contract/openapi.json`
  不一致时中止构建**：构建编译的是签入的 `src/api/generated`，所以契约改了而生成文件没跟上时 typecheck
  照样是绿的——本轮 S5M 就是这样把 1.10.0 新加的 `codePrefix` 漏了过去。

### 修复（后端，响应形状不变）

- **两条 apiIntent 共用一个 operationKey，过去过得了校验、到 Gate 批准时才 500**。
  `bp.business_key_index` 的主键是 `(project_id, blueprint_version, section, key_kind, entry_key)`，
  而 `key_kind` 不只有 businessKey：索引器为每条 apiIntent 额外写一行 `operationKey`（UI 组件按它引用操作）。
  S2 的 `duplicate_keys` 只按容器的**主键**查重，于是重复的 operationKey 一路穿过
  REQUIREMENTS→DOMAIN→…→freeze，最后由 `promote_draft` 的 INSERT 撞 `pk_business_key_index`——
  一个 psycopg `UniqueViolation`，即 500，而且离真正写出它的那条条目隔着两跳。
  现在 `EntryContainer` 自己声明 `secondary_keys`，**查重、引用解析、写索引三处读同一份声明**；
  冲突折进 BPV-BP-002（BLOCKING，消息点名是哪个字段），Agent 提交的这条 patch 当场被拒，
  freeze 则给出 `promotable: false`。
  验证：撤掉这一改动后重跑同一份文档，`bp.business_key_index` 仍抛
  `Key (project_id, blueprint_version, section, key_kind, entry_key)=(…, apiIntents, operationKey, …) already exists`，
  而 patch 与 freeze 都放行——生产症状原样复现。

- **一次 Change Request 授权的删除候选，从来没有到达编译**。R1 取"本项目那一条 IN_PROGRESS 的请求"用的是
  `store.in_progress_change_request`（它的查询条件就是 `status = 'IN_PROGRESS'`），取回来之后却再问一句
  `status == "APPROVED"`——CP-CR-002 里 APPROVED 是被 `start` 迁到 IN_PROGRESS 的，CP-BR-12 又规定一个项目
  最多一条 IN_PROGRESS，所以这句判断**永远为假**：编译从未被告知是哪次请求授权了它，S1 也就不再传候选清单。
  S2 的 BP-CMP-026 要求"请求号 + 候选清单"两样都有才收条目，于是产物的 manifest 里 `deletionCandidates`
  恒空，`POST /environments/{key}/delete-objects` 对任何在册项目都拿不出可提交的载荷（FR-TST-002 的清理
  在真机上无路可走，此前只能像本轮那样绕开 S4 直接 DELETE）。
  现在 `compile_for_environment` 增加 `deletion_candidates`（S1 端口、替身与 S2 适配器三处同步），
  R1 按 CP-CR-002 的正确读法把 IN_PROGRESS 请求的候选交出去；候选为空对 S2 无害——它仍逐条要求
  DEPRECATED、依赖图无人引用、目标实例无关联数据。
- 此前**没有任何测试**断言过 R1 怎么调用 S2，这条断裂才得以长存。新增
  `tests/service/test_flows.py::test_an_authorized_request_hands_its_deletion_candidates_to_the_compile`：
  走公开 API 建请求→影响分析→批准→start→重跑被作废的设计线→跑完 Run，断言 S2 替身看到的候选清单就是
  请求里那一条；撤掉本次改动它当场失败（收到空清单）。`tests/helpers.py:finish_design_line` 顺带补上
  CP-CR-004 作废后的重做：附录 A 只允许 INVALIDATED 经 rerun 边回到运行。

## 1.12.0 — Run summary 的 execution 槽位承认它本来就返回对象（依据 S5IF-FLD05、CP-UI-012、S5IF-G03）

`GET /api/v1/deployment-runs/{runId}/summary` 在有真实执行报告的生产环境里对**任何身份**都 500（管理员亦然）。

### 破坏性变更（响应形状）

- **`summary.execution.reportArtifactId` 与 `importReportArtifactId` 由 `string` 改为 `{status, artifactId}` 对象**
  （复用已有的 `ArtifactSlot`，与 `summary.snapshot` 同一形状）。
  生产者 `services/runs.py` 的 `_artifact_slot` 从一开始就返回这个对象，而且**故意**如此：
  报告产物可能已按保留策略清除，只有带着 status 控制台才能不给出一条点下去 410 的下载链接。
  模型把字段声明成裸 id，于是 FastAPI 在序列化响应时校验失败——两个字段、一条 500。
  两个控制台都没有读这两个字段（它们拿 `execution.executionId` 再查 `/executions/{id}`），
  所以实际渲染不受影响；受影响的是按旧声明写死的第三方客户端。

### 为什么测试没抓到

`tests/api/test_s5if_acceptance.py` 那条 summary 用例覆盖的 Run，其 COMMIT 阶段没有 `EXECUTION_REPORT` /
`DHIS2_IMPORT_REPORT` 产物，槽位永远是 `null`，声明的形状从没被校验过。
现在该用例注册两个真产物并断言其形状，改回旧声明即复现生产的 `ResponseValidationError`。

## 1.11.0 — 授权不再凭空问项目角色的动作（依据 FR-SEC-002、FR-SEC-004）

`directory.roles_of` 只有在检查**说出是哪个项目**时才回项目级角色。因此凡是"项目角色也持有"的动作，用
裸 `authorize(request, action)`（不带项目）去问，等于把它悄悄限制成只有组织级角色能用：
`auth=off` 下策略放行，测试全绿；真机上是一连串 403。这是生产部署实跑出来的：
`patch.submit`、`gate.approve.DEPLOY_GATE`、`project.view`（Run 视图）与 Change Request 全家的拒绝，
根因同一条。

### 变更（无破坏性）

- **`GET /api/v1/upload-limits` 新增可选查询参数 `projectId`**（S5IF-FLD10）。
  `input.upload` 是项目角色持有的动作，不带项目这项检查就答不了；控制台改为带着"我要往哪个项目上传"来问。
  不带时退回原有的组织级判定——这两个数字本身是全局设置，不该因为页面没说往哪儿传就显示不出来。
- 其余为**内部修正**，响应形状不变：Change Request 7 条、阶段对话 GET、Gate 提问回答、工作流
  pause/resume/cancel、agent-run 读取与取消、NRR 面板、环境绑定两条、Run 视图/摘要/drift/取消、
  执行读取/报告/reconcile、通知标记已读、COMMIT 追溯、产物下载与受限/PHI 读取，全部改为按各自的
  项目句柄（run/workflow/stage/gate/cr/question/notification/artifact）推导作用域。
- `gate.approve.DEPLOY_GATE` 一类"只有带环境类别才可被授予"的动作，粗粒度钩子改为**先解析类别再问**
  （CP-GATE-012：`gate.approve.DEPLOY_GATE.TEST` 不等于 `gate.approve.DEPLOY_GATE`，而更窄的授权永不
  覆盖更粗的请求，故原名任何人都持有不了）。
- `tests/unit/test_role_catalogue.py` 新增守卫：**项目角色持有的动作，路由不得无项目地问**；
  例外必须逐条写理由（22 条，全部是环境/凭据这类组织级对象与跨项目列表页），
  且"已修好却还留在清单里"同样失败，防止例外清单烂成借口清单。
  同文件的扫描器已认识 `authorize_stage/workflow/run/gate/execution`，改走助手不会漏检。

### 当时待裁决、现已于 1.14.0 落定

`GET /projects` 与 `GET /deployment-runs` 不按成员关系过滤（FR-SEC-004 的隔离在这两个页面上靠的是
"没人持有组织级非管理员角色"这个巧合）——已改为按成员关系过滤，见 1.14.0；
环境和凭据是组织级对象，而 `environment.*` / `secret.manage` 同时又是项目角色持有的名字——
裁决是维持现状并写明理由（同一个角色名可在组织级与项目级并存），见 1.14.0 的"变更（行为）"。

## 1.10.0 — 项目可以指定自己的 codePrefix（依据 BP-REG-009、FR-TST-002）

### 新增

- **`POST /api/v1/projects` 请求体接受 `codePrefix`**。
  S2 为每个受管对象派生 `code` 的形式是 `{prefix}_{TYPE}_{businessKey}`，并且**会替换作者自己写的 code**
  （BP-REG-009：身份由 S2 分配，不接受外部断言）。而 S1 的 `prepare_initial_blueprint` 从来不透传这个前缀，
  于是所有项目的策略一律落在 S2 的默认值 `GEN` 上。
  后果在共享环境上才成立：往官方演示实例提交时，落盘的 code 是 `GEN_DE_DATAELEMENT_...`，
  既不指向创建它的那一次运行，也无法按运行前缀找回并删除——而 FR-TST-002 要求的正是"按运行前后缀识别并清理"。
  `POST /stages/{id}/patches` 里写 code 不能绕开这一点（会被替换），补池与 UID 也都救不了：前缀只能在项目建立时定。
- 校验：`codePrefix` 非空时必须匹配 `^[A-Za-z][A-Za-z0-9_]{0,15}$`。前缀是被原样拼进 code 的，
  因此不允许出现路径分隔、点号或任何会进入目标实例的字符；留空即沿用 S2 默认 `GEN`。

## 1.9.0 — 真身后的两条死路：UID 池可以补了、提交出去的东西可以删了（依据 BP-REG-008a、CP-UI-021、DX-EXE-038、FR-TST-002）

后端从替身切到 S2/S3/S4 真身之后才暴露的：契约里的端点在、`allowed-actions` 里的动作名也在，唯独没有一条走得通的路。

### 新增

- **`POST /api/v1/projects/{project_id}/uid-pool/replenish`**（S5IF-BP09）。
  `uid_pool.replenish` 从建动作表起就在 `allowed-actions` 里回答"可用"，S1 SRS 697 还点名要求这个名字存在——
  但**S1 从来没有任何地方调用它**，补池只发生在一次 Agent 阶段运行内部（`steps.py` 的 `_run_agent_stage`）。
  后果是手工创作这条路走不通：`POST /stages/{id}/patches` 提交第一条 `CREATE` registry 条目就撞
  `UID_POOL_EXHAUSTED`，而 `UI-MAN-003` 给操作者的答复是"S1 处理后重试，界面只显示等待"——指向一次会同样失败的
  重试。同时 `submit_manual_patch` 现在与 Agent 路径一致，先补池再提交。
  这是《前端页面成熟度审计报告》§12.1 留给 S1 的那笔账（"一个没有可依从写路径的动作名"）的反向守卫补全。
- **`POST /api/v1/environments/{environment_key}/delete-objects`**（DX-EXE-038）。
  S4 的 `delete_objects` 一直是实现好的，但不在 `ExecutionAdapter.METHOD_MAP` 里，所以 S1 一处都到不了它。
  这不是理论缺口：总体 SRS FR-TST-002 要求一次跑完**必须删除本次创建的元数据**，而官方在线演示实例是公开共享环境。
  此前唯一的清理手段是测试里用裸 httpx 发 DELETE、刻意绕开 S4（`S4/tests/contract/live_run.py`）。
  权限复用 `iam.ENVIRONMENT_MANAGE`（附录 B 的现行词汇，M4 只给平台管理员），**没有新造动作名**——
  新名字要同时改两个控制台的 ACTION 表与 `allowed-actions` 的回答集合，而那会让一个没人问得到的控件永远灰着。
  `Idempotency-Key` 为必填：不可逆的操作不能继承一个由 S1 用环境名+操作者编造的键，否则同一只手第二次不同的
  删除会被答复成第一次的结论（NFR-REL-003）。

### 修复

- **S4 的 import 范围判定读了一个真实例从来不发的字段**。`_check_import_scope` 走
  `GET /api/{type}/{uid}/sharing` 并取顶层 `manage`，而 2.41 那个子资源回的是
  `owner` / `public` / `users` / `userGroups`——**里面没有 manage**。于是非超级用户账户在任何真实实例上都被判
  "不可导入"，连接测试恒失败，而 Deployment Run 的前置条件正是连接测试通过。证据改取对象自己的
  `access.manage`（`GET /api/{type}/{uid}?fields=id,access`，实例对"当前用户能不能管这个对象"的直接答复，仍只读）。
  S4 的内存实例同步改成与真实例同形：它过去回一个顶层 `manage` 布尔，正是这条缺陷能在替身下跑绿的原因。
- **实例上没有的表达式校验端点被算成 BLOCKING**。官方 2.41 演示实例根本没有
  `programIndicators/expression/validate` 这条路由，请求被按 `programIndicators/{id}`（id="expression"）匹配后回
  404 `E1005`，而 S4 把任何 `Dhis2Error` 都折成阻断问题——于是每一份带 programRule / programIndicator /
  validationRule 表达式的包都过不了 VALIDATE，原因与表达式无关。404 归到 `skipped`（与"该版本没有对应校验接口"
  同一个事实，只是晚一个请求才知道）；账户读不到（`Dhis2PermissionError`）仍然阻塞，那是安全边界。
  表达式的**判定结论**为否时依旧 BLOCKING 未动：FR-CMP-011 把"失败只报告"挂在服务端校验上，但那是实例对
  表达式的真实答复，不是"没校验成"，收紧还是放宽这一条属于产品取舍，本轮不替它决定。

## 1.8.0 — 延后条款落到后端：回归记录可读、上传内容会被扫、审批人数真的算数（依据 FR-SEC-001～005、FR-REQ-004、AI-REG-003、CP-GATE-012）

SRS 本轮把 M3/M4 的延后条款全部转为本期交付，后端跟着动的是这四处。

### 新增

- **`GET /api/v1/governance/regression-records`**（S5IF-GOV11，可按 `promptName` / `promptVersion` 过滤）。
  此前只有写侧：`activate_prompt` 会拒绝"没有回归记录的 Prompt 版本"（AI-REG-003），但**没有任何读侧**——
  于是两个控制台界面上那句"缺评测或回归记录时不可切换"根本无从判断，只能让人点了才知道。
  规则要能被看见，才不是装饰。配套改了 S3：`list_regressions`（`prompts/activation.py` 读表、
  `governance/capabilities.py` 出视图、`api.py` 导出、fake 同步）。
- **`GET /api/v1/questions`**（CP-Q-002 的跨项目列表，按 `status` / `projectId` / `workflowId` 过滤，
  offset 分页 + `total`）。在此之前只能一个工作流一个请求，移动端"待办"的第四类（待确认问题）
  就是这么被省掉的——不是取舍，是没有端点可问。
- **CP-API-009 的内容扫描**（`security/content_scan.py`）：可执行/脚本魔数、归档成员路径穿越、
  归档符号链接条目、条目数上限、解压比上限。**说清楚它是什么也说清楚它不是什么**：这是结构性的静态检查，
  不是病毒特征库；扫描命中仍回 422 `UPLOAD_REJECTED`，`details.violation` 给出具体规则码。

### 修复

- **DEPLOY_GATE 的审批人数以前是写死的 1**（`services/steps.py` 创建 Gate 时 `required_approvals=1`）。
  环境上配置的 `requiredApprovals` 早就在 `GET /environments` 的响应里被读给界面看，**却没人拿它建 Gate**——
  于是 PRODUCTION 的多人审批在界面上是一个数字、在后台是一个承诺。现在建 Gate 时按环境取值，
  读不到保护设置的环境退回 1（并在 Gate 行里留下实际用过的数字，事后可审计）。
  `services/gates.py` 的多人数判定与职责分离（CP-GATE-007/012、AC-GATE-04）本来就在，缺的是这一个入口。
- **`m4_features_are_live()` 的注释与文档还在说"M4 再说"**：`authMode != off` 时这些写操作就是活的，
  本机 `--auth local` 部署早已如此。文档跟着代码改，不是反过来。

### 新增（测试与对账，不改契约：`docs/openapi.json` 逐字节未变）

- **`STAGE_ACTIONS` 补上 `deployment_run.drift_check`**（不改行为：这个元组除了被守卫测试当作
  "能被列出的动作"清单读取之外没有任何消费者）。`run_actions()` 早就按 run 发这个名字
  （`actions.py:495/498`，运行关闭后回 `NO_OPEN_RUN`），清单里却没有它——于是移动端把「重新检查漂移」
  按 UI-BR-01 接上门控之后，`test_action_vocabulary.py` 反而报"控制台在发明一个服务器不会列出的动作"。
  守卫的权威是"能构建答案的那个模块"，而按形状扫描集合看不见写在构造函数里的字面量；补进清单是把
  权威说全，不是把守卫放宽。同样在 `run_actions()` 里发的 `execution.commit` 与 `deployment.cancel`
  本来就在清单上，所以缺的只有这一条。

- **`tests/unit/test_console_error_catalogue.py`**：把两个控制台的四份错误码目录（S5 的
  `src/i18n/messages.ts` 两种语言、S5M 的 `src/i18n/locales/{en,zh-CN}.ts`）与 `factory_control.errors`
  实际能发出的码对账。写这条之前谁都没查过——量出来 **S1 能回答的 72 个码里，桌面缺 24 个、移动端缺 23 个**，
  包括 `CSRF_FAILED`、`SECRET_IN_USE`、`DHIS2_TASK_FAILED/LOST`、全部 `DX_*` 提交类拒绝与 `AI_PARSER_*`。
  缺那一格的后果不是"少一句翻译"那么轻：前端会退回抛出方的原文，于是中文界面上出现一句写给日志看的英文，
  而那一屏的职责正是告诉操作员下一步该做什么。补齐由两端各自完成（文案取自本服务的目录，不另编一套），
  这条测试负责它不再漂。
- 同一轮两端把开发包动作 `frontend_package.download` 接进了门控与替身；
  `tests/unit/test_action_vocabulary.py` 按源码读两张 ACTION 表与两份 mock，因此新动作名一并被对账（6 passed）。

### 兼容性

- 两处新增端点、零字段删除、零既有任何端点的形状变化；`allowed-actions` 的动作名集合未变（守卫测试
  `tests/unit/test_action_vocabulary.py` 仍过）。三份 vendored 契约文档（S5 / S5M）与生成的类型同批重导。
- 新增测试文件 `tests/api/test_scan_questions_and_approvals.py`（6 条）：扫描的三类拒绝与一份真 xlsx 通过、
  跨项目问题列表的分页与过滤、回归记录写完读得回、审批人数取值与退回路径。

## 1.7.2 — `SessionView` 关于 CSRF 的说明改对了（依据 FR-SEC-005；无字段变化）

- 契约里 `SessionView` 的注释原文是"`csrfToken` 在这里且只在这里返回"，而 **`GET /auth/session` 并不返回它**：
  该端点用 `response_model_exclude_none` 把未填的字段一并抹掉，刷新后页面要用的值来自可读的
  `factory_csrf` cookie（`config.py` 里 `csrf_cookie_name` 的注释才是这件事的权威说法），两个控制台的客户端
  也正是这么读的（S5 `stores/csrf.ts:39`、S5M `api/http.ts:readCookie`）。
  一句过时的话会经契约被两个控制台各自生成的类型注释引用，所以它进了契约文档：
  **只有描述文字与 `info.version` 变化，140 条路径、请求与响应结构一字未动**（新旧文档逐叶子对比只剩两个不同）。
- 活体复核（本机 `127.0.0.1:8090`）：`GET /auth/session` 返回的键是
  `authenticated,userId,subject,displayName,roles,authMode,expiresAt,mustChangePassword` ———— 没有 `csrfToken`，
  与本条说明一致。
- **`PUT /users/{id}/roles` 带着一个不存在的项目 id 原来回答 500**（活体探测撞上的）：项目级角色隐含成员身份，
  而成员身份那一行后面有外键，于是错拼的项目名走进 `IntegrityError`、变成一条"内部错误 + 关联 ID"，
  操作者看不出自己做错了什么。现在先查项目是否存在，不存在就 404 并点名 `projectId`
  （`details.fields[0].path`），角色也不会半写进去。**契约不需要改**：`404` 本来就挂在每个操作上（CP-API-008）。
  新增用例 `test_a_role_pointed_at_a_project_that_is_not_there_is_answered_as_a_missing_project`
  同时钉住反面——同一个调用换一个真实存在的项目 id 仍然成功。
- **`UserView.roles` 里为什么没有 `revoked`**（只加了一段注释，行为不变）：`revoke_role` 是
  `session.delete(row)`，不是打标记，所以这个列表里每一行都是活的授权。契约的
  `RoleAssignmentView.revoked` 描述的是"这一次分配/收回之后成不成"，属于那条调用的响应；
  在列表里给每行补一个 `revoked: false` 会让人以为"也可能是 true"，而没有任何地方能验证。
  两个控制台的界面因此都按"列表变短"来回答收回，这是对的。

### 实现补正（不改契约，`docs/openapi.json` 逐字节未变）

- **登录失败的计数被它自己报告的失败回滚掉了 —— 5 次锁 15 分钟此前是一条不生效的规则**。
  `identity/authn.py` 原先在**认证所在的那个事务里**给 `cp.users.failed_attempts` 加一，然后抛出
  `AuthenticationFailed`；而 `factory_foundation/db/uow.py:89` 见到异常就 `rollback`，于是这一次尝试
  连同计数一起被抹掉。症状是"看起来在限速"：每次回答都是 401，审计里也有失败记录，但
  `locked_until` 永远不会被置上，口令可以无限次地猜下去 —— FR-SEC-005 要的正是这件事不发生。
  现在计数走自己的一条事务并显式提交（`_register_failure`），失败的回答不变，失败的事实留下。
  这条是新增的 `tests/api/test_identity_lockout.py` 第一次把锁定路径跑出来时发现的：在那之前
  `MAX_FAILED_ATTEMPTS` / `LOCK_MINUTES` / `locked_until` 在整个测试集里没有任何一处被引用。
- **停用账号这条路现在也有网络层面的测试**：`PATCH /users/{id} {status: DISABLED}` 之后，下一次登录
  必须 401，而停用之前已经发出去的会话必须不再被接受——只挡新登录不挡旧会话的话，离职的人手里还留着
  一整个空闲窗口。写这条时踩到一个值得记下来的边界：**带着别人仍然有效的会话 Cookie 去 `POST /auth/login`
  得到的是 403 而不是 401**——中间件把"有可解析会话的写请求"一律按 CSRF 双提交要求处理，这本身是对的
  （界面的登录请求要么没有会话，要么带着从 `factory_csrf` 取到的值），但"这个账号还能不能登录"必须
  从一个干净的 cookie jar 去问，否则量到的是 CSRF 层而不是认证层。
- **登录名不可重复这条路现在也有测试**：同一个 `subject` 再建一次回 422 且 `details.fields[0].path = subject`，
  并且不写出第二行。检查跨来源、跨状态——停用的账号仍然占着它的名字，否则"重建同名账号"会把旧授权
  交给最后来要的人（`service.create_user` 用 `get_by_subject_any_status`）。
- **被锁的账号在登录接口上仍然与"口令错"逐字相同**（除每次请求自带的 `correlationId`）。这不是遗漏而是
  选择：`AuthenticationFailed` 的文档写着不区分"没有这个账号"与"口令错"（总体 SRS 21.4），
  而"被锁了"若从登录回答里说出来，就是一条新的账号枚举通道（猜五次真账号会锁、猜五次假账号不会）。
  操作者要知道谁被锁，去 `GET /users` 的 `locked` 看——那一列本来就在契约里，两个控制台现在都画它。
- **口令长度成为服务层的规则**（`identity/passwords.MIN_PASSWORD_CHARS`）。此前 `minLength: 8` 只写在
  `PasswordSetRequest` 的 schema 上，而 `POST /users` 的 `password` 没有任何约束、`create_user` 只判断
  "有没有给"，于是**建账号时可以写一个 1 个字符的口令**；更要紧的是 bootstrap 管理员是在进程内直接调用
  `create_user` 的，schema 那条规则从来管不到它。现在 `create_user` 与 `set_password` 共用同一个检查，
  消息只点名字段（`details.fields[].path = password`）绝不引用值。
- **`password` 进入 422 的抹密名单**（`rest/app.py` 的 `OPAQUE_FIELDS` 原有 `material`）：
  `POST /users` 或 `POST /users/{id}/password` 把口令提交成错误类型时，pydantic 的摘要里带着原值，
  而那条摘要会进客户端日志、代理日志和求助时贴出去的一段话。
- **`mustChangePassword` 明确为"报告位"**，服务端不拦截：`PUT`/`POST` 一切端点照旧按权限回答。
  裁决理由记在这里以免又被"发现"一遍：硬拦的话，新装机器上第一个账号（bootstrap 建的就是
  `mustChangePassword=True`）连 `GET /setup` 之外什么都做不了，引导第一屏就是一片 403；
  界面改为把这件事说清楚并给出入口（见 S5 1.3.0、S5M 1.3.0），要收紧成服务端拦截是后续一个
  明确的版本决定，不是当前的疏漏。

## 1.7.1 — 本地推理服务可以不填密钥，`/secrets` 的 PUT 不再收 Idempotency-Key（依据 CP-SEC-001…003、S5IF-G09、AI-LLM-003）

本轮定为**修订号**：没有新增或删除任何字段，没有改任何路径；改的是两处声明、一处语义，以及一条上一轮宣布过但没落实的行为。

### 变更

- **`RegisterProviderRequest.apiKeySecretRef` 由必填改为可省（默认空串）**。目录里 `local` 那几条（Ollama、LM Studio、llama.cpp、本机 vLLM）默认不鉴权，而原先必须给它编一个引用名，于是操作者随手写进一个不存在的 `env://变量名`：登记通过、解析必失败，`/setup.credentials.unresolvable` 还要替这条假引用报一次警。现在 `endpoint` 仍然必填（那是寻址），`apiKeySecretRef` 为空就是说"这个地址不需要 `Authorization` 头"；只有非空时才按引用语法校验。
- **`PUT /api/v1/secrets/{name}` 不再声明 `Idempotency-Key`**，200 也不再声明 `X-Idempotent-Replay` / `X-Idempotency-Stored`，`x-requirements` 去掉 `CP-API-003`。这个路径本就整段排除在回放表之外（表里逐字存响应体，而这里的响应体正是那个引用），所以这个头只会"被接受、被记录、然后被忽略"。重复请求的安全性来自 `PUT` 这个动作本身（创建与轮换是同一句话），不来自一个键。契约里写着的声明客户端就会照着发——两个客户端的生成类型现在**拒绝**发它，桌面端那处旧代码正是这样被查出来的。
- **`/setup.credentials` 的两个字段各说各话**：`masterKeyConfigured` = 主密钥这一行存在且非空白；`storeAvailable` = 它长到能用（≥ `MIN_CREDENTIAL_MASTER_KEY_CHARS` = 32）。此前两者同值，于是一把 10 个字符的密钥被报成"未配置"，把操作者指到 env 文件里错误的那一行——设置其实在，只是太短，而后者才是 `validate_runtime()` 会说的那句话。
- **422 不回显被拒的值，这条 1.7.0 就宣布过的行为，到本轮才真的成立**：pydantic v2 把被拒的值放在每个错误的 `input` 键上，不是 `ctx.input`，所以原先只读 `ctx` 的那层取值对多数类型错误是空转，值照原样进了摘要。现在两层都读，并且 `_renderings()` 把 dict / list 里的每个值一并抹掉——把凭证当 `{"token": "sk-…"}` 这种错类型提交，原本会把里面那串字符串留在摘要里。回归测试 `tests/unit/test_422_secret_scrubbing.py`（7 条）先断言 pydantic 确实会把值渲染进 `str(exc)`，再断言这些值一个都不出现在响应里。
- **`PLATFORM_ACTIONS` 里那一条 `settings.update` 是错的，改成 `iam.SETTINGS_MANAGE`（`settings.manage`）**。
  `PUT /api/v1/settings` 用 `settings.manage` 授权，SRS 的动作词表（CP-UI-019）写的也是 `settings.manage`；
  `settings.update` 是从审计事件 `cp.settings.updated` 的后缀抄进词表的一个名字，S1 把它放进
  `allowed-actions` 的回答里，却没有任何端点按它检查过。界面按 UI-BR-01 只能读服务端给出的键，
  于是系统设置页的保存按钮在**有权限的人**那里也是灰的——这既是被撤掉的那条声明暴露的问题，也是本轮把它改回来的原因。
  动作名在响应体里是数据而不是 schema，所以 `docs/openapi.json` 逐字节未变。
- **`SecretView` 里没有任何密钥派生字段**（这条决定写进了 `_view()` 的注释）。审查中一度打算回一个 8 位 `keyFingerprint`（`sha256(派生密钥)` 的前缀）用来回答"这批凭证是不是换主密钥之前写的"。这个用法不成立：每次加密都取新 salt，派生密钥因此逐行不同，两行同键写入也不会相等——它标识的是一个 blob，不是一把密钥。既然答不了任何问题，它就只是一份白给离线试猜用的检查值，于是撤掉；这一列留在库里，`decrypt()` 用它区分 `keyMismatch` 与 `corrupt`。

### 新增（测试，不改契约）

- `tests/unit/test_action_vocabulary.py`：两个控制台的 `ACTION` 表里不许出现 `allowed-actions` 给不出的动作名，并且桌面端用到的名字移动端必须有同名条目。词表按形状从 `domain/actions.py` 的七个动作组收集——**只收这七个组，不收 `iam.py` 的常量**，因为 `iam` 是"请求按什么检查"，而这五个组是"服务端能回答什么"，本轮的 `settings.update` 正是把两者混为一谈才活下来的：一个只存在于 `iam` 的名字，界面按它 gating 就永远灰。两个控制台的**替身数据**（`S5/src/mocks/db.ts`、`S5M/scripts/mock-s1.mjs`）在同一条测试里按同样的规则检查——替身发明一个名字，所有前端测试照样绿，而它在教下一个作者那是真的。
- `tests/api/test_openapi_contract.py::test_the_published_and_vendored_documents_are_all_the_current_one`：`docs/openapi.json` 与 `S5`、`S5M` 的 `contract/openapi.json` 三份都必须等于进程当前生成的文档。重新导出是一条没人记得跑的命令，而漂移的表现是"生成的类型里有个服务端从不发的字段"。

## 1.7.0 — 凭证可以在界面里录了，而且是加密落库的（依据 S5IF-SEC01…03、S5IF-GOV07、CP-SEC-001…003）

本轮定为**次版本**：三条新端点、一个目录端点、`/setup` 多一个 `credentials` 段；既有字段一个都没删、没改名。

### 新增

- **`PUT /api/v1/secrets/{name}`（S5IF-SEC01）**。此前配好一台环境或一个模型提供方，必须先在服务器上
  把令牌写进 `.deploy/env`，再回界面敲 `env://变量名` —— 那是"从配置到运行"这条路上唯一要求人离开
  浏览器的一步。现在值只在录入的那一瞬间经 HTTPS 进入服务端，落 `cp.secrets` 时已经是
  AES-256-GCM 密文（HKDF-SHA256 从 `FACTORY_CREDENTIAL_MASTER_KEY` 派生密钥，每行独立 salt，
  AAD 绑定凭证名），响应只回 `{name, ref, algo, createdAt, updatedAt, rotatedAt, createdBy}`。
  用 `PUT` 而不是 `POST`：创建与轮换是同一句话（"现在这就是它的值"），所以一次丢了响应的重试是无害的。
- **`GET /api/v1/secrets`（S5IF-SEC02）** 只列名字与时间；**没有 `GET /secrets/{name}`** ——
  那会是一个 403/404 枚举探针，而列表端点在同一个权限下给出同样多的信息。
  **`DELETE /api/v1/secrets/{name}`（S5IF-SEC03）** 在还有环境或提供方引用它时回 409
  `SECRET_IN_USE` + `details.referencedBy`：删掉一个正在被引用的凭证不是清理，是把一次正常的
  DHIS2 调用变成将来某刻的 `SECRET_UNAVAILABLE`。
- **`db://` 成为被接受的引用格式**（`REFERENCE_SCHEMES`），排在最前；S4 的 `CredentialRef` 本就放行
  任意 `://`，故 S4 零改动。同时把 `vault://`/`secret://`/`kms://` 明确标注为**兼容语法**：
  这个进程里真实装着的后端只有 `env`、`memory` 与本轮的 `db`，界面不该给操作者一个解析不到的地址。
- **`GET /api/v1/governance/provider-catalogue`（S5IF-GOV07、AI-CAT-001）**：内置常用厂商的
  展示名、默认地址、模型清单与建议的引用名。目录**只列 OpenAI 兼容的**（运行时只有一个
  `OpenAICompatProvider`：地址原样使用、追加 `/chat/completions`、`Bearer` 头），
  因此 `api.anthropic.com` 与 Gemini 原生地址不在其中 —— 列进去等于给用户一个存得进去、
  第一次调用必失败的地址。目录条目不代表已验证连通，也没有任何"可达"字段可让界面画成绿灯。
- **`/setup` 的 `credentials` 段**：`storeAvailable`、`masterKeyConfigured`、`named`、
  `unresolvable[{ref, holders, reason}]`。最后那一项是这一整轮里最实用的一行：一个仍然被引用、
  但已经解不开的凭证（删过、备份回滚过、主密钥换过）在别处全都显示"已配置"，只有真正跑阶段时
  才炸；现在引导页能提前说出来。无 `secret.manage` 的调用方拿到 `null` 而不是 0。
- 平台动作 **`secret.manage`** 进入 `PLATFORM_ACTIONS` 与 `allowed-actions`，授予
  PLATFORM_ADMINISTRATOR 与 DHIS2_IMPLEMENTER —— 即"能把某个东西指向凭证的那两个角色"。
- `RegisterModelRequest` 补上 `endpoint` 与 `apiKeySecretRef`：S3 的 `ModelRow` 两列早就有
  （AI-LLM-003 的按模型覆盖），缺的只是 S1 的请求体，于是同一提供方下不同地址/不同密钥的模型
  在界面里根本表达不出来。

### 变更

- **422 不再回显被拒的值**。`str(RequestValidationError)` 会把每个错误的 `ctx.input` 写进
  `input_value=…`，而 S1 的 422 信封原先就把它当 `message` 发出去 —— 对一个普通字段这是最有用的文本，
  对一个凭证字段则是把密钥抄进客户端日志、代理日志和操作者贴出去求助的那段话里。
  现在 `material`（`OPAQUE_FIELDS`）的输入值在渲染前被抹掉，逐字段的 `details.fields[]` 保持原样，
  所以表单照样说得出哪里不对。
- **`/secrets` 被排除在幂等响应回放之外**：`_record_response` 会把响应体逐字存进
  `cp.idempotent_responses`（保留期是个配置值），而凭证写入的响应正是那个引用本身。
  PUT 本身可重复，不需要回放表再存一份。
- `identity/passwords.py` 的"本部署不引入加密依赖"这句限定为**口令路径**：口令仍然只用
  `hashlib.scrypt`；`cryptography` 只为"必须能读回来"的凭证存储而加。
- **替身环境档案的 `secretRefs` 改为与凭据行同源**（`testing/fakes.py`）。这一条是 2026-10-04 在
  fakes 部署上**量出来的**：`PATCH /environments/{key}` 换了凭据之后，视图里的 `secretRefs` 仍是旧值，
  于是删除守卫继续按那个已经没人解析的引用回 409 `SECRET_IN_USE`，操作者看到的现象是"引用改了，凭证却永远删不掉"。
  真实 S4 的两个字段在读的时候由同一份凭据列表派生（`factory_dhis2/environment/service.py:213`），
  不可能不一致；替身若不跟着做，就是在教一条只在替身上走得通的流程。
  回归测试 `test_moving_the_references_out_is_what_frees_the_credential` 走的就是量出来的那串请求。

### 兼容性

- 全是加法：`cp.secrets` 是新表（Alembic `0003_cp_secrets`，无 DEFAULT、无回填），
  未设 `FACTORY_CREDENTIAL_MASTER_KEY` 的部署行为与 1.6.0 逐字节相同 —— 不注册 `db` 后端、
  三条端点回 503、`/setup.credentials.masterKeyConfigured` 为 `false`，其余一切照旧。
  **启动不因此被阻断**：把主密钥缺失变成起不来，会让一台本来能跑的生产机因为一个可选功能而砖掉。
- `DbSecretBackend.supports_write()` 是 `False`，这一点有测试守着：`SecretResolver.store` 在调用方
  不写 scheme 时推断"唯一可写后端"，多于一个就 `SecretStoreUnsupportedError` —— 而 FD-SEC-004 的
  一次性演示凭证正依赖那个推断（`store_secret()` 的既有调用点遍布 S1/S4 的测试）。
- 保护范围说清楚：主密钥与密文在同一台机器上（`.deploy/env` 是 0600，`DATABASE_URL` 也在里面），
  所以这一层加密防的是**业务库** —— 备份、副本、`pg_dump`、能 `SELECT` cp 的人；
  不防已经拿下这台主机的人。需要后者请继续用 `env://` 配真的密钥服务，本条不假装做不到的事。

### 门禁

`tests/unit/test_credential_crypto.py`：往返与随机 salt/nonce、AAD 绑名（换名解不开）、
换主密钥报 `keyMismatch` 而不是"损坏"、密文被改只说"解不开"、畸形 blob 是答案不是崩溃、
空值/空主密钥直接拒。
`tests/api/test_secrets_endpoint.py`（21 项）：无主密钥时 503 且点名变量；写入只回引用；
解析回来的值与存进去的一致且 `repr` 仍掩码；轮换不留第二行；非法名被拒；
**422 不回显值**（把凭证当对象发过去，整个响应体不含该串）；审计行不含值；
被引用时 409 + `referencedBy`；`/setup` 在换密钥后点名那个引用；
`store_secret()` 的推断未被第二个后端破坏；`secret.manage` 出现在平台清单；
目录每条都是 `openai_compat` 且地址以 `/v1`/`/v4` 结尾、不含 Anthropic/Gemini 原生域名、
且没有任何"可达"字段。

整套门禁实测：`pytest` **524 passed / 1 skipped**（那一条 skip 是 AC-GATE-04 在
`test_identity_m4.py` 里已经证过，`test_ac_m2.py` 不重复计），`mypy`（`packages = ["factory_control"]`，
strict + pydantic 插件）130 个文件无问题，`ruff check src tests scripts` 干净。
迁移 `0003_cp_secrets` 在测试库上随 `FACTORY_MIGRATIONS_MODE=apply` 实际建出 `cp.secrets` ——
上面那些写入测试就是往那张表里写的，不是靠读代码推断出来的；活体部署之后 `psql` 也确认了
`damf` 库的 `cp_alembic_version` 从 `0002_cp_identity` 走到了 `0003_cp_secrets`。

补测两条（都是写文档时对着实现找出来的空档）：`/setup` 的 `credentials` 段在调用方没有
`secret.manage` 时必须回 `null` 而不是 `named: 0`（此前只有 `intelligence` 那条有测试守着）；
`test_ac03b_*` 把 AC-03 的"值不外泄"承诺铺到三条凭证端点上 —— 写入响应、清单、`/setup`、审计行
与一个 422 的响应体里都搜不到那个哨兵串，并且**用同一个 `Idempotency-Key` 重发要产生一次新的写入**
（`rotatedAt` 出现值），这才是"这张表没存过这个响应"的行为学证据。



本轮定为**次版本**：新增一个只读端点与一个动作名，既有字段一个都没删、没改名。

### 新增

- **`GET /api/v1/setup`（S5IF-SET01）**。全新装机在界面上无法诊断，是三件事叠在一起：
  端口是真实现还是替身只在 `/healthz` 里（不带 `/api/v1` 前缀，nginx 不代理）；
  `Settings.validate_runtime()` 早已产出人读的错配清单，却只在启动 fail-fast 时读一次；
  「登记了几台环境/几个模型/有没有项目」要 S5 自己发四个请求再数行。这个端点把这些**事实**一次回齐：
  `providers`、`startup`、`configuration.problems`、`identity`、`environments`（含 `neverTested`，
  即 S4 记录里从没做过连接测试的那批 —— 校验阶段会拒绝它们）、`intelligence`、`projects`。
  它**只回事实不回判断**：哪一颗按钮能点仍然只来自 `allowed-actions`（CP-UI-019、FR-UI-009），
  「下一步做什么」属呈现，留在界面侧（总体 SRS P-06）。
  权限不足的小节回 `null` 而不是 0 —— 0 会被读成「什么都没登记」，而真实原因是「你不能看」。
- **平台作用域的 `allowed-actions` 回答 `project.create`**（S5IF-G10 早已要求，此前漏做）。
  `POST /projects` 的守卫用的就是这个名字，`services/project.py` 的注释也称它是平台动作，
  但它不在 `PLATFORM_ACTIONS` 里，于是界面唯一的诚实选择是"发一次看看会不会 403"。
  现在无该角色的调用方在同一次读取里收到 `reasonCode: PERMISSION_DENIED`。

### 变更

- **`X-Operator` 现在接受 UTF-8 百分号编码，并按 UTF-8 解码**（CP-API-002，`rest/middleware.py:decode_operator`）。
  Fetch 标准把 header 值限制在 ISO-8859-1，因此浏览器**根本发不出**一个中文操作人名 —— Chromium 直接抛
  `TypeError`，操作者看到的是一句"无法连接服务器"。现在两端把超出 Latin-1 的姓名编码后再发，
  S1 在门口解码：`%E5%BC%A0%E4%B8%89` 落审计是 `张三`，而裸 `%` 与不完整的转义序列按字面保留，
  既有的 ASCII 调用方（含 `curl -H 'X-Operator: ada'`）一字不变。
  另外补上 curl 那一侧：`curl -H 'X-Operator: 张三'` 的字节能发出去，但传输层按 ISO-8859-1 读它，
  到的是 `å¼ ä¸‰` —— 一个没人打过的名字，会永远留在审计里。S1 把这些字节按 UTF-8 重读一次，
  只有能干净还原时才还原，因此真正的 Latin-1 姓名（单个 `0xE9` 字节）不会被改写成别的名字。
  契约里 `XOperator` 的 `description` 同步写明这条规则。

### 兼容性

- 新增端点与新增动作名都是**加法**：既有客户端读不到它们也照常工作；
  界面侧把「`allowed-actions` 里没有 `project.create`」继续按不可用处理（UM-BR-01），
  因此对 1.5.0 的 S1 也不会误开按钮。
- `GET /setup` 的读权与平台 `allowed-actions` 一致（`project.view`），
  一次回答披露的信息不多于 `GET /environments` + `GET /projects` 已各自披露的。

### 门禁

`tests/api/test_setup_endpoint.py`：零登记的装机回 200 且各项为 0/空（不是 404、不是 500）；
登记但未测试的环境出现在 `neverTested`；停用环境不计入 `inService` 也不带 `lastTestedAt`；
无 `governance.manage` 时 `intelligence` 是 `null`；`validate_runtime` 的句子只写变量名、不写值；
缺 `project.view` 时 403 `PERMISSION_DENIED`；`project.create` 出现在平台清单且被拒时带原因码。
`tests/api/test_operator_header.py`：解码表（中文、带重音、裸 `%`、不完整转义、空值）；
一个以 `张三` 命名的 `POST /projects` 走完整 HTTP 后，审计行上的名字是 `张三` 而不是转义串。

## 1.5.0 — 策略上限可读回、提供方的拒绝不再是 500、停用环境的答案诚实了（依据 S5IF-G02/G08、S5IF-ENV05、FR-UI-009）

本轮定为**次版本**：三处都是响应里**新增**键或**换掉**一个本就无人能解释的状态码，没有删除或改名任何既有字段。

### 新增

- **`GET /governance/policy` 带出 `ceiling`**。生效矩阵与允许的上限成对出现在同一次读取里，
  界面才可能只给操作员看「还能放宽到哪」而不是让他猜。上限由 S3 导出（`POLICY_MATRIX`），
  S1 的端口镜像与 REST 模型同步加这一个键 —— 一个读、一次往返，两份矩阵不可能不同步。
  项目级的 `get_effective_policy`（D-07 的收窄）不带 `ceiling`：组织上限不是项目可以商量的东西。
- **`POST /governance/policy/relaxations` 的回答补上 `reason`、`change`、`createdAt`**，操作员能在
  响应里读回自己刚批了什么；此前这些字段被 S1 的投影悄悄丢掉，批准记录只能从审计里找。
  `change` 也不再是自由字典：`RelaxationChangeView` 要求 `level` + 至少一个类别，未知键直接拒。
  S3 的别名（`classification`/`allowedClasses`/`classes`）在门口归一，不再为不同的调用方造第二种形状。
- **`GET /users` 的每一行带 `roles`**（该账号实际持有的授予）。撤销角色需要说出撤的是哪一条，
  而角色清单（`GET /roles`）回答的是「系统里有哪些角色」，两者不是一回事。

### 变更

- **一个提供方的拒绝不再表现为 S1 自己的崩溃。** 此前 DHIS2 / 模型 / 编译器抛出的 25 个错误码
  既不在 S1 的状态表里也不在别名表里，`http_status_for` 一律落到 500 —— 而 500 不点名任何字段，
  操作员只能重试或放弃。现在按语义给出状态：停用实例上的操作、重复停用、执行顺序不对 → 409；
  载荷哈希不符、验证已过期、表达式越界 → 422；实例连不上、任务丢失、模型回了不可解析的答复 → 502；
  缺解析器依赖、缺表达式文法 → 503。新增的守护测试 `test_every_code_a_provider_registers_has_a_status`
  会遍历各提供方注册的错误码，任何一个落到默认 500 就失败（`INTERNAL_ERROR` 与 S3 的基类 `AI_ERROR` 除外，
  它们本来就是「未分类」）。
- **停用环境在 `allowed-actions` 里是真的不可用**（`GET /allowed-actions`、`/workflows/{id}/allowed-actions`）。
  S4 在探测能力档案之前就拒针对停用实例的操作，所以「测试连接」「能力探测」「停用」三条一起翻成
  `available: false`，`reasonCode = ENVIRONMENT_DISABLED`，`details.status = "DISABLED"`；
  而**编辑档案与紧急锁定保持可用** —— 否则一台停用的环境再也没人扶得起来。同一份规则同时喂两个视图，
  平台清单与项目清单不会各说各话。

- **两个"界面说的和接口做的不一样"补上了。**
  `POST /workflows/{id}/pause|resume` 现在在已取消的工作流上回 409 `INVALID_STATE`
  （`details.reasonCode = WORKFLOW_CANCELLED`）—— 动作清单一直拒它，写操作却照做，于是一个已经结束的
  Run 会挂上一个谁也解不开的暂停标记。
  `POST /environments/{key}/{disable,test-connection,probe-capabilities}` 在环境已停用（`status = DISABLED`
  或 `enabled = false`）时同样回 409，`details.status = "DISABLED"`：S4 本来就是这么拒的（状态检查先于
  `allow_unprobed`，`rules.can_disable` 拒绝二次停用），但 `fakes` 全部接受 —— 在替身部署上量到的是 200，
  换真实例就变成 409，而两者都跟界面自己给出的 `ENVIRONMENT_DISABLED` 矛盾。
  档案编辑与紧急锁定**仍然接受**，那正是把环境扶起来的唯一出路。

- **`project.export` 有人被授予了**：`PLATFORM_ADMINISTRATOR` 此前拿到 `project.archive` 与
  `project.delete`，却没有 `project.export` —— 而 `CP-PRJ-007` 规定**删除之前必须先有 PROJECT_EXPORT
  产物**。于是这条链谁也走不通：删除回 409（`details.requires = "PROJECT_EXPORT"`），导出对系统里
  每一个身份回 403（`DENIED_NO_ROLE`），两个界面上的「导出」按钮对已登录的账号永远是灰的。
  `auth=off` 下看不出来（策略一律放行），新增的 `tests/unit/test_role_catalogue.py` 改为用这条规则
  守住：路由 `authorize` 的每个动作至少要有一个内置角色持有，且被授予删除的角色必须同时能导出。

### 兼容性

`ceiling` 与 `roles` 是新增键；`RelaxationView` 的三个补键同理。`change` 由自由字典收紧为具名模型，
影响的是**请求**中本就非法的那部分（多余的键以前会被静默丢弃，现在会 422）。状态码从 500 变成 409/422/502/503
对只判 `>= 500` 的旧客户端是可见的，但那些调用方原本就得到一个无法解释的失败。

## 1.4.0 — 提供方可就地编辑、枚举取值写进契约（依据 AI-GOV-001/004、S5IF-GOV02/03、S5IF-ENV01~02；S3 SRS 7、S4 SRS 5）

本轮定为**次版本**：`PATCH /governance/providers/{name}` 的请求体加宽、响应从 `{ref, enabled}` 换成
完整的 `ProviderView`（`enabled` 是其子集，旧客户端读的键仍在），新增字段全部可选，既有调用方无需改动。

### 新增

- **就地编辑提供方**（S5IF-GOV03）：`PATCH /governance/providers/{name}` 现在接受
  `providerClass`/`endpoint`/`apiKeySecretRef`/`adapter`/`enabled` 的任意子集，**未给出的字段保持原值**。
  此前这个操作只能改 `enabled`，改地址必须重新 `POST` 一条完整登记；而 S3 的登记是整行覆盖，
  于是「只想修一个拼错的地址」会把密钥引用一并清成空值。S3 因此新增公开函数
  `update_provider(name, patch, operator)`（AI-GOV-001 的「一个能力一个函数」），S1 的端口与适配器同步映射。
  响应改为 `ProviderView`，操作员在一次编辑后能立刻看到落地的地址与引用，而不必再查一次列表。
- **契约里写出枚举取值**：`RegisterProviderRequest.providerClass`、
  `CreateEnvironmentRequest.environmentClass`/`authMode`/`llmDataPolicy`、`CredentialSpec.scope`
  在 `docs/openapi.json` 中带 `enum` 数组。裁决规则没有变——仍是服务端 `model_validator` 拒绝非法值，
  返回 `VALIDATION_ERROR` 并在 `details.allowed` 里列出全部合法取值；`enum` 只让客户端在生成类型阶段
  拿到字面量联合，不必再手抄一份常量表。可选字段（`UpdateEnvironmentRequest`、`PatchProviderRequest`）
  不带 `enum`：它们的 JSON Schema 是 `anyOf`，把 `enum` 并排放进去对生成器没有意义。

### 变更

- `PATCH /governance/providers/{name}` 的 `operationId` 由 `enable_provider_…` 变为 `update_provider_…`
  （处理函数改名，路径、方法与鉴权 `governance.provider.enable` 都不变）。
- **`protection` 从自由字典变成有名字的请求模型**（`CommitWindowSpec` / `ProtectionSpec`，
  `extra="forbid"`）：`POST /environments` 与 `PATCH /environments/{key}` 都接受它，键名拼错、
  星期越界、时刻写成 `2am` 都从「S4 里抛出的 pydantic 错误 → 一个不点名任何字段的 500」变成
  422 + `details.fields`。`PATCH` 能带它，是因为 S4 的 `EnvironmentPatch` 本来就有这一列
  （`ensure_protection_row(allow_existing=True)`），此前 S1 只是没有把它写进契约。
- **S4 会在保存时复查的规则，S1 现在在门口就拒**（同一份 body 到了 S4 会变成 500，因为
  `EnvironmentInvalidError` 不是 `FactoryError`，S1 的异常处理接不住它）：
  `environmentKey` 含空白、`baseUrl` 不是 http(s)、`timeoutSeconds < 1`、缺 `versionConstraint`
  （`DX-ENV-001`）、缺 `discovery`/`import` 之一、同一 scope 出现两行、`PRODUCTION` 没有时间窗
  （`DX-ENV-002`）、`protection.emergencyLock: true`（`DX-ENV-007`）。
  环境名清空也在这里拒 —— S4 会对合并后的档案重跑校验。
- **`environmentClass` 在 `PATCH /environments/{key}` 上按名字拒绝**（`DX-ENV-001`）：S4 的
  `EnvironmentPatch` 没有这个字段且 `extra="forbid"`，转发过去是一个不带字段名的 500。类别决定
  这个环境已满足哪些规则，所以它是登记时的选择，不是可改的列。

### 一致性

- 替身（`testing/fakes.py`）此前把 `protection` 整个丢掉：创建时不保存 `commitWindows`，
  `deletionPolicy` 不分类别一律取 `SAFE_ONLY`。于是 `fakes` 下能建出一个「没有时间窗的生产环境」，
  而真实 S4 会拒 —— 替身现在按 S3/S4 的语义合并 `protection`，并对空补丁、未知名字给出与真实
  后端同类的拒绝。

## 1.3.0 — 依赖图、开发包文件、"尚未生成"统一语义与 M4 门控（依据 `S1_Factory控制平面_SRS.md` V1.1.3 §3.12 CP-UI-020~023、§8.3、附录 B；S2 SRS V1.1.4、S0 SRS FD-COM-001a、00 文档 V1.2.3）

本轮按裁决定为**次版本**：`kind=NONE → 404` 属行为变更、object-graph 换路径与形状属破坏性变更，
随 SRS V1.1.3 一次性交付，S5/S5M 需按下文同步迁移。

### 新增

- **依赖图端点**（CP-UI-020，BP-TD-009 V1.1.3）：
  `GET /workflows/{id}/object-graph?runId=&blueprintVersion=&dhis2Type=&businessKey=`。
  三种命名构建方式：`runId` 取该 Run 环境绑定编译（BIND_COMPILE）的构建；`blueprintVersion`
  取该版本最近一次基线编译的构建；都省略取当前版本的；`runId` 与 `blueprintVersion` 同时给出
  返回 422。图经 S2 端口 `get_dependency_graph(build_id, filters)` 整体读取后按 camelCase 投影：
  节点只含 Registry 条目（无 `section`），**入度/出度、`orphans`、`unresolvedRegistryRefs` 由 S2
  在完整图上计算，筛选只裁剪返回内容**（BP-TD-009、BP-AC-16），S1 不存在自行计算图的代码路径
  （CP-AC-33）。S1 本方记录补齐：基线编译把 build id 与 `blueprintVersion` 记入 `StageRun`
  （与 BIND_COMPILE 同法），`blueprintVersion` 取名据此解析。
- **开发包文件端点**（CP-UI-021，M3，BP-HO-004 V1.1.3）：`GET /projects/{id}/frontend-package/files` 与
  `GET /projects/{id}/frontend-package/files/{path}?download=true`，转发 S2
  `list_package_files(artifact_id)` / `read_package_file(artifact_id, path)`（参数名按 S2 实现定为
  `artifact_id`；与执行链路指 METADATA_PACKAGE 的 `package_artifact_id` 是两个概念，S2 SRS 7.5 已加注）。
  S1 解析**项目当前 Blueprint 版本**对应的 FRONTEND_PACKAGE 产物：尚无、或清单答复的版本与当前版本
  不符 → 404 `NOT_GENERATED`（`requiredStage=HANDOFF`）；S2 的 `ARTIFACT_TYPE_MISMATCH` 是 S1 自身
  解析错误 → 记 WARNING 并映射 500 `INTERNAL_ERROR`（`details.providerErrorCode` 保留原码）；清单外
  路径与被 S2 拒绝的畸形路径（`..`、前导 `/`、反斜杠、`#`、NUL、空）一律按 `NOT_EXIST` 转发，
  二者不可区分、不泄露包内结构（BP-HO-004 第 4 项）。下载文件名按 CP-UI-016 规则三
  （`FRONTEND_PACKAGE-{产物ID}.{扩展名}`，扩展名取自请求路径），响应带 `X-Data-Classification`。
- **新错误码 `FEATURE_NOT_ENABLED`**（409，`details={feature, milestone}`）：登记入 S0 错误码目录并
  提供中英文消息（CP-API-013）；`NOT_FOUND` 的 `details` 定型为
  `{reason: NOT_GENERATED | NOT_EXIST, requiredStage?, runId?}`（§8.3 两新行）。
- **附录 B 动作**：`frontend_package.download`、`member.manage`、`restricted_grant.manage`、
  `production_exemption.manage`；后三项 M4 前恒不可用，原因码 `FEATURE_NOT_ENABLED`（CP-UI-023）。
- **S2 接线**：S2 已发布并导出三个产物读取（`factory_blueprint.api` 的
  `get_dependency_graph` / `list_package_files` / `read_package_file`，含 provider Fake，
  BP-TD-009、BP-HO-004）。S1 端口返回类型是 `dto/graph.py` / `dto/package_files.py` 的
  字段镜像并由兼容性测试逐字段比对（CP-RT-005 通过）；适配器保留显式方法仅因 `GraphFilters`
  需把 S1 镜像转成 S2 自己的模型。真实模式即走真实读取；fakes 模式沿用 CP-ASM-08 的可预置
  替身，其过滤与拒绝语义与 `narrow_graph`/BP-HO-004 对齐（无图为 NOT_GENERATED、缺包/缺文件为
  NOT_EXIST）。

### 行为变更（客户端必读）

- **"尚未生成"一律 404**（CP-UI-022，CP-AC-31）：`GET /projects/{id}/package-files` 未编译时不再返回
  200 + `kind="NONE"`，改为 404 `NOT_FOUND`，`details={reason:"NOT_GENERATED",
  requiredStage:"BASELINE_COMPILE"}`；开发包在未交付或与当前版本不符时 `requiredStage:"HANDOFF"`；
  依赖图在对应基线编译前 `requiredStage:"BASELINE_COMPILE"`、按 `runId` 取名而该 Run 尚未环境绑定
  编译时 `requiredStage:"BIND_COMPILE"`（CP-AC-31 验收）。客户端按 `details.reason` 区分"先完成某
  阶段"与"确实不存在"，不再有 200 空壳。
- **M4 三类写端点 MVP 返回 409**（CP-UI-023，CP-AC-32）：`PUT/DELETE /projects/{id}/members`、
  `POST/DELETE /projects/{id}/artifact-grants`、`POST/DELETE .../production-exemptions` 一律
  409 `FEATURE_NOT_ENABLED`，`allowed-actions` 中对应动作以同一原因码标为不可用；读取端点不变，
  且不参与任何判定（MVP 授权钩子恒为允许，CP-IAM-001）。`FACTORY_AUTH_MODE != off` 或
  `FACTORY_M4_FEATURES_ENABLED=true` 的部署视为已交付 M4，行为同 1.2.0。

### 破坏性变更（S5/S5M 需同步迁移）

- **依赖图路径与形状**（CP-UI-020/AC-33）：旧 `GET /projects/{id}/object-graph` 标记 deprecated，保留为
  薄转发别名（旧 `version` 参数映射到新端点的 `blueprintVersion`——"按版本取图"由此平滑过渡；
  `view/environmentKey/q/limit` 接受但忽略，`dhis2Type` 仍生效），响应换成 S2 图投影：节点无
  `code/decisionId/environmentBindings/observedHash/status`，新增 S2 计算的 `inDegree/outDegree`；旧
  S1 自算字段 `counts/truncated/matched/orphan` 消失，孤立与断点由 S2 的 `orphans/
  unresolvedRegistryRefs` 回答（完整图口径，筛选不重算）。**断点只是图上标注**，不得作为第二份
  问题列表与校验问题并列展示（S2 BP-VAL-030 为问题口径；S5M UM-CAP-002）。新调用走
  `GET /workflows/{id}/object-graph`。
- **动作名字符串改名**（附录 B）：`project.member.manage → member.manage`、
  `artifact.grant.manage → restricted_grant.manage`、`production.exemption.manage →
  production_exemption.manage`；S5 若按字面量门控按钮需同步改。

### 待同步

- §8.1 的 M4 行仍写 `POST /projects/{id}/members`、`/restricted-grants`、
  `DELETE /restricted-grants/{id}`，与现实现的 PUT members、`artifact-grants`、
  DELETE 按 `user_id+classification` 三处不一致；本轮经裁决保留现路径仅出门控，统一待 SRS
  修订或 M4 正式定义。
- **`ARTIFACT_TYPE_MISMATCH` 上下游已闭环**：S0 V1.0.1（FD-COM-001a）把该码登记为内置
  （所有者 S0、不可重试），S2 在 `artifact_reads` 实际抛出并由 provider Fake 支持；S1 侧
  映射（→ 500 `INTERNAL_ERROR` + `details.providerErrorCode` + WARNING 日志）以替身测与
  真实读取测（`tests/service/test_real_s2.py`）双向钉住。
- **S0 V1.0.1 严格错误码目录的对齐**：目录现按"一码一主"拒绝跨所有者重登记——S1 的启动
  登记（`bootstrap.initialize → register_error_codes`）改为只登记 S1 所有的码；S0 内置码
  （`NOT_FOUND`、`VALIDATION_ERROR`、`IDEMPOTENCY_CONFLICT`、`ARTIFACT_*` 等）与上游归属码
  （`SCHEMA_ERROR`/`REGISTRY_ERROR`→S2，`LLM_*`/`POLICY_DENIED`→S3，`DHIS2_*`/`DRIFT_DETECTED`
  →S4）由 S1 直接抛出、不再登记；可重试口径与目录对齐（`CONCURRENCY_ERROR` 可重试、
  `SECRET_UNAVAILABLE` 不可重试，per FD-COM-001a）。REST 语义不变，Temporal 重试以目录为准。
- 依赖图截断/limit（NFR-CAP-001 的 5,000 节点保护）在新形状下归属未定，待 S2 为
  `get_dependency_graph` 声明服务端裁剪口径；S1 暂全量转发。
- 缺口文档 S5IF-BP16/BP17 已被 CP-UI-020/021/022 吸收并部分改写（BP17"未编译返回空清单"与
  CP-UI-022 直接冲突），该文档应标注废弃或修订。

## 1.2.0 — M4 登录与权限、预算写入、M3 阶段内对话（依据 `S1_Factory控制平面_SRS.md` V1.1.0 §3.10 与总体 SRS 21.1）

三项目标能力在 SRS 里都只写了"预留"，端点形状由本轮定义：SRS §3.10 说 M4 "届时以本文档新版本定义"，
§8.1 端点表早于对话与权限接口。因此新端点的 `x-requirements` 引用**已有**需求编号
（`CP-IAM-002`、`CP-GATE-007/012`、`CP-CR-002`、`CP-WF-020`、`CP-UI-006/010/019`、`FR-SEC-001~005`、
`FR-UI-002`、`S5IF-GOV18`、`AI-CHAT-001/002/003`），不新造编号。

### 破坏性变更（仅在 `FACTORY_AUTH_MODE != off` 时生效；默认 `off`，MVP 行为逐请求不变）

- **`allowed-actions` 变为按人计算**（CP-UI-019）：状态允许但角色不允许的动作从 `allowed` 移入
  `unavailable`，`reasonCode` 取 `PERMISSION_DENIED` / `MEMBERSHIP_REQUIRED`；批量求值一次会话完成。
- **PRODUCTION Gate 改为多人审批与职责分离**（CP-GATE-012、AC-GATE-04）：所需审批数取 S4 保护属性的
  实时值（CP-WF-027，不缓存）；同一人重复审批被拒；执行本 Run 的人（`validate_operator` /
  `commit_operator`）不得审批其 DEPLOY_GATE；保护属性给出 `operatorRole` 时审批人须持有该角色；
  审批动作改用带环境类的精确名 `gate.approve.DEPLOY_GATE.<CLASS>`（附录 B 已定义，此前从未生效）。
- **批准变更请求需 SOLUTION_ARCHITECT**（CP-CR-002）。
- **RESTRICTED / PHI_PII 产物读取需单独授权并逐次审计**（FR-SEC-003）：`artifact.read.restricted`
  与 `artifact.read.phi` 从死动作变为实际调用点，授权记录 `cp.artifact.grant_created/revoked`、
  读取记录 `cp.artifact.read_restricted`。

### 新增

- **认证**（FR-SEC-001）：`POST /auth/login`、`POST /auth/logout`、`GET /auth/session`、
  `GET /auth/oidc/request`、`POST /auth/oidc/exchange`。本地账号口令用 stdlib `hashlib.scrypt`
  （自描述参数、登录时透明升级）、会话为服务端不透明令牌（库内只存 SHA-256），空闲 30 分钟与
  绝对 12 小时双时限（FR-SEC-005），Cookie 认证的变更请求需 `X-CSRF-Token`；OIDC 走授权码 +
  userinfo 断言，**本轮不引入密码学依赖**，验签点收在 `identity/authn.py::_userinfo` 一处。
- **权限管理**：`GET /roles`、`GET/POST /users`、`PATCH /users/{id}`、`POST /users/{id}/password`、
  `PUT|DELETE /users/{id}/roles`、`GET/PUT/DELETE /projects/{id}/members`、
  `GET/POST/DELETE /projects/{id}/artifact-grants`、`GET/POST /projects/{id}/production-exemptions`
  与 `DELETE /production-exemptions/{id}`。用户与角色变更全部入审计（总体 SRS 21.4）。
- **预算写入**（S5IF-GOV18、UI-ADM-003、AI-GOV-007 写侧）：`PUT /governance/budget?projectId=`，
  动作名 `governance.budget.set`（此前既不在动作目录也不被调用）；S3 `set_budget` 由恒抛
  `AI_FEATURE_NOT_ENABLED` 变为真实写入 `ai.budget` 并审计 `ai.budget.updated`，形状不合法返回
  新码 `AI_BUDGET_INVALID`(422)。执行侧判定本就生效，故 S1 不声称"写入即拦截"。
- **阶段内对话**（AI-CHAT-001/002/003、FR-UI-002）：`POST/GET/DELETE /stages/{stage_id}/chat`。
  同步一轮回合一轮答（与 `POST /artifacts/{id}/explain` 同级，不属 CP-API-012 的 202 清单），
  `Idempotency-Key` 生效；响应带上下文头（项目、阶段、活动草稿、目标环境）。S3 侧新增
  `runtime/chat.py` 与 `review.explain.v1` 提示词：历史存为版本化工件、逐条消息经 Policy Engine、
  被要求的修改在映射未冻结时走该阶段 Agent 的增量模式提交到当前草稿（S2 拒绝即如实回述），
  冻结后只答不写。
- **数据**：迁移 `0002_cp_identity` 新增 `cp.users`、`user_roles`、`project_members`、`sessions`、
  `restricted_grants`、`production_exemptions`，并给 `approvals` 记 `approver_user_id/approver_roles`、
  给 `notifications` 记 `recipient_user_id`（个人通知）。`off` 模式下这些新列保持 NULL，等于旧行为。
- `GET /gates?mine=true` 待我审批（CP-UI-010）；`stage.chat` 进入附录 B 动作与 `allowed-actions` 目录。

### 已知取舍

- 默认 `auth_mode=off`：未认证部署仍按声明的 `X-Operator` 记账，多人审批与职责分离无法判定身份，
  故只在实际启用认证时生效；`/healthz` 与启动报告回显 `authMode`，避免"以为开了其实没开"。
- OIDC 不本地验签 ID Token，改为向签发方走 TLS 调 userinfo 断言 subject；配置校验拒绝非 HTTPS issuer。
- 对话的端点形状与 `get_chat` 读接口是 SRS §8.1/§7 之外的补充（对话历史存在 S3 的工件里，
  S1 不读他方 schema），待 SRS 下一版收录编号。

## 1.1.0 — SRS V1.1.0 逐条复核后的契约补齐（依据 `requirements/S1_Factory控制平面_SRS.md` V1.1.0）

SRS 于 2026-09-29 重新发布（V1.1.0，新增 `CP-API-010~016`、`CP-WF-031`、`CP-UI-001~019`
与附录 B），本轮以它为基线逐条复核。含破坏性变更（受理信封与错误 `details`），提升次版本。

### 破坏性变更

- **长任务一律 202 受理**（CP-API-012，SRS 8.1 行内标注）：
  `POST .../inputs/{artifactId}/parse`、`POST /deployment-runs/{id}/drift-check`、
  `POST /governance/evaluations`、`GET /projects/{id}/export`、`POST /projects/import`、
  `POST /deployment-runs/{id}/commit` 的响应改为**同一个受理信封**
  `{stageId?, stepRunId, accepted}`，不再返回同步结果。`stepRunId` 是**结果落在哪条记录上**
  的 id：阶段步 → stage run；COMMIT → 执行记录（`GET /executions/{id}`）；导出 →
  PROJECT_EXPORT 产物（`GET /artifacts/{id}`）；导入 → 新项目（`GET /projects/{id}`）；
  评测 → S3 的 `evalRunId`（`GET /governance/evaluations/{id}`）。
  删除的响应组件：`CommitView`、`DriftSlot`（drift-check 侧）、`ExportView`、`ImportView`、
  `ParseResultView`；进度与结果仍经 SSE 与既有查询端点观察。
- **漂移重检成为动作**（CP-UI-013）：除受理信封外，结果写入该 Run 的最近漂移结论
  （`GET /deployment-runs/{id}/summary` 的 `drift`）、推送新事件
  `deployment_run.drift_checked`，且检测到漂移时按 CP-GATE-005 使 DEPLOY_GATE 失效。
- **错误 `details` 按码定型**（SRS 8.3，CP-API-013）：`INVALID_STATE` 现含
  `{currentState, requiredStates[], targetId}`；`APPROVAL_INVALIDATED` 的
  `mismatches[]` 项目改为 `{refType, refId, expectedHash, actualHash, problem}`（原
  `presented`/`current` 取消）；`ENVIRONMENT_LOCKED` 增 `lockChangedAt`；
  `UPLOAD_REJECTED` 的 `reason` 限定 `SIZE|TYPE`（原机器原因移到 `violation`）；
  `VALIDATION_ERROR` 统一为 `fields[{path, message}]`（原 `problems`/`field` 取消），
  且 **FastAPI 自身的 422 体也被改写为同一信封**（此前 `{"detail":[...]}` 会直接透出）。
  `reason` 键已让位给 `cause`（`INVALID_STATE` 等码），以便 `UPLOAD_REJECTED` 的枚举独占它。
- **幂等声明由文档自证**：`openapi.IDEMPOTENT_OPERATIONS` 手工表删除，改为从操作自身的
  参数引用推导——新端点再不会因漏登记而没有 `X-Idempotent-Replay`/`X-Idempotency-Stored`。
- **失败响应契约补齐**（CP-API-008 / CP-API-010）：
  ① 请求体解析失败（FastAPI 自身的 422 `{"detail":[…]}`）改由 S1 统一信封回答，
  `errorCode=VALIDATION_ERROR` + `details.fields[{path,message}]`；文档里随之删除
  `HTTPValidationError` / `ValidationError` 两个组件（它们描述的体已不存在）。
  ② 每个 `/api/v1` 操作都声明 `400/404/409/422 → components/responses/Error`，
  客户端对任何调用都能类型化失败，不再依赖各 handler 自己记得写 `responses=`。

### 字段与行为补齐

- `platform_notification_channels` 读为 `{type, target, secretRef}`（CP-UI-017）；
  人工 Patch 响应 `questions[]`（CP-UI-018）；候选项含 `contentHash`；校验问题支持
  `sortBy/sortDir`；NRR 增 `category`/`inputArtifactId`/`minConfidence` 过滤；
  阶段聚合与 `GET /workflows/{id}` 返回 `activeDraftId`（CP-UI-002）；
  输入 `parseStatus` 可为 `RUNNING`，未解析时为 null（CP-UI-007）；
  部署控制台补 `changeStats{create,update,delete,externalReferences}`（CP-UI-012）；
  附录 B 增治理放宽动作名 `governance.policy.relax`。
- **`allowed-actions` 覆盖新增端点的全部动作**（CP-UI-019）：补 `environment.probe_capabilities`、
  `artifact.purge`、`governance.provider.register` / `.enable`、`governance.regression.record`、
  `governance.evaluation.run`；漂移重检改用附录 B 自己的动作名 `deployment_run.drift_check`
  （原借用 `deployment.create`），环境与 Run 类动作继续携带 `targetId`。
- **追溯补全**（CP-API-010 ③）：`x-requirements` 现在按 SRS 8.1 的 `需求` 列登记所属的
  `CP-*` 编号（`CP-UI-001~019`、`CP-PRJ-002~006`、`CP-API-009/014/015/016` 等），
  每个操作都至少引用一条 S1 自己的需求；契约-wide 的 `CP-API-008/010/011` 挂在全部操作上。
  `errors` 的中英文消息目录不变（本轮未新增码）。

### 真实 S2 + 真实 S4 链路暴露的问题（`tests/service/test_real_s2_s4.py`）

把两个真实提供方接进同一条链路后，先撞出来的都是假替身永远说不了谎的那类错误。

**已修（S1 侧）**

- **Issue 的文本会整条丢掉**：S2 把结论写在 `message`、修复建议写在 `remediation`，S1 的端口镜像与
  `IssueView` 只声明 `description` / `suggested_action`，而两边都是"未知字段忽略"——于是一条 BLOCKING
  带着空描述抵达 S5，没人能据此采取行动。现在提供方拼写在边界处折进 S1 的名字（与 `severity`→`level`
  同一处、同一机制），S1 自己的名字在两者都给时仍然优先。
- **`POST /environments` 的转发载荷被真实 S4 拒绝**：`to_spec()` 把 `operator` 放进了 spec 字典，而 S4 的
  `EnvironmentSpec` **禁止未知字段**（不是忽略），因此整请求失败。操作者本来就已由 `X-Operator` 进入请求
  上下文、S4 自己读取，故该键从载荷中删除；同时 `name` 在 S4 是必填、在 S1 可选，缺省以 `environmentKey`
  代偿。**REST 请求体形状未变。**
- **测试夹具从不清理 `dx`**：`harness.py` 的 drop 列表缺 S4 的 schema，环境、连接测试记录与能力画像会跨用例
  泄漏（版本表就在 `dx` 里，重跑迁移是 no-op）。装上真实 S4 后这会成为错用例的成因，故一并补上。
- 兼容性闸（见上条 `usedModel`）另覆盖字段类型比对，`scripts/install_dev.sh` 现在安装 S2/S3/S4，
  否则真实提供方用例在新克隆上静默跳过。

**待裁决（S1 不代答）**

- **DISCOVERY 的 scope**：`services/steps.py:374` 以 `DESIGN_REFERENCE` + Registry 对象清单请求发现，而项目刚
  建成时 Registry 还是空的，S4 判为"什么都没选中的 scope"并拒绝（`discovery/scope.py:168`）。S1 该改问
  `GREENFIELD`、还是该问 Blueprint 自己的对象类型，是需求问题；真实链路用例因此止步于此。
- **提供方领域错误一律 500**：S4 拒绝 PRODUCTION 缺提交窗（`DX_ENVIRONMENT_INVALID` / DX-ENV-002）时，S1
  透出 500 而不是 CP-API-008 的统一信封与可读错误码。需要一张 S1 侧的错误码映射（或 S4 侧改用公共错误类型）。
- 上游两处小坑：S4 的内存实例在 UID 用尽后发出 13 字符的填充 UID，而 S4 自己校验要求 11 字符；S4 为 S1 旧名
  `last_connection_check` 保留的别名，在 S1 收敛到 `get_last_connection_test` 之后已无人调用。
- `IssueView` 同时发布 `suggested_action` 与 `remediation` —— 一个概念两个名字，与 00 文档 6.10 相悖，但删除
  其一是破坏性变更，需 S5 确认它读哪一个。

### 待同步

| 文档 | 待办 |
| --- | --- |
| SRS CP-EVT-007 | 事件类型清单需补 `deployment_run.drift_checked`——CP-UI-013 要求漂移重检"推送事件"，而 007 的列表里没有可用的类型 |
| SRS CP-API-012 | 受理信封只说 `stepRunId`，未说它指向什么。本文把它实现为"结果落在哪条记录上的 id"（阶段步为 stage run，转发步为提供方记录，产物步为产物），请确认该解释即需求本意 |
| SRS 8.3 | `reason` 键在 `INVALID_STATE` 类拒因里的用法未列出，本文改名为 `cause` 以让 `UPLOAD_REJECTED` 的 `SIZE|TYPE` 枚举独占 `reason` |
| S5 缺口清单 | 无新增待办：本轮全部条目已回写 SRS V1.1.0 |

## 1.0.0 — 裁决后的 C 类条目与契约收尾（依据 `requirements/S1_add_api_for_S5.md` v0.2.0）

第 11 章 15 项 C 类条目已于 2026-09-29 全部裁决（6 项调整实现方式或归属），本轮实施那些
标注 **【裁决 2026-09-29】** 的段落。含破坏性变更，因此提升主版本。

### 破坏性变更

- **追溯字段改名**（S5IF-G11 ②）：`x-s5if` → 通用的 **`x-requirements`**，同时装 S1 自己的
  `CP-*` 与缺口清单的 `S5IF-*`。`servers` 改为**空数组**，基址由部署注入（S5 与 S1 同域）。
- **`POST /stages/{id}/run` 与 `/rerun` 改为 202**，响应固定 `{stageId, stepRunId, accepted}`
  且**不再出现 `engine`**（S5IF-G06）。RUNNING 迁移与 step run 现在在请求内同步完成，
  所以阶段不可启动时调用方立刻拿到 409，而不是后台线程里的失败。
- **COMMIT 的幂等键只剩请求头**（S5IF-G08）：请求体里的 `idempotencyKey` 已删除，缺
  `Idempotency-Key` 头返回 **400 `IDEMPOTENCY_KEY_REQUIRED`**。同键重发由 S1 用已存响应重放，
  不再进入 handler；`commit_run` 里按键向 S4 恢复的路径保留（服务层测试覆盖）。
- **分页信封统一**（S5IF-G04）：一个形状 `{items, limit, total?, offset?, nextCursor?}`。
  `GET /audit`、`GET /projects/{id}/audit`、`GET /workflows/{id}/artifacts` **改为游标分页并
  去掉 `total`**，`offset` 参数不再接受；其余列表参数统一为 `sortBy` + `sortDir`（`ASC|DESC`），
  排序与分页都在数据库里做。游标对客户端不透明，伪造它返回 422。
- **`GET /settings` 的形状**（S5IF-FLD10）：除 `items[{key,value}]` 外新增 `values`——七个键的
  **逐键定型对象**（生成器只能从它做表单）。设置键是数据，保持 `snake_case`，并在 OpenAPI 里
  声明为枚举（S5IF-G03 例外的唯一一处）。新增 `GET /upload-limits`，`settings.manage` 之外的
  上传者也能读到 `maxUploadBytes` / `allowedUploadTypes` 做浏览器端预检。
- **阶段视图的 `feedback` 与 `inputBlueprintRef` 已定型**（S5IF-FLD02）：`feedback` 带
  `kind|reason|sourceStage|gateId|issues[]|createdAt`，`kind` 的已知值集合为
  `GATE_REJECTED|PATCH_REJECTED|VALIDATION|POLICY_DENIED|BUDGET_EXCEEDED|EXECUTION_FAILED`
  且**声明为可扩展**——S5 遇未知值走通用渲染。冻结阻塞（原 `FREEZE_BLOCKING`）归入 `VALIDATION`
  并把原值留在 `reason`。
- **`Issue` 的级别字段只有一个名字 `level`**（S5IF-BP14 / UP11）：S2 说 `level`、S3 说
  `severity`，S1 在边界上归一；`severity` 仍可输入但绝不出现在响应里。端口的 `Issue` 镜像
  同样只声明 `level`，S3 的 `severity` 在镜像入口处折进来 —— 两个名字并存的话，"哪个说了算"
  要到运行期才暴露，而 BLOCKING 被读成默认值会让一次失败的部署看起来是通过的。
- **`usedModel` 改为布尔**（S5IF-INP02，AI-INP-004）：需求问的是**是否**用过模型，S3 的
  `ParsedInput.used_model` 也确实是 `bool`，而 S1 的镜像与视图都写成了 `string` —— 于是每一次
  真实 `parse_input` 的结果在 S1 侧 `model_validate` 直接抛错（假替身从不填这个字段，所以一直没
  被发现）。字段含义不变，类型收紧为 `boolean`（未解析过时仍为 `null`）。
- **提供方镜像的字段类型有了一道闸**（CP-RT-005）：`tests/unit/test_provider_mirrors.py` 用
  S2/S3/S4 的公共类型做往返并逐字段比对类型种类；函数签名一致不代表字段类型一致，上面那条就是
  签名全对而类型错位。
- **枚举值一律 UPPER_SNAKE**（S5IF-G03 提升后的全局约定）：S2 的 `ChangeLevel` 写作
  `Cosmetic|Minor|Major`，公共 REST 上回答 `COSMETIC|MINOR|MAJOR`（`GateDecisionView`、
  版本列表的 `changeLevel`）。S1 内部仍用提供方拼写做比较，只在投影处归一。

### 新增端点（6）

| 编号 | 端点 | 说明 |
| --- | --- | --- |
| S5IF-FLD03 | `GET /api/v1/gates?status=&projectId=&type=&limit=&offset=` | 跨项目待审批 Gate；元素 `GateSummaryView` 带 `projectName`、`approvalCount`、`warningCount`、`unresolvedWarningCount`、`highOpenQuestionCount` |
| S5IF-FLD04 | `GET /api/v1/deployment-runs?status=&environmentKey=&outdated=&limit=&offset=` | 跨项目 Run 列表，默认 `updatedAt` 倒序；`environmentClass` **不落 S1 库**，请求时查 S4 并最多做 60 秒进程内缓存 |
| S5IF-FLD05 | `GET /api/v1/deployment-runs/{runId}/summary` | 部署控制台聚合；**该请求对 DHIS2 的调用次数为 0**（漂移、能力新鲜度、连接测试都只回最近一次记录，`get_protection` 是唯一允许的实时读） |
| S5IF-FLD05 | `POST /api/v1/deployment-runs/{runId}/drift-check` | 实时重检漂移必须是显式动作；结果写回该步的执行记录并写审计 |
| S5IF-BP11 | `GET /api/v1/deployment-runs/{runId}/environment-bindings/{businessKey}/candidates` | 归属 S4 的 `search_discovery_objects`（只读产物、不访问 DHIS2），S1 解析该查哪个发现产物与对象类型后转发 |
| S5IF-BP14 | `GET /api/v1/workflows/{workflowId}/validation-issues?runId=&level=&sourceStage=&ruleId=&businessKey=` | 跨报告问题清单，筛选与分页由 S2 的 `query_issues` 承担 |

### 字段补全

- **阶段视图七项聚合由服务端计算**（S5IF-FLD01）：`inputCompleteness{satisfied,total,ratio,
  missing[{key,label}]}`、`artifactCount`、`openQuestionCount`、`highOpenQuestionCount`、
  `blockingIssueCount`、`lastRunAt`、`currentArtifactVersion`。**整个阶段列表一次算完**，
  不逐阶段查询；`missing[].label` 随 `Accept-Language` 本地化。
  “输入完成度”按裁决落成 S1 维护的前置清单：前置阶段完成、上游必需产物存在、
  REQUIREMENTS 需至少一个已解析输入、BIND_COMPILE 及其后的 Run 步骤需本 Run 已记录能力档案。
- **工作流视图带 `activeDraft`**（S5IF-BP05 唯一硬要求）：S1 由 `projectId`（＋阶段）解析出
  活动 `draftId`——先问 S2 的项目语义，拿不到就用 S1 自己记在阶段上的 `inputBlueprintRef`，
  因此不依赖 S2 导出 `active_draft_id`（原 UP03 对 S5 的阻塞解除）。
- `GET /artifacts/{id}/content?download=true` 返回 `Content-Disposition: attachment`，文件名三条
  规则：输入用上传原名、导出用 `{项目名}-export-{UTC日期}.zip`、其余用 `{类型}-{产物ID}.{扩展名}`；
  内容已清除时**两种请求都**返回 410 `ARTIFACT_PURGED`（S5IF-FLD09）。
- `allowed-actions` 增补 `artifact.download`、`deployment_run.drift_check`（按 Run 给 `targetId`），
  并把 `targetId` 语义写进文档：stage/gate/run 用行 ID、环境动作用环境键、无目标时省略
  （S5IF-FLD12 / G10）。
- 执行计划步骤、能力档案、评估用例（`cases[]`/`injectionCases[]`）、Agent 名录、连接检查八项、
  Gate 回答、COMMIT 追溯的 `approvals[]` 等原先的自由对象改为具名组件。

### 契约收尾（S5IF-G01 全量）

128 个操作（113 条路径）的 2xx 响应体现在全部引用具名组件。仍有 23 处**字段**（分布在 19 个操作上）是开放对象，逐条登记在
`tests/api/test_openapi_contract.py` 的 `OPEN_FIELD_EXEMPTIONS` 里并写明理由（提供方未发布格式，
或裁决要求 S1 原样透出）；新增开放字段而不登记会直接失败，登记项一旦被定型也会失败，
所以这份清单不会悄悄腐烂。响应头 `X-Idempotent-Replay` / `X-Idempotency-Stored` /
`X-Correlation-Id` / `traceparent` 也已在 `components.headers` 声明并被操作引用——
名称以 `rest/middleware.py` 的实现为准（S5IF-G05 裁决），S1 的 CTD 里那个
`Idempotent-Replayed` 需要按本实现修订（见 11.4 文档同步待办）。

### 顺带修掉的既有缺陷

- `commit_run` 的步骤记录会被后一次写入整体覆盖，导致能力档案与阻塞计数丢失——现在按字段合并。
- 阶段运行经 REST 提交时，`stage.started` 事件的载荷里没有阶段标识，聚合只能靠猜。
- `InProcessEngine` 原先把非法启动吞进后台线程；现在受理与执行分开，409 回到调用方。


### 本节之外还欠的文档同步（缺口清单 §11.4，S1 不代写）

裁决写回了条目正文，但下面几处仍由各文档的所有者修订，S1 这边已按裁决实现：

| 文档 | 待修订 |
| --- | --- |
| 00 文档 §6.x | 公共 REST 线上约定：字段 camelCase、枚举值 UPPER_SNAKE（系统设置键属数据除外）、信封 `{items,limit,total?,offset?,nextCursor?}`、跨切请求头清单、错误码归一规则、`x-requirements` 追溯字段、`servers` 由部署注入 |
| S1 SRS | §8.1 增补本轮 7 条端点与 `content?download=` 的文件名规则；阶段视图增补聚合字段并**给出"输入完成度"的前置清单定义**；§8.3 增加按码定义的 `details`；commit 以 `Idempotency-Key` 请求头为唯一幂等来源；响应头名以实现 `X-Idempotent-Replay` 为准（现有 CTD 写 `Idempotent-Replayed`） |
| S2 / S3 / S4 SRS | 统一 Issue 结构（级别字段 `level`）、`query_issues`、`search_discovery_objects` 与"最近一次连接测试结果"只读查询、`severity`→`level` |

## 0.2.0 — S5 接口缺口清单第 1～3 批（依据 `requirements/S1_add_api_for_S5.md`）

### 新增端点（49）

| 组 | 编号 | 端点 |
| --- | --- | --- |
| 环境与能力 | S5IF-ENV01～ENV12 | `/environments`、`/environments/{environmentKey}`、`POST /environments`、`PATCH /environments/{environmentKey}`、`POST .../disable`、`GET .../protection`、`POST .../emergency-lock`、`POST .../test-connection`、`GET .../capability-profile`、`POST .../probe-capabilities`、`GET .../discovery-results`、`GET /environments/expiring-credentials` |
| Blueprint / Registry | S5IF-BP01～BP10、BP12、BP13、BP15 | `.../blueprint/section-schemas[/{section}]`、`.../blueprint/versions`、`.../blueprint/entries`、`.../blueprint/draft`、`.../blueprint/diff`、`/projects/{id}/registry`、`/projects/{id}/registry/managed-scope`、`/projects/{id}/uid-pool`、`/projects/{id}/decisions`、`/deployment-runs/{runId}/environment-bindings[/{businessKey}]`、`/workflows/{id}/nrr` |
| Governance | S5IF-GOV01～GOV19 | `/governance/{providers,models,prompt/activations,prompts[/{name}/versions/{version}],regression-records,policy,policy/relaxations,evaluations,usage,budget,agents}`、`/projects/{id}/effective-policy` |
| 输入与 Agent 运行 | S5IF-INP01～INP03、INP05～INP07 | `POST /projects/{id}/inputs/{artifactId}/parse`、`GET .../column-mapping`、`POST /artifacts/{id}/explain`、`GET /workflows/{id}/agent-runs/{runId}`、`POST .../cancel` |

### 破坏性变更

- **响应与请求命名统一为 camelCase**（S5IF-G03）。修正了 5 处 `model_dump()` 绕过别名导出的泄漏：
  `GET /executions/{id}`、`POST /executions/{id}/reconcile`、`GET /audit/commit-trace/{id}` 的
  `execution` 子对象、所有内嵌 `issues[]`、Gate 说明的 `why.decisions[]`。
  唯一保留的下划线键是 S2 自有的定位键 `_section` / `_path`（S5IF-BP04 要求原样透出）。
- **全部门控的 2xx 响应改为具名组件**（S5IF-G01）。`additionalProperties: true` 的自由对象仅剩
  `GET /artifacts/{id}/content`（改为 `application/octet-stream`）与
  `GET /workflows/{id}/events`（改为 `text/event-stream`）。
- 子系统未安装或未导出被调函数时，回答 503 `PROVIDER_UNAVAILABLE`（可重试）而不是 500 `INTERNAL_ERROR`。
- **提供方错误码归一为 S1 目录中的码**（S5IF-G07.2）：`DX_OUTSIDE_COMMIT_WINDOW` → `OUTSIDE_COMMIT_WINDOW`、
  `DX_ENVIRONMENT_LOCKED` → `ENVIRONMENT_LOCKED`、`DX_ENVIRONMENT_IN_USE` → `INVALID_STATE`、
  `AI_BUDGET_EXCEEDED` → `BUDGET_EXCEEDED`；原码保留在 `details.providerErrorCode`。
  此前未登记的提供方码（`AI_*`、`BP_UNSUPPORTED_SCHEMA_VERSION`、`UID_POOL_EXHAUSTED`）现在给出
  4xx/5xx 而不是 500。
- **`execution.commit` 不可用时的理由优先级调整**（S5IF-AC06）：环境级理由（锁定、时间窗、缺少前置验证）
  现在排在 `GATE_NOT_APPROVED` 之前，且 `targetId` 为环境键而非 Run ID。

### 字段补全

- `stage` 视图保留 `feedback` / `inputBlueprintRef`（形状属 S5IF-FLD02，待架构组裁决，故定型为开放对象而非删除）。
- `gate.warnings[]` 统一为 `{issueId,ruleId,level,message,businessKey,sourceStage,remediation}`；
  `confirmationItems[]` 与 `boundObjects[]` 定型（S5IF-FLD07）。
- `question.suggestions[]` 定型为 `{id,text,rationale,changesUid,regenerates,columnMapping?}`（S5IF-FLD08）。
- `input` 列表补 `parseStatus`/`errorCode`/`errorMessage`/`parserVersion`/`parserType`/`usedModel`/`parseId`/`nrrArtifactId`（S5IF-INP02）。
- `deploymentRun` 视图补 `projectId`/`projectName`/`environmentClass`/`lastVerifiedAt`/`postVerifyArtifactId`（S5IF-FLD06）。
- `POST /stages/{id}/patches` 补 `assignedUids`（S5IF-FLD11）。
- 七个系统设置键逐个定型（S5IF-FLD10）。
- `allowed-actions` 增补 `environment.*`、`governance.*`、`input.parse`、`input.confirm_column_mapping`、
  `artifact.explain`、`agent.cancel`、`registry_binding.set`、`uid_pool.replenish`（S5IF-FLD12）。

### 请求体类型化（S5IF-G02）

既有 mutating 端点不再收自由 `dict`：`POST /projects`、`PATCH /projects/{id}`、`POST /projects/import`、
`PUT /projects/{id}/target-environments`、`PUT /projects/{id}/settings`、`PUT /settings`、
`POST /stages/{id}/[resolve-manual-check|patches|questions]`、`POST /gates/{id}/[approve|reject]`、
`POST /questions/{id}/answer`、`POST /workflows/{id}/deployment-runs`、`POST /deployment-runs/{id}/commit`、
`POST /audit/verify`、`POST /projects/{id}/change-requests`、`POST /change-requests/{id}/reject`
均改为具名组件，由 S1 先校验（422 `VALIDATION_ERROR` + `details.field`）再转发。

三条既有语义**刻意未在模型里抢判**，因为它们的判定属于服务层、结论是 409 `INVALID_STATE` 而不是请求畸形：
Gate 驳回的理由为空、`resolve-manual-check` 传入非 `PASSED|FAILED` 的结论、
`PATCH /projects/{id}` 提交不可编辑字段。模型只声明键与类型，把状态判断留给原处。

两处**有意保持开放**：上传输入是 multipart 无 JSON 体；`POST /stages/{id}/patches` 里的 `patch`
本体是 S2 的文档级操作集（S1 SRS 8.1 规定资源模型由提供方定义），S1 只声明外壳、不代 S2 造字段。

### 契约元数据

- 跨切请求头 `X-Operator`、`Idempotency-Key`、`Accept-Language`、`X-Correlation-Id`、`traceparent`
  进入 `components.parameters`，逐操作以 `$ref` 引用（S5IF-G05）。
- 统一错误信封进入 `components.responses.Error`（S5IF-G07）。
- 每个 `/api/v1` 操作带 `x-s5if: [...]` 回指需求编号（S5IF-G11）。
- 新增 `servers` 与文档 `description`。

### 修复的既有缺陷（非本轮需求，但阻塞转发）

- `adapters/delegating.py` 只会在 `session=None` 时把该参数丢弃，从不注入。因此对着已安装的 S2，
  所有要求 `*, session` 的读（含已登记的 `registry_object_refs → get_registry`）都会
  `TypeError: missing 'session'`。新增 `SESSION_METHODS` 分类修好这一类问题。
- `GET /audit?operatorName=` 把 camelCase 的参数名当作 Python 关键字传给 `query_all`，必然 `TypeError` → 500。
- `Gate` 批准时对 `seenBoundHashes` 的元素类型无防御：传字符串（文档 §S5IF-FLD07/AC14 的字面读法）
  会 `AttributeError` → 500。现同时接受 `{refId,contentHash}` 对象与裸哈希串两种形式。
- `tests/unit/test_port_conformance.py` 被 `testing/fakes.py` 的注释引用但文件不存在；已建立，
  作为"加了端口方法却忘了 fake"的唯一静态防线。
- 错误码中英目录此前无相等性校验（CP-NFR-09）；已加入，并补齐 `PERMISSION_DENIED` 等缺失条目。

### 未交付（需架构组裁决，见需求文档第 11 章）

S5IF-FLD01、FLD02、FLD03、FLD04、FLD05、FLD09、BP11、BP14、G04、G06、G08。
其中 `run`/`rerun` 仍返回受理信封与同步结果的并集（G06 未裁决），既有三种分页信封保持原状（G04 未裁决），
新端点一律使用 `{items,total,limit,offset}`。

### 依赖上游而未定型的部分（需求文档第 9 章）

`capability-profile` 的 `content`、`discovery-results` 的对象级列表、整份草稿 `document`、
版本 vs 草稿 Diff、NRR 的 `category`、结构化列映射 `tablePlans`、`eval.get` 的逐例结果、
`BindingInfo` 的观测三元组、`DeletionPolicy` 取值 —— 这些字段在响应中显式为 `null`，
未由 S1 臆造。
