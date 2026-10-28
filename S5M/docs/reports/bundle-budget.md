# 首屏与分块包体（UM-NFR-02）

生成：`npm run check:budget`（gzip -9，读 dist/index.html 的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 <style>/<script>）。

| 指标 | 实测 | 上限 | 判定 |
| --- | --- | --- | --- |
| 首屏 JS | 162.4 KB | 200.0 KB | ✓ |
| 首屏 CSS | 37.2 KB | 43.0 KB（FCP 护栏） | ✓ |
| 懒加载块 | 75 个，最大 176.6 KB | 150.0 KB | ✓ |

按名豁免（UM-NFR-02「CodeMirror、G6、Scalar 除外」的可审计转写）：

| 块 | gzip | 豁免理由 |
| --- | --- | --- |
| JsonEditorCore-CER5c1sv.js | 176.6 KB | UM-NFR-02：CodeMirror 编辑器块 |
| OpenApiDoc-C5UrShJ6.js | 6.1 KB | UM-NFR-02：懒加载 API 文档视图 |

首屏明细：

| 文件 | gzip |
| --- | --- |
| index-Bhh6KRzS.js | 73.6 KB |
| vue-DMXEO6A6.js | 59.8 KB |
| query-CDcVN-ho.js | 11.2 KB |
| vant-CDz_yMsH.js | 17.3 KB |
