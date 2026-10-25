# 009 · 首屏渲染与对比度：调色板重取、Vant 主题覆盖顺序、壳层按文件引入

状态
: 通过

日期
: 2026-10-01

决策者
: S5-M 前端（判据为 UM-NFR-04 的 Lighthouse 阈值与 WCAG 2.1 AA，非偏好）

## 背景

**UM-NFR-04** 要求 Lighthouse 移动端 性能 ≥ 85、无障碍 ≥ 95、最佳实践 ≥ 90；**UM-NFR-01** 要求 4G 模拟下 FCP < 2 s、LCP < 2.5 s。
本轮第一次真实审计（`docs/reports/lighthouse-mobile.json`）给出 无障碍 84、最佳实践 96，丢分项是可定位到 DOM 的四类，而非「氛围不对」：

| 审计 | 丢分原因 | 证据 |
| --- | --- | --- |
| `color-contrast` | `--fui-primary` 取 `#1677ff`（对白 4.10）；Vant 主按钮实际渲染 `#1989fa`（对白 3.50） | Lighthouse 明细列出 `.van-button__text`、`.home__todo-num`、`.shell__tab-label` |
| `aria-command-name` | 顶栏两个可点 `<FuiIcon>` 有 `role=button` 却无可读名称 | `van-icon-manager-o` / `van-icon-search` 的 snippet |
| `aria-dialog-name` | Vant `Dialog.mjs:210` 把**标题文本**写进 `aria-labelledby`，引用不存在的元素 | 操作者对话框 `aria-labelledby="我是谁"` |
| `aria-required-children` | 底部导航是 `<nav role=tablist>`，子元素却是带 `aria-current` 的 `<a>` | `.shell__tabbar` |
| `errors-in-console` | `/favicon.ico` 404 每次加载都发生 | 最佳实践 96 的唯一来源 |

`#1989fa` 那一行还有第二层原因：`vant/es/style/base.css` 在 `:root` 上声明 `--van-primary-color: var(--van-blue)`，
我们的 `tokens.css` 同样写 `:root`，而 Vant 的样式由每个 FUI 包装文件引入、在打包产物里排在 `tokens.css` **之后**——
同特异度后者胜出，于是主题映射长期静默失效。这不是审美问题，是层叠顺序 bug。

## 备选方案

| 候选 | 实测/依据 | 判定 |
| --- | --- | --- |
| 只改文案、不动颜色 | 无障碍仍 84 | 否决：AA 是算术，不是解释 |
| 用 `!important` 或把 `tokens.css` 移到入口最后引入 | 移动引入顺序能修好本轮产物，但任何新增 FUI 文件都会把它推回去 | 否决：依赖打包顺序的修复下次还会坏 |
| 提高 Vant 映射块特异度为 `html:root`（0,1,1 > `:root` 0,1,0） | 与引入顺序无关；暗色块同步改为 `html:root[data-theme='dark']`（0,2,1）保持覆盖 | **取** |
| 逐个组件写 `:deep()` 覆盖按钮底色 | 需覆盖 button/switch/dialog/toast/progress 等 6+ 处，且新组件必然漏 | 否决 |
| 颜色按「肉眼更深一点」调 | 无法预测比值 | 否决：改为用相对亮度公式对 `#ffffff`/`#f5f6f7`/`#f2f3f5` 与四个语义淡底逐一带验，见下表 |

重取后的最小达标调色板（对比度按 WCAG 相对亮度公式计算，非目测）：

| token | 旧值 | 新值 | 对白 | 对 `--fui-bg` | 对自身淡底 |
| --- | --- | --- | --- | --- | --- |
| `--fui-primary` | `#1677ff` 4.10 | `#0e5fd6` | 5.79 | 5.35 | — |
| `--fui-primary-hover` | `#3c8cff` 3.29 | `#1468d8` | 5.25 | 4.85 | — |
| `--fui-primary-active` | `#0e5fd6` | `#0a52bb` | 7.15 | 6.61 | — |
| `--fui-success` | `#16a34a` 3.30 | `#0f7a35` | 5.45 | 5.03 | `#e7f6ec` 4.87 |
| `--fui-warning` | `#e08600` 2.77 | `#b45309` | 5.02 | 4.64 | `#fdf3e2` 4.57 |
| `--fui-danger` | `#dc2626` 4.83 | `#c81e1e` | 5.74 | 5.30 | `#fdeaea` 4.95 |
| `--fui-info` | `#2563eb` 5.17 | `#1d4ed8` | 6.70 | 6.19 | `#e9effe` 5.82 |
| `--fui-text-secondary` | `#646a73` 5.45 | `#4f565f` | 7.42 | 6.86 | — |
| `--fui-text-tertiary` | `#8f959e` 3.02 | `#67707b` | 5.02 | 4.64 | — |

