# S5-M 架构决策记录（ADR）

本目录是 S5-M SRS **UM-TECH-003** 所要求的「仓库的架构决策记录」。SRS 只规定了选型变更要记录在仓库里、不必改文档，未规定目录与格式，约定由本仓库确立如下。

## 什么要写 ADR

- SRS 第 4 章标注为「推荐」的技术选型被替换或采纳时（UM-TECH-003 的三类重型能力尤其如此）；
- 验收标准出现「允许自研 / 允许第三方」分叉，需要用实测数据收敛时（UM-CAP-001～004）；
- 引入临时兼容（对 S1 契约缺位的适配）且该兼容必须被删除时；
- 范围裁剪（§1.4 页面移除、点击预算导致的流程改动）。

## 编号与命名

`NNN-kebab-case-title.md`，三位递增，`000` 留给模板。状态变化开新文件取代旧的（`状态: 被 NNN 取代`），不改写历史。

## 模板段落（固定九段，`src/test/adr.test.ts` 机器校验）

| 段落 | 要求 |
| --- | --- |
| `状态` | 提议 / 通过 / 已废弃 / 被 NNN 取代 之一 |
| `日期` | ISO `YYYY-MM-DD` |
| `决策者` | 人名或「S5-M 前端」 |
| `背景` | 必须引用 SRS 条款号（UM-*、UM-AC-*、CP-*） |
| `备选方案` | 每个候选都要带体积或性能实测数据，不接受「较小/较快」 |
| `决策` | 一句话结论 + 理由 |
| `验收对应` | UM-AC-* 编号 + 承载它的测试文件路径 |
| `后果` | 正面 / 负面 / 留下的债务 |
| `复审触发` | 什么条件成立时这篇必须重写 |

## 索引

| 编号 | 标题 | 状态 | 日期 | 关联条款 |
| --- | --- | --- | --- | --- |
| [001](001-json-editor-codemirror-ajv.md) | JSON 编辑器：CodeMirror 6 + Ajv 2020 + jsonc-parser | 通过 | 2026-10-01 | UM-CAP-001、UM-AC-17、UM-TECH-002/003、UM-NFR-02 |
| [002](002-graph-renderer-svg-measured-then-canvas.md) | 依赖图渲染器：先实测 SVG，再落 Canvas | 通过 | 2026-10-01 | UM-CAP-002、UM-AC-18、UM-TECH-003 |
| [003](003-openapi-viewer-self-built.md) | OpenAPI 文档视图：自研 Schema 树，不引入 Scalar | 通过 | 2026-10-01 | UM-CAP-003、UM-REV-005、UM-AC-20 |
| [004](004-m4-carve-out.md) | §1.4 范围裁剪：MVP 移除成员、受限授权、生产豁免 | 通过 | 2026-10-01 | §1.4、UM-AC-21、CP-UI-023、D-16 |
| [005](005-fui-only-enforcement.md) | UM-TECH-001 的机器强制：Vant 去全局化与双防线 | 通过 | 2026-10-01 | UM-TECH-001/002、UM-NFR-02 |
| [006](006-s1-contract-compat-and-not-generated.md) | S1 契约缺位的临时兼容与 NOT_GENERATED 判定 | 通过 | 2026-10-01 | UM-REV-007、UM-AC-19/20、CP-UI-020/021/022 |
| [007](007-openapi-contract-refresh-and-csrf.md) | 契约刷新与 X-CSRF-Token | 通过 | 2026-10-01 | UM-IF-001/002、00 文档 6.11 |
| [008](008-click-budget-flow-changes.md) | 点击预算导致的流程与步骤改动 | 通过 | 2026-10-01 | §6.1、UM-NFR-06 |
| [009](009-contrast-and-first-paint.md) | 首屏渲染与对比度：调色板重取、Vant 主题覆盖顺序、壳层按文件引入 | 通过 | 2026-10-01 | UM-NFR-01/02/04、UM-UX-002、UM-UI-001 |

## 再生成

`docs/reports/` 下的实测数据是本目录「备选方案」段的数据来源，生成命令见 `docs/reports/README.md`。ADR 里的数字必须由这些命令产生，不得手写。
