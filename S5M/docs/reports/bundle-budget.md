# 首屏与分块包体（UM-NFR-02）

生成：`npm run check:budget`（gzip -9，读 dist/index.html 的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 <style>/<script>）。

| 指标 | 实测 | 上限 | 判定 |
| --- | --- | --- | --- |
| 首屏 JS | 157.3 KB | 200.0 KB | ✓ |
| 首屏 CSS | 37.2 KB | 43.0 KB（FCP 护栏） | ✓ |
| 懒加载块 | 71 个，最大 176.6 KB | 150.0 KB | ✓ |

按名豁免（UM-NFR-02「CodeMirror、G6、Scalar 除外」的可审计转写）：

| 块 | gzip | 豁免理由 |
| --- | --- | --- |
| JsonEditorCore-cIFEx0jo.js | 176.6 KB | UM-NFR-02：CodeMirror 编辑器块 |
| OpenApiDoc-C-XDocJv.js | 6.1 KB | UM-NFR-02：懒加载 API 文档视图 |

首屏明细：

| 文件 | gzip |
| --- | --- |
| index-CvLh3EII.js | 68.4 KB |
| vue-BaGnRkma.js | 59.9 KB |
| query-CdC0XRe6.js | 11.2 KB |
| vant-B_0uKX1c.js | 17.3 KB |