同一条改动链上还做了三件与首屏直接相关的事，各自的可测结果：

| 改动 | 动机 | 实测 |
| --- | --- | --- |
| `index.html` 内联 SVG favicon | 消除 `/favicon.ico` 404 | 最佳实践 96 → 100 |
| `/config.js` 改 `defer` | 它是同步阻塞脚本，多占一次串行 RTT | Lighthouse `render-blocking-insight` 中 `config.js` 项消失 |
| `#app` 内预渲染骨架 | 空容器使 FCP 必须等 JS 执行完 | FCP 由「等 JS」变为「等 CSS」，`bootup-time` 不再出现在 FCP 之前 |
| 壳层改为按文件引入 FUI | `@/fui` 桶文件把 30+ 个原语连同各自的 `vant/es/x/style` 副作用全部拉进入口块 | 首屏 CSS 41.9 → **37.1 KB** gzip（`docs/reports/bundle-budget.md`） |

## 决策

无障碍按 AA 比值重取语义色并统一走 `html:root` 主题映射；Vant 的 `aria-labelledby` 缺陷在 `FuiDialog` 内用 `aria-label` 补齐；
`FuiIcon` 承担「可点即有名且可键盘激活」的责任；底部导航放弃 `role=tablist`；首屏用内联骨架提前 FCP、`config.js` 改 `defer`、
壳层绕开桶文件以缩小渲染阻塞的 CSS。理由：这五处的判据都是机器可复核的比值或审计项，而不是主观印象。

## 验收对应

- UM-NFR-04 无障碍 ≥ 95、最佳实践 ≥ 90 → `docs/reports/lighthouse-mobile.md`（实测 100 / 100），承载文件 `scripts/lighthouse.mjs`；
- UM-UX-002 键盘可达 → `src/fui/FuiIcon.vue` 的 `@keydown.enter/space` 合成点击；
- UM-UI-001 自持设计令牌 → `src/fui/tokens.css`（令牌即对比度的唯一真相：全仓 `.vue` 里只有 2 处裸色值，均为叠在主色/警告色上的 `#fff`，白对 `#0e5fd6` 5.79、白对 `#b45309` 5.02）；
- UM-TECH-001 只用 FUI → `src/test/fuiPurity.test.ts`（壳层改为按文件引入不放宽任何一条：`vant` 仍禁止出现在 `src/fui` 之外）。

## 后果

- 正面：无障碍与最佳实践两类审计不再依赖机器负载，属于可当场签署的证据；主题映射不再依赖打包顺序；首屏 CSS 少 4.8 KB。
- 负面：主色明显变深，与 S5 桌面端的 `#1677ff` 不再逐像素一致——桌面端若不跟进重取，跨端品牌色会有可见差异。
- 留下的债务：`performance` 项在本机仍无法作证（2 核、`load1` 常为核数的 2–25 倍，同产物 TBT 2.1 s ↔ 29.3 s），
  `docs/reports/lighthouse-mobile.json` 的 `host.performanceAttestable` 会如实标出；冷启动的操作者对话框仍是 LCP 元素。

## 复审触发

- 在任何一台 `load1 < 核数` 的机器上重跑 `npm run report:lighthouse`：若性能 < 85 而 `performanceAttestable` 为真，本篇的 CSS/JS 收益估计作废，需重新分解；
- S5 桌面端调整品牌色或引入共享令牌包时，本篇的调色板必须与之合并；
- Vant 修复 `Dialog.mjs` 的 `aria-labelledby`（或升级到已修版本）后，`FuiDialog` 里的 `aria-label` 补偿应删除；
- 若产品决定把 UI-OP-001 的操作者询问从模态改为内联提示，LCP 一项需重测并可能推翻本篇的骨架收益评估。
