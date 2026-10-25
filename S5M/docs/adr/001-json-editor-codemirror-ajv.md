# 001 · JSON 编辑器：CodeMirror 6 + Ajv 2020 + jsonc-parser

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端

## 背景

S5-M SRS **UM-CAP-001**（§4.1，M1）要求桌面端 JSON 编辑器具备：语法高亮；按 S2 分节 Schema 实时校验并把错误定位到**行列与字段路径**且在编辑器内标注；基于 Schema 的**键名与枚举值补全**；格式化；与表单双向同步且**不丢失未知字段**；5,000 行文档输入延迟 < 100 ms。同一条明确写着「**普通 textarea 不满足本条**」，而上一轮的实现正是 `<textarea>` + `JSON.parse`（`ManualAuthoring.vue` 旧 L150-156）。**UM-AC-17** 的判据是「输入一个不在 Schema 中的键 → 行内标注错误并给出字段路径；输入键名首字母时出现补全建议」。

§4 技术选型表推荐 CodeMirror 6（按需加载，「比 Monaco 轻，支持触屏」），UM-TECH-003 允许替换但必须先过 4.1 的能力验收，UM-TECH-002 要求按路由懒加载。

## 备选方案

体积为各包 ESM 入口经 `esbuild --minify | gzip -9` 的实测值（单包数，合并共享依赖后总量约低 5–8%）：

| 候选 | 体积（min+gz） | 关键缺陷 |
| --- | --- | --- |
| `monaco-editor` 0.52 | 约 1.4 MB 级 | 桌面 S5 用的就是它；UM-NFR-02 首屏 200 KB 预算下不可接受，且 Worker 装载链在 PWA 里复杂 |
| `codemirror` 元包 | 额外拖入 `@lezer/javascript`、`language-data` | 预设包含我们不需要的 JS 语言与搜索栈，无法逐个取舍 |
| **`@codemirror/*` 组合**（state 15.6 + view 63.7 + language 13.8 + commands 9.7 + lint 6.4 + autocomplete 13.5 + lang-json 0.5，单位 KB） | **≈ 131 KB** | 需要自己拼装扩展，约 200 行装配代码 |
| `jsoneditor` | 未取 | 提供树编辑而非文本编辑，与「分节 JSON 文本 + 表单双视图」的形态不符 |

校验器（S2 分节文档是 Draft 2020-12，`sections.json` 全部 17 个分节都写 `additionalProperties: false`，并把 118 处引用内联到 `#/definitions/*`）：

| 候选 | 体积 | 判定 |
| --- | --- | --- |
| **`ajv@8` + `ajv/dist/2020` + `ajv-formats`** | ≈ 40 KB | **取**。编译成代码，是唯一能在防抖后重校验 5,000 行还保住 <100 ms 输入预算的实现；错误的 `instancePath`（JSON Pointer）+ `params.additionalProperty`（越界键名）正是 UM-AC-17 要的两件信息；自包含 `#/definitions/*` 零解析器配置 |
| `@cfworker/json-schema` | ≈ 11 KB | 有 `instanceLocation`，但为解释器，5,000 行慢一个量级，直接危及延迟条款 |
| `@exodus/schemasafe` | ≈ 9 KB | 最小，但错误是纯字符串，需要自己重建指针 → 失去意义 |
| `jsonschema`（ICI） | — | 交叉 `$ref` 与错误路径都弱 |

文本↔路径桥另取 `jsonc-parser`（≈ 13 KB）而非直接走 `@lezer/json` 语法树：需要三件 Lezer 不给的东西——**错误恢复**（`parse(text, errors, {allowTrailingComma})` 对半截文档仍给出部分值，于是可以校验已写出的部分而不是拒绝回答）、`getLocation/offset` 与 `parseTree`+`findNodeAtOffset` 的**偏移↔指针**双向映射、以及 `format()` 直接给出格式化编辑。自写这部分约 150 行且会在 `lang-json` 升级时碎掉。

## 决策

CodeMirror 6 组合包 + Ajv 2020 + jsonc-parser，共 ≈ 184 KB gzip 进入一个懒加载块 `JsonEditorCore`。

分层是为了同时满足 UM-TECH-001 与 UM-TECH-002：`src/fui/FuiJsonEditor.vue` 是页面唯一可见的组件，内部用 `defineAsyncComponent(() => import('./internal/JsonEditorCore.vue'))`；**全仓只有 `src/fui/internal/` 与 `src/lib/json|openapi|graph/` 可以静态导入这些重包**，由 `eslint.config.js`（`no-restricted-imports`）与 `src/test/fuiPurity.test.ts` 双重把关。移动端不装编辑器，退到 `FuiReadOnlyViewer` + 「JSON 编辑请在电脑端继续」。

<100 ms 的实现要点（都在 `JsonEditorCore.vue`）：击键路径零 JSON 工作；语法先行、语法有错则跳过语义校验；`@codemirror/lint` 的 `delay` 防抖；文档内容未变复用上一轮结果；Ajv 编译按 `$id` 缓存；格式化走单个 `ChangeSet`（一步可撤销）；不在每次击键 `structuredClone` 整个 model（表单侧 `FuiSchemaForm` 原本如此，桥接层未继承）。

双向同步不丢未知字段的规则在 `src/lib/json/modelBridge.ts`：model 是唯一真相，文本是其投影；编辑器→model 仅在解析成功时整体替换（文本本就由含未知字段的完整 model 生成，故不会丢），model→编辑器仅在改动不来自编辑器且缓冲不脏时下发；脏缓冲遇到服务端草稿到达时不覆盖而请读者选择。**修掉的既有丢数据点**：旧 `enterForm()` 会清空 `jsonErr` 并沿用陈旧 model。

顺带修 **B5**：`resolveSchema.ts` 是编辑器与 `FuiSchemaField` 共用的 `$ref` 解析器，此前表单完全不解析 `$ref`。

## 验收对应

- UM-AC-17：`src/lib/json/json.test.ts`（越界键定位到键节点且带 `additionalProperty`、字段路径、`presentKeys`、枚举候选）；`tests/e2e/jsonEditor.spec.ts`（真实浏览器里的行内标注与补全弹层，jsdom 无法测 CM6 布局）
- UM-CAP-001 的 5,000 行延迟条款：`tests/e2e/jsonEditor.spec.ts` 的 longtask 采样
- UM-TECH-002：`src/test/fuiPurity.test.ts`；UM-NFR-02：`npm run check:budget`（`JsonEditorCore` 在按名豁免白名单里，体积被打印出来）

## 后果

正面：UM-CAP-001 由「明确不达标（textarea）」变为可实现且可测；表单第一次能渲染 `$ref` 共享类型。

负面：新增 11 个运行时依赖；CM6 在 jsdom 下不可测，编辑器的交互验收必须有 Playwright，而这台机器此前没有 e2e 基础设施（本轮建立）。

债务：`docs/reports/` 里的 longtask 延迟数字尚未纳入 `npm run verify`（需要 e2e 跑通才能自动化，见 §6.1 预算套件的同一前置）。

## 复审触发

- S1 分节 Schema 从 Draft 2020-12 换掉（`$schema` 值变化）→ Ajv 实例入口需同步；
- 首屏 JS 预算收紧到装不下 184 KB 懒加载块 → 重新取舍 `@codemirror/search`（−7.2 KB，无验收影响）；
- S2 分节文档改为外部 `$ref`（不再自包含）→ 必须引入离线解析策略，本 ADR 的「零解析器配置」前提失效。
