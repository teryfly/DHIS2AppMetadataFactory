# 005 · UM-TECH-001 的机器强制：Vant 去全局化与双防线

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端

## 背景

UM-TECH-001 规定「页面层禁止直接引用第三方 UI 组件库；只能引用 FUI」，§4 技术选型表写明 Vant 4 由自研 FUI 层包装、「页面只使用 FUI 组件，不直接引用 Vant」。上一轮实现并没有遵守：`src/main.ts` 用 `app.use(Vant)` 全局注册整个库，页面与外壳里散布 **87 处** Vant 直接引用（84 个 `<van-*>` 标签 + 2 处 `:deep(.van-*)` 样式泄漏 + 1 处全局导入），分布在 22 个文件。后果不只是合规问题：

- 首屏 `vant` 块 = 整库 **77.68 KB gzip**，`index` CSS = **55.9 KB gzip**，UM-NFR-02 的 200 KB 首屏预算被一个库吃掉近 40%；
- 页面可以随意使用未过设计令牌与无障碍约束的组件（本轮就发现 `FuiButton` 缺 `label` prop，导致全站 70 处 `:label="…"` 按钮实际渲染为空文本，无障碍与可用性双重损失——顺手在 `FuiButton` 补上 `label` 并让默认插槽优先）。

## 备选方案

| 候选 | 首屏 JS（实测 gzip） | 首屏 CSS（实测 gzip） | 风险 |
| --- | --- | --- | --- |
| A. 保持 `app.use(Vant)`，只加 ESLint 规则 | 182.2 KB | 55.9 KB | 违规可挡，体积与令牌失控照旧 |
| B. `unplugin-vue-components` + `@vant/auto-import-resolver` | — | — | 它转译**所有** `.vue`，会在页面里自动注入 `van-*`，把 UM-TECH-001 的口子重新打开；且解析结果不出现在源码里，评审看不见用了什么 |
| C. FUI 补齐 14 个原语 + 每个包装文件内 `import { X } from 'vant'` + `import 'vant/es/x/style'`，去掉全局注册 | **123.6 KB** | **41.6 KB** | 迁移期任一页面漏改就是运行时 unknown component 空白渲染（不是编译错误）；Vant 的共享 base/工具 CSS 不随 JS 一起被 tree-shake |
| D. 弃用 Vant，自研全部控件 | — | — | 超出本轮范围，且 §4 明写 Vant 4 为选型 |

实测数据来自 `npm run build && npm run check:budget`：`vant` 块 77.68 → **14.25 KB**，`index` 37.5、`vue` 60.6、`query` 11.2，首屏合计 182.2 → **123.6 KB gzip**（距 200 KB 上限余 76.4 KB，为 CodeMirror 等懒加载能力腾出空间）；首屏 CSS 55.9 → 41.6 KB，剩余部分主要是 Vant 的 `style/base.css` 与工具类（约 16 KB 属各控件自身，其余为共享基础样式），按 JS 侧同等粒度裁剪不可能，因为样式是副作用导入。

## 决策

取 C，并配**双防线**，因为单靠一条都不足：

1. **ESLint**（`eslint.config.js`，写作 `error`）：`vue/no-restricted-syntax` 匹配 `VElement[name=/^van-/i]` 覆盖模板里的 `<van-x>` 与 `<VanX>`；`vue/no-restricted-class` 挡 `class="van-*"`；`no-restricted-imports` 在 `src/!(fui)/**` 禁 `vant`/`vant/**`——这条是关键，防止有人用 `import { Field } from 'vant'` 绕过标签规则。
2. **Vitest**（`src/test/fuiPurity.test.ts`）：ESLint 看不见的东西由它挡——`FuiCounterBadge` 之类不含标签文本的用法、样式里的 `.van-` 选择器、以及 `main.ts` 是否又出现全局注册。它同时是 **ratchet**：`VAN_BACKLOG` 记录当前残留数，只允许下调，降到 0 时终态断言自动启用。

配套：新增 14 个 FUI 原语（`FuiField`/`FuiCell`/`FuiIcon`/`FuiRadioGroup`/`FuiRadio`/`FuiCheckboxGroup`/`FuiCheckbox`/`FuiSwitch`/`FuiSearchField`/`FuiDialog`/`FuiOverlayPanel`/`FuiSpinner`/`FuiProgressBar`/`FuiCounterBadge`），barrel 由 28 增至 42 个导出。页面之所以当初直接抓 Vant，很大程度是 FUI 没有对应控件——补原语是清偿的前提，不是附加工作。

迁移顺序上把 ADR-004 的 M4 页面删除放在清偿之前，白减 6 处；四个即将承接功能开发的页面（GraphView、ManualAuthoring、ProjectDetailView、ApiReviewView）只做标签替换，功能改动留在各自里程碑里一次做完，避免二次触碰。

## 验收对应

- UM-TECH-001：`src/test/fuiPurity.test.ts`（ratchet 归零 + 终态断言）、`npm run lint`（已并入 `npm run verify`，此前 `verify` 不跑 lint，防线是装饰性的）
- UM-TECH-002：`fuiPurity.test.ts` 的重型内核检查（`src/fui` 顶层与 barrel 不得静态导入 codemirror/ajv/jsonc-parser/js-yaml/@antv）
- UM-NFR-02：`npm run check:budget` → `docs/reports/bundle-budget.md`
- 清偿进度：`npm run report:fui` → `docs/reports/fui-purity.md`（87 → 0，逐文件表）

## 后果

正面：UM-TECH-001 由「不合规」转为机器可检的合规；首屏 JS 少 58.6 KB、CSS 少 14.3 KB；设计令牌与无障碍（44px 触控目标、aria 角色）第一次真正贯穿到表单与弹层；`FuiButton` 的空文本缺陷修复，70 处按钮恢复可读文本。

负面：`src/fui` 的导出面变大（42 个），后续新增控件时要先问「FUI 有没有」而不是「Vant 有没有」；迁移期若漏改一处，症状是运行时空白而不是编译错误（靠 lint + 纯度测试兜住，而不是类型系统）。

债务：Vant 共享基础样式（约 41.6 KB 中的大部分）仍随首个使用 FUI 控件的入口进入首屏，本轮未解；若 UM-NFR-01 的 FCP 在真机不达标，这里是第一个要动的地方（候选：把 `vant/es/style/base.css` 里的工具类按需重写进 tokens）。

## 复审触发

- 任何页面再次出现 `van-*`（lint 会拦，但拦不住注释掉的尝试）：说明有人选择绕过 FUI，须重判包装层是否缺件；
- 引入第二个第三方 UI 库（图或 API 文档能力可能诱发）：本 ADR 的「只有 src/fui 可引用 UI 库」边界必须扩展并重新论证；
- 首屏 CSS 成为 FCP 瓶颈：按上述债务项处理，必要时把 `base.css` 依赖拆开。
