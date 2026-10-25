# 003 · OpenAPI 文档视图：自研 Schema 树，不引入 Scalar

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端

## 背景

**UM-CAP-003**（§4.1，桌面端，M3）要求：按标签与 operationKey 浏览；展开任意层级的嵌套 Schema 并解析 `$ref`；显示请求与响应示例、参数、错误响应、`x-registry-refs`；全文搜索；且**只读**（UM-REV-005）。§4 推荐 Scalar API Reference（按需加载，仅桌面完整视图），同时允许自研，UM-TECH-003 允许换方案只要过能力验收。

上一轮的实现有三处不达标：`JSON.parse` 解析产物（S2 编译出的是 **YAML**：`OPENAPI_FILE = "api/openapi.yaml"`、`OPENAPI_SPEC_VERSION = "3.1.0"`，见 `S2/src/factory_blueprint/compilers/openapi.py:49,74`），解析异常被 `catch` 后返回空数组，所以**对真实产物这个页面一条接口都不显示**；只列 operation 摘要，没有 `$ref`、没有嵌套 Schema；产物靠 `/openapi|api/i` 正则挑，而类型是精确 token `OPENAPI`（`S1/src/factory_control/testing/fakes.py:127`）。

## 备选方案

体积由 `npm run build && npm run check:budget` 实测（gzip）：

| 候选 | 首屏 | 懒加载块 | 判定 |
| --- | --- | --- | --- |
| Scalar API Reference | 0（懒加载） | 未采用，无法在此给出诚实数字 | 不取。三点：① Scalar 自带 Console/try-it 入口，正是 UM-REV-005 明令禁止的能力，去掉它等于和库的默认形态对着干；② 它自带样式与 DOM，页面层无法只经 FUI 出口（UM-TECH-001）且 Lighthouse 无障碍分数不可控（UM-NFR-04 要求 ≥95）；③ §4 只是「推荐」 |
| **自研 Schema 树** | 0 | `OpenApiDoc` 内核 6.1 KB + `docModel`/js-yaml 18.4 KB，均只在懒加载路由出现，不在 `dist/index.html` 的首屏集合里 | **取**。UM-CAP-003 的五项能力逐条实现，样式走 FUI 设计令牌，展开节点是真实 `<button aria-expanded>` |
| 继续用卡片列表 | 0 | 0 | 不取。UM-CAP-003 明确不满足 |

YAML 解析器取 `js-yaml`（实测 13,378 B gzip）而非 `yaml`（实测 31,993 B gzip）：文档是机器生成的，不需要 round-trip 与注释保留，`yaml` 多出的体积买的是我们用不到的能力。

## 决策

自研。分层与 JSON 编辑器一致（UM-TECH-002）：页面只见 `src/fui/FuiOpenApiViewer.vue`（轻壳），其内部 `defineAsyncComponent(() => import('./internal/OpenApiDoc.vue'))`；纯逻辑在 `src/lib/openapi/{parseDoc,resolve,docModel,exampleFromSchema,search}.ts`。`$ref` 解析带记忆化、在途链循环守卫、深度 8 与展开节点 400 两个独立上限，命中上限或循环时**显示标记而不是静默截断**；400 上限按当前选中的 operation 计数，避免浏览整本文档后预算被耗尽。移动端保留接口卡片（UM-REV-005 第一句）。文档来源仍是 OPENAPI 产物的 `content` 端点：S1 SRS §8.1 没有 OpenAPI 服务端点，CP-UI-021 的单文件读取 `frontend-package/files/{path}` 在 v1.2.0 也不存在，所以这条记为对 S1 的依赖（ADR-006 债务第 3、4 条）。

两处必须说出来的诚实降级：S2 的渲染器只写 `responses["200"]`，因此**真实文档里没有错误响应**，视图显示「文档未定义错误响应（编译产物目前只写入 responses["200"]）」而不是编造；`requestExample/responseExample` 落在 `api/examples/*` 而无端点可读，因此示例由 Schema 确定性合成，并在有声明值时优先使用——标签分别是「示例：文档内声明」与「示例：由 Schema 推导（文档未提供该接口的示例值）」。**UM-IF-003 禁止向 S1 提新接口**，这是在既有契约下唯一能满足「显示请求与响应示例」的做法。

## 验收对应

- UM-CAP-003：`src/lib/openapi/openapi.test.ts`（YAML 解析、`#/components/schemas` 解析、循环引用标记、深度上限、示例确定性、3.1 `["string","null"]` 归一、按摘要全文检索）；`src/fui/internal/OpenApiDoc.test.ts`（两栏渲染与展开标记）
- UM-AC-20 / UM-REV-005：`src/test/isolation.test.ts`（出网原语白名单、URL 字面量零命中、try-it 词表零命中、依赖黑名单里 `@scalar/`、`redoc`、`swagger-ui` 被显式禁止）；`ApiReviewView.vue` 与 `OpenApiDoc.vue` 文件头写明只读约束
- UM-NFR-02：`npm run check:budget` → `docs/reports/bundle-budget.md`（首屏 JS 不变，`OpenApiDoc` 在按名豁免清单中并被打印体积）

## 后果

正面：API 审核页第一次能真实显示接口（B2 修复）；UM-CAP-003 五项能力齐备且体积只占一个懒加载块；不引入任何 UI 库，UM-TECH-001 边界仍然成立。

负面：Schema 树的正确性由本仓库维护（`oneOf/anyOf` 变体、`allOf` 合并、3.1 的 `$ref` 兄弟关键字都要自己实现）；示例是推导的，读者可能误以为是交付包里的真实示例——已用标签与「非交付包内示例」措辞压制该风险。

债务：错误响应与真实示例都依赖 S2 把这些写进 `openapi.yaml`（或 S1 提供 `frontend-package/files/{path}`），在此之前视图只能显示「未定义」。

## 复审触发

- S1 落地 `GET /projects/{id}/frontend-package/files/{path}`（CP-UI-021）→ 文档与示例改从开发包读取，本 ADR 的产物来源段作废重写；
- S2 开始输出错误响应与真实示例 → 撤掉合成示例，改为直接显示；
- 产品裁定可以引入带 Console 的第三方文档器 → 必须先解决 UM-REV-005 的 try-it 禁止与 FUI 边界，二者任一条不成立都不得引入。
