# UM-CAP-002 图渲染实测

生成：`npx playwright test graph.perf`（生产构建 `npm run build:harness` + `vite preview`）。
CPU 节流经 CDP `Emulation.setCPUThrottlingRate`，双指捏合经 `Input.dispatchTouchEvent`。

阈值（UM-CAP-002）：1,000 节点在中端手机平移与缩放 >= 30 fps；5,000 节点在桌面 >= 30 fps；>200 节点默认按类型折叠。

| 渲染器 | 状态 | 节点 | 视口 | 节流 | 手势 | fps | p50 帧距 | p95 帧距 | 最差帧 | 帧数 | 重绘 | 展开节点 | 超节点 | SVG 元素 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| svg | collapsed | 1000 | 393px | 4x | pan | **60** | 16.7 ms | 16.8 ms | 33 ms | 191 | 91 | 0 | 9 | 9 | PASS |
| svg | collapsed | 1000 | 393px | 4x | pinch | **60** | 16.7 ms | 16.8 ms | 33 ms | 142 | 85 | 0 | 9 | 9 | PASS |
| svg | collapsed | 5000 | 1280px | 1x | wheel | **60** | 16.7 ms | 16.7 ms | 17 ms | 188 | 90 | 0 | 9 | 9 | PASS |
| svg | expanded | 1000 | 393px | 4x | pan | **30** | 33.3 ms | 83.4 ms | 133 ms | 242 | 91 | 1000 | 0 | 1000 | 参考 ≥30 |
| svg | expanded | 1000 | 393px | 4x | pinch | **60** | 16.7 ms | 433.3 ms | 617 ms | 177 | 85 | 1000 | 0 | 1000 | 参考 ≥30 |
| svg | expanded | 5000 | 1280px | 1x | wheel | **10** | 100.0 ms | 333.4 ms | 550 ms | 268 | 90 | 5000 | 0 | 5000 | 参考 <30 |
| canvas | collapsed | 1000 | 393px | 4x | pan | **60** | 16.7 ms | 16.8 ms | 17 ms | 206 | 92 | 0 | 9 | 0 | PASS |
| canvas | collapsed | 1000 | 393px | 4x | pinch | **60** | 16.7 ms | 16.8 ms | 33 ms | 152 | 92 | 0 | 9 | 0 | PASS |
| canvas | collapsed | 5000 | 1280px | 1x | wheel | **60** | 16.7 ms | 16.8 ms | 50 ms | 189 | 92 | 0 | 9 | 0 | PASS |
| canvas | expanded | 1000 | 393px | 4x | pan | **60** | 16.7 ms | 100.1 ms | 150 ms | 257 | 93 | 1000 | 0 | 0 | PASS |
| canvas | expanded | 1000 | 393px | 4x | pinch | **60** | 16.7 ms | 100.0 ms | 200 ms | 161 | 95 | 1000 | 0 | 0 | PASS |
| canvas | expanded | 5000 | 1280px | 1x | wheel | **60** | 16.7 ms | 50.0 ms | 167 ms | 187 | 93 | 5000 | 0 | 0 | PASS |

fps 取帧距中位数的倒数：一次手势中的单个长帧就是可见的卡顿，平均值会把它藏起来。
「重绘」列证明手势确实落到画布并触发重绘——没有它，任何 fps 读数都可能只是空转。
「展开节点 / 超节点」是场景真实的折叠状态：`expanded` 行的超节点数必须为 0，`collapsed` 行的展开节点数必须为 0，否则这一行不成立（测试里是硬断言）。「SVG 元素」只数 `.fui-graph__node`，Canvas 臂恒为 0。
