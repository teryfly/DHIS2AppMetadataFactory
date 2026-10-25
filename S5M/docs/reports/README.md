# 本轮实测证据

每个文件都由命令生成，不手写。ADR 里引用的数字必须能在下面找到来源。

| 文件 | 生成命令 | 覆盖的验收 |
| --- | --- | --- |
| `bundle-budget.md` / `.json` | `npm run build && npm run check:budget` | UM-NFR-02 首屏 ≤ 200 KB、懒加载块 ≤ 150 KB（按名豁免并打印体积）；首屏集合 = `dist/index.html` 引用的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 `<style>/<script>` |
| `fui-purity.md` / `.json` | `npm run report:fui` | UM-TECH-001（87 → 0 逐文件清偿表） |
| `graph-bench.md` / `.json` | `npm run report:graph` | UM-AC-18 / UM-CAP-002（两渲染臂 × 折叠/展开 × 平移/捏合/滚轮；含「重绘次数」列以证明手势真实落到画布） |
| `click-budget.md` / `.json` | `npm run test:budget` | §6.1 的 8 条流程 × 移动与桌面两种视口（UM-NFR-06） |
| `um-ac-matrix.md` | `npm run report:ac` | UM-AC-01..35 → 证据文件（路径不存在即失败） |
| `json-editor-latency.json` | `npm run test:capabilities` | UM-CAP-001 的 5,000 行输入延迟（逐击键帧间隔中位数与 P95，另附 longtask 上下文） |
| `lighthouse-mobile.md` / `.json` | `npm run report:lighthouse` | UM-NFR-04 性能/无障碍/最佳实践，UM-NFR-01 的 FCP/LCP |

`playwright.config.ts` 的 `webServer` 会拉起 `scripts/mock-s1.mjs` + `vite preview`；`report:lighthouse` 先 `npm run build`，
并在审计前比对"服务端入口 hash == dist 里的入口 hash"，避免对着陈旧或 dev-harness 产物出报告（这两种错误都真实发生过）。

## 结论摘要

- **无障碍 100 / 最佳实践 100**：DOM 与 CSS 审计不受机器负载影响，可采信。
- **性能 82 / 阈值 85**（3 次运行中位数：FCP 3219 ms、LCP 3524 ms、TBT **40 ms**、CLS 0）：本轮把首次审计的 28 分推到 82 分，
  丢分只剩 FCP 一项，而 FCP 与 `elementRenderDelay 2201 ms` 同步——`mainthread-work-breakdown` 显示 3.4 s（脚本求值 1.35 s、样式与布局 0.8 s），
  即在本机上它是 CPU 等待而不是字节等待（关键路径资源在 181 ms 内全部下载完毕）。
  `host.performanceAttestable` 为假（load1 3.54 / 2 核），所以这 82 分同样要在安静机器上复测才能签署。
- 可采信的替代证据是字节数与结构：首屏 JS 128.1 KB、CSS 37.1 KB（`bundle-budget.md`，UM-NFR-02 上限 200 KB / 43 KB），
  关键路径深度为 1 个 HTML + 1 个样式表 + 4 个 modulepreload。
- LCP 元素是冷启动的操作者对话框（`div.van-dialog__content > .shell__name-why`）。UI-OP-001 要求「首次使用时要求填写操作者名称」，
  改成非模态的内联提示会同时改善 LCP 与脚本求值，但那是交互决策，未在本轮擅自改动。

## 已知待办

- 在 ≥ 4 核、`load1 < 核数` 的机器上重跑 `npm run report:lighthouse`，为 UM-NFR-04 的性能项与 UM-NFR-01 的 FCP/LCP 签署。
- 若产品同意把 UI-OP-001 的操作者询问改为内联提示，重测 LCP。
- SVG 降级臂在全部展开时实测 12 fps（5,000 节点），需要视口裁剪或禁止降级臂全部展开——见 `docs/adr/002` 的更正记录与债务段。
