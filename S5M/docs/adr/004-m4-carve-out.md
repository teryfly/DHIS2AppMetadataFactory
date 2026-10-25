# 004 · §1.4 范围裁剪：MVP 移除成员、受限授权与生产豁免页面

状态
: 被取代（复审触发条件成立：M4 已交付，三页按 S1 契约重写，见下）

日期
: 2026-10-01

决策者
: S5-M 前端（范围依据 00 文档 D-16 与总体 SRS 的 MVP 决定）

## 背景

S5-M SRS §1.4「范围外」明确写：登录与权限界面，以及项目成员、受限产物授权、生产豁免的页面「随 M4 引入，届时另行补充；**MVP 期间即使 S1 已提供相应端点也不实现这些页面**（S1 CP-UI-023）」。**UM-AC-21** 的判据是「MVP 版本检查导航与路由 → 不存在成员、授权、豁免页面」（里程碑 M1）。

00 文档 D-16 给出理由：M1～M3 不实现认证、角色、成员与权限，状态变更以 `X-Operator` 标识，M4 之前不设任何访问保护（风险已由项目方接受）。S1 CP-UI-023 则规定这些端点属 M4，提前实现时写操作必须在 `allowed-actions` 中不可用（`FEATURE_NOT_ENABLED`）、直接调用返回 409。

上一轮的实现把这三类页面做进来了：`/access` 一个路由里塞六个 tab（session / users / roles / members / grants / exemptions），并配了 `useProjectMembers`、`useArtifactGrants`、`useProductionExemptions` 三个查询与六个 mutation。也就是说 UM-AC-21 当时是**不通过**的。

## 备选方案

| 候选 | 代价 | 风险 |
| --- | --- | --- |
| A. 隐藏入口但保留代码与路由（注释掉 tab 项） | 改动最小 | UM-AC-21 判据是「不存在页面及其路由」，深链 `/access?tab=members` 仍可用；`noUnusedLocals` 使被注释代码的依赖变成编译错误 |
| B. 删除三个 tab 及其查询、mutation、类型别名、i18n 键与联调探测 | 约 130 行删除 | 需要 M4 时重写；但重写依据是 S1 契约而非本地记忆，成本可控 |
| C. 连同 session/users/roles 一起删 | 更大 | 超出 UM-AC-21 点名的三个页面；S1 一旦开启会话鉴权，删掉登录面板会把用户永久锁在门外（功能回归） |

## 决策

取 B。删除范围（一次提交，可整体 revert）：

- `src/features/access/AccessView.vue` 的 `members`/`grants`/`exemptions` 三个 tab、对应模板块（原 L178-225）、其状态与提交函数，以及仅这三页使用的项目范围选择器（`scope`/`scopeOpen`/`scopeOptions`/`pickScope` + scope 选择面板）和随之失去调用方的 `useRevokeRole`/`submitRevoke`；
- `src/api/queries.ts` 的 `useProjectMembers`、`useArtifactGrants`、`useProductionExemptions`；
- `src/api/mutations.ts` 的 `useSetMembers`、`useRemoveMember`、`useCreateGrant`、`useRevokeGrant`、`useCreateExemption`、`useRevokeExemption`；
- `src/api/types.ts` 的九个 `Member*/Grant*/Exemption*` 别名——删别名（而非删生成文件里的类型）使「页面无法触达该词表」成为**编译期**事实，而不是运行约定；
- 两个语言包的 `access.members/addMember/removeMember/grants/exemptions/scopeProject`（`parity.test.ts` 保证两语同时改）；
- `scripts/verify-live.mjs` 的三条对应探测。

保留 `/access` 路由与 session / users / roles 三个 tab、侧栏与「我的」入口，按 §1.4 的字面指示只移除点名三类页面。

## 验收对应

- UM-AC-21（本轮判据为「不存在」）：`src/test/m4Absence.test.ts`（路由表、features/layouts 源码、api 层导出、两语言包、视图 tab 五路检查）；`tests/e2e/m4Absence.spec.ts`（移动与桌面两种视口渲染出的导航/tab 列表）
  —— 两个文件随 M4 交付**改名并反向**为 `src/test/m4Presence.test.ts` / `tests/e2e/m4Presence.spec.ts`，检查同一组事实的反面（三页存在、路由懒加载且文件在、写入走 `FuiActionButton`、幂等意图含请求体摘要、环境键取自 `GET /environments`）；本节保留原文件名以记录当时的判据。
- 不回退：`src/i18n/parity.test.ts`、`npm run typecheck`

## 后果

正面：UM-AC-21 由「不通过」转为机器可检的通过；`src/fui` 之外的 `van-field`/`van-cell` 泄漏随这三页的删除少掉 10 处，直接减轻 M3 的清偿量。

负面：M4 落地时这三页要重写。缓解方式是删的是**代码**而不是**知识**——端点形态在 S1 契约 §8.1 与生成类型里仍然完整存在（`components.schemas.MemberList` 等仍保留在 `src/api/generated/schema.d.ts`），重写只是把别名和视图加回来。

债务：`useAssignRole` 的失效键里仍包含 `['projects', id, 'members']`（角色分配对成员缓存的清理）。这是一个无副作用的缓存 hygiene，但它是代码中残留的最后一处 members 词表，M4 恢复成员页时必须一并复核。

**已记录的张力**：§1.4 的原文是「登录与**权限界面**，以及…」，字面上也覆盖本视图保留的 users/roles 两个 tab 与登录面板，而 UM-AC-21 只点名三个页面。本轮按指示保留它们，并把该判读差异作为下述复审触发条件显式挂起，而不是等下一位评审者当成缺陷重新发现。

## 复审触发

- S1 进入 M4（成员/授权/豁免端点从 409 `FEATURE_NOT_ENABLED` 转为可用）：本 ADR 归档，三页按新契约重写；
- 项目方裁定 §1.4 的「权限界面」按字面覆盖 users/roles：则本视图收缩为纯登录面板，`/access` 侧栏入口随 §1.4 一并移除；
- 任何人在 MVP 期间以「S1 已经有端点」为理由把这三页加回来：视为违反 §1.4，须先改判本 ADR。
