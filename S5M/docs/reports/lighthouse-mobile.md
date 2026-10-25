# Lighthouse 移动端（UM-NFR-04 / UM-NFR-01）

生成：`npm run report:lighthouse`；目标 http://127.0.0.1:4173/；Chrome `/home/ubuntu/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`；Lighthouse 13.5.0；
节流 Lighthouse 内置 4G 模拟（1.6 Mbps / RTT 150 ms）；取 3 次运行的中位数。
测量机：2 核，load1 3.54 / load5 3.35，空闲内存 771 MB / 1968 MB。
load1 已达 2 核的 1.8 倍 —— **本机无法为性能分作证**（同一产物三次独立测量的 TBT 曾从 2.1 s 跳到 29.3 s），需在安静机器上重跑；无障碍与最佳实践是 DOM/CSS 审计，不受负载影响。

| 类别 | 得分 | 阈值 | 判定 |
| --- | --- | --- | --- |
| performance | 82 | ≥ 85 | FAIL |
| accessibility | 100 | ≥ 95 | PASS |
| best-practices | 100 | ≥ 90 | PASS |

FCP 3219 ms · LCP 3524 ms · TBT 40 ms · CLS 0

UM-NFR-01 要求 4G 模拟下 FCP < 2 s、LCP < 2.5 s：FAIL。

未达 0.9 的审计项（丢分来源）：

| 审计 | 得分 | 标题 |
| --- | --- | --- |
| first-contentful-paint | 0.42 | First Contentful Paint |
| largest-contentful-paint | 0.63 | Largest Contentful Paint |
| speed-index | 0.66 | Speed Index |
| mainthread-work-breakdown | 0 | Minimize main-thread work |
| bootup-time | 0 | Reduce JavaScript execution time |
| unused-css-rules | 0.5 | Reduce unused CSS |
| unused-javascript | 0 | Reduce unused JavaScript |
| bf-cache | 0 | Page prevented back/forward cache restoration |
| forced-reflow-insight | 0 | Forced reflow |
| network-dependency-tree-insight | 0 | Network dependency tree |
| render-blocking-insight | 0.5 | Render-blocking requests |
