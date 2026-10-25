# 006 · S1 契约缺位的临时兼容与 NOT_GENERATED 判定

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端

## 背景

S5-M SRS **UM-REV-007**（源自 S1 **CP-UI-022**）要求：依赖图、开发包、OpenAPI、UI Specification 等派生资源在「尚未生成」与「确实不存在」两种情况下给出不同引导，判据是 S1 的 404 `NOT_FOUND` + `details.reason ∈ {NOT_GENERATED, NOT_EXIST}` 与 `details.requiredStage`。

本轮实测：`NOT_GENERATED` 在 S1 仓库中出现次数为 **0**（全库 grep），`object_graph` 在无蓝图版本时抛的是 `NotFoundControlError(..., details={"projectId": ...})`（`S1/src/factory_control/services/blueprint_reads.py:269`，registry/managed_scope/draft 同样如此，见 `:222`、`:377`、`:127`）；开发包则用 **200 + `kind: "NONE"`** 表示尚未交付（`PackageFilesView.kind` 默认值 `"NONE"`），正是 CP-UI-022 明令禁止的表示方式。

S1 SRS §8.1 与已发布契约 v1.2.0 也不一致：§8.1 写 `GET /workflows/{id}/object-graph?runId=&dhis2Type=&businessKey=`（CP-UI-020），现网实现是 `GET /projects/{project_id}/object-graph?version&view&environmentKey&dhis2Type&q&limit`（`S1/.../rest/routes/blueprint.py:131-165`，标 `S5IF-BP16`）。CP-UI-021 的单文件读取 `GET /projects/{id}/frontend-package/files/{path}` 在 v1.2.0 的 133 条路径中不存在。

## 备选方案

| 候选 | 代价 | 风险 |
| --- | --- | --- |
| A. 页面各自判断 `errorCode==='NOT_FOUND'` | 0 新文件 | 六处各写一遍、语义互不一致；S1 上线 reason 后要在六个文件里找兼容代码 |
| B. 集中在 `src/api/notGenerated.ts` 的唯一意图映射器 + 显式标记的兼容分支 | 1 个新模块 | 兼容分支必须可被一次性删除，否则变成长期债务 |
| C. 等 S1 先落地 CP-UI-022 再做引导式空状态 | — | UM-AC-19（M2）本轮无法验收；UM-IF-003 禁止前端要求后端先改 |

## 决策

取 B。所有派生资源读取都产出一个 `ReadIntent`（`ok / notGenerated{requiredStage} / notExist / error`），页面只消费它，不认识 HTTP 错误码。三条规则按来源分层：

1. **前瞻**：`details.reason === 'NOT_GENERATED'` → 直接取 `requiredStage`（缺失时退到 `details.stage`）。这条就是 CP-UI-022 的实现，不需要任何删除动作。
2. **临时兼容 A**：`NOT_FOUND` 且 `details.reason` 缺失且资源属于派生集合 → 判为 NOT_GENERATED。理由：这类资源在无构建时唯一可能的解释就是"还没生成"，而 S1 现在不区分两者。标记 `// S1 V1.1.2 上线后删除`。
3. **临时兼容 B**：开发包 200 且 `kind==='NONE'` 且文件为空 → 判为 NOT_GENERATED，`requiredStage` 取 `HANDOFF`。这是成功响应，所以走 `packageIntentOf` 而不是 `readIntentOf`。

`requiredStage` → 阶段路由的解析放在 `src/lib/stageJump.ts`：用既有的 `useWorkflowOfProject` + `useStages` 找 `stageType` 匹配的**真实** stage id，找不到就禁用按钮并说明"当前流程没有这个阶段"，不臆造 id（模式 A 没有 PAGE_DESIGN/HANDOFF）。中文名复用 `src/lib/labels.ts` 的 `stageTypeLabel()`，`stageTypes.BASELINE_COMPILE` 已是「基线编译」，不新建第二份映射。

依赖图端点采**适配层按 §8.1 命名**：`src/api/objectGraph.ts` 对外暴露 `getObjectGraph(workflowId, {runId, dhis2Type, businessKey})`，内部把 workflow→project、`runId`→`GET /deployment-runs/{id}` 的 `{blueprintVersion, environmentKey}`、`businessKey`→`q` 后调用现网 v1.2.0 路径。S1 上线 §8.1 后只改这一个文件。

## 验收对应

- UM-AC-19：`src/api/notGenerated.test.ts`（前瞻、两条兼容含阶段兜底、NOT_EXIST、500、无错误）；`src/fui/FuiGuidedEmpty.test.ts`；`tests/e2e/notGenerated.spec.ts`（未编译 → 文案含「完成基线编译后可查看」且跳转真的落到 `/stages/{id}`，并在切回已编译数据后断言引导消失）
- UM-REV-007 的语义本身：`docs/adr/README.md` 索引 + 本文件记录的删除条件

## 后果

正面：S1 补齐 `details.reason` 时，前端只删两个分支，页面代码不动；引导式空状态在 S1 尚未合规的情况下就已经可验收。

负面：兼容 A 是有根据的猜测——若 S1 将来对"项目根本不存在"也返回无 reason 的 NOT_FOUND，用户会看到"完成基线编译后可查看"而不是"内容不存在"。已用 `requiredStage` 缺失时退化为通用文案（`notGenerated.awaitingGeneric`）降低误导。

债务：**临时兼容清单**（本文件是索引，删除条件写在各处代码注释里）
1. `src/api/notGenerated.ts` 兼容分支 A —— S1 上线 `details.reason` 后删除；
2. `src/api/notGenerated.ts` 兼容分支 B —— S1 把开发包改为 404 + NOT_GENERATED（CP-UI-022）后删除；
3. `src/api/objectGraph.ts` 的路径映射 —— S1 上线 §8.1 的 `GET /workflows/{id}/object-graph` 后删除；
4. `src/features/review/ApiReviewView.vue` 用 OPENAPI **产物**而非 §8.1 的开发包单文件读取（CP-UI-021 未实现，v1.2.0 无 `files/{path}`）—— S1 落地后改指开发包文件。

## 复审触发

- S1 仓库中出现 `NOT_GENERATED`（即 CP-UI-022 落地）：本文件必须重写，兼容分支 A/B 删除；
- S1 契约中出现 `/workflows/{id}/object-graph`：映射层删除，本文件的第 3 条债务出清；
- S1 契约中出现 `/projects/{id}/frontend-package/files/{path}`：第 4 条债务出清，OpenAPI/UI Specification 改从开发包读取；
- UM-REV-005「不得通过 S1 或其他途径代理此类请求」与 UM-ADM-001「连接测试」的读法由产品裁定为绝对禁止时：`EnvironmentsView` 的测试连接按钮必须删除，UM-ADM-001 转为前端无法实现项。
