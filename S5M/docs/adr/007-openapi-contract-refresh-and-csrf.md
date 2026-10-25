# 007 · 契约刷新与 X-CSRF-Token

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端

## 背景

UM-IF-002 要求客户端与类型由 S1 发布的 OpenAPI 生成，本轮按约束「类型从 S1 最新的 OpenAPI 重新生成，不手写接口类型」重跑 `npm run codegen`。

刷新前的 `contract/openapi.json`（2026-10-01 01:20 复制）与 S1 当前发布的 `docs/openapi.json`（02:11）差异（实测）：路径集同为 **133**、操作数同为 **156**、`info.version` 同为 **1.2.0**，差异全部集中在鉴权面——S1 新增顶层 `security: [{sessionCookie},{bearerToken}]`、`components.securitySchemes{sessionCookie, csrfCookie, bearerToken}`、`components.parameters.XCsrfToken`，并把该参数挂到 **73/156** 个操作上；vendored 副本这三项全为 0。

`src/api/http.ts` 当时没有任何 CSRF 处理。S1 的中间件在 `middleware.py:384-396` 对**cookie 会话**的改状态请求校验该头，不匹配返回 `403 CSRF_FAILED`（`details={header}`）。也就是说：光刷新类型不会报错（`src/api/types.ts` 只读 `components.schemas`，请求全部走手写 `apiGet/apiSend`，编译期零破坏），但一旦 S1 开启 cookie 会话，**所有写操作在运行期 403**。这类"类型安全、运行期坏"的组合是最容易漏过评审的。

00 文档 §6.11 的跨切请求头约定要求这类头必须在契约中声明并由客户端统一注入。

## 备选方案

| 候选 | 代价 | 风险 |
| --- | --- | --- |
| A. 只刷新类型，CSRF 等真出事再说 | 0 | 403 只在启用会话的环境暴露，Mock 与本地都测不到；UM-AC-01/05 等写路径验收会在集成日集体失败 |
| B. 刷新类型 + 在 `http.ts` 的改状态分支统一注入 `X-CSRF-Token` | 1 个 `readCookie()`，无新依赖 | 头名与 cookie 名是 S1 配置项，写死会在改名时静默失效 |
| C. 引入 `openapi-fetch` 生成的类型化客户端 | 新依赖 + 全部调用点重写 | 本轮改动面已经很大；S5 用这条路，S5-M 没有理由在同一年同时换两套请求层 |

## 决策

取 B。`readCookie()` 只读 S1 明确设计为可读的 `factory_csrf`（会话本身是 httpOnly，前端永不读取），cookie 名与头名都放进 `src/config/runtime.ts`（`csrfCookieName` / `csrfHeader`，默认值与 S1 `config.py:102-103` 一致）由部署侧注入，与 §6.11「`servers` 留空由部署侧注入」同构。

`fetch` 的 `credentials` **保持默认 `same-origin`**：S5-M 与 S1 同域部署（UM-COMP-004）、开发期由 Vite 代理 `/api/v1`，改成 `include` 反而会撞 `SameSite=Strict`。这一条写进注释，防止后人"顺手修"。

`AppError` 增 `isCsrfFailed`，`errors.CSRF_FAILED` 补进两个语言包（`parity.test.ts` 会同时把关）。`verify-live` 补一个"改状态请求不带 CSRF 头"的探测，与既有的 `OPERATOR_REQUIRED` 探测同构。

## 验收对应

- UM-IF-001/002：`npm run codegen`（契约来源唯一）、`src/api/api.test.ts` 的错误映射
- 写路径不回归：`tests/e2e/clickBudget.spec.ts` 全部流程都是写操作，403 会直接表现为预算失败

## 后果

正面：S1 启用会话鉴权那天不需要紧急改前端；契约与生成类型回到同一版本。

负面：多读一次 `document.cookie`（每次改状态请求），可忽略。

债务：契约副本仍需人工 `cp`，没有 CI 保证它与 S1 发布版本同步——本轮无 CI（SRS UM-NFR-05 假定的 CI 在本仓库不存在，已记入汇报的文档问题）。

## 复审触发

- S1 契约的 `info.version` 主版本变化（破坏性变更须记 CHANGELOG，按 §6.11）；
- S1 放弃 cookie 会话、只留 bearer（则 `csrfCookieName` 注入为空，注入分支自动失效，本 ADR 可归档）；
- 引入类型化生成客户端时（届时 `readCookie` 应移入客户端拦截器而非 `http.ts`）。
