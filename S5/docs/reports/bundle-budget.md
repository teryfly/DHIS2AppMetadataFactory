# 首屏与分块包体（UI-NFR-06）

生成：`npm run check:budget`（gzip -9，读 dist/index.html 的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 <style>/<script>）。

| 指标 | 实测 | 上限 | 判定 |
| --- | --- | --- | --- |
| 首屏 JS | 174.6 KB | 200.0 KB | ✓ |
| 首屏 CSS | 6.6 KB | 117.2 KB | ✓ |
| 懒加载块 | 63 个，最大 112.7 KB | 300.0 KB | ✓ |

按名豁免（UI-NFR-06「编辑器、图库仅在需要时加载」的可审计转写）：

| 块 | gzip | 豁免理由 |
| --- | --- | --- |
| ObjectGraph-CvmKcaS7.js | 59.9 KB | UI-NFR-06：对象依赖图（React Flow） |
| monaco-NdBD_j2R.js | 4.9 KB | UI-NFR-06：Monaco 编辑器块 |

首屏明细：

| 文件 | gzip |
| --- | --- |
| index-B-nv3ruk.js | 129.6 KB |
| react-CRncvnw5.js | 44.4 KB |
