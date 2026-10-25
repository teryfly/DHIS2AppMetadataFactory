// UM-NFR-02 machine-readable budget. Edit deliberately: every exemption here is a transcription of
// a clause in the S5-M SRS, so the list is auditable rather than inferred from file names.
export default {
  // "首屏 JavaScript（gzip）≤ 200 KB"
  firstScreenJsBytes: 204_800,
  // "单个懒加载路由块 ≤ 150 KB"
  lazyChunkBytes: 153_600,
  // Not an UM-NFR-02 clause: first-screen CSS drives UM-NFR-01 FCP < 2 s on 4G, so it gets its own
  // ceiling. Measured after M3 (Vant de-globalisation): 55.9 KB -> 41.6 KB gzip. The residue is
  // Vant's shared base/utilities CSS, which arrives with the first FUI component that needs it and
  // is not tree-shakeable the way JS is (see docs/reports/bundle-budget.md). UM-NFR-02 caps JS only;
  // this ceiling exists to stop that number regressing back toward the whole library.
  firstScreenCssBytes: 44_000,
  // "（CodeMirror、G6、Scalar 除外，它们仅在需要时加载）" — named allow-list, printed on every run
  // so an exempt chunk cannot grow unnoticed.
  exempt: [
    { pattern: /JsonEditorCore/, reason: 'UM-NFR-02：CodeMirror 编辑器块' },
    { pattern: /OpenApiDoc/, reason: 'UM-NFR-02：懒加载 API 文档视图' },
    { pattern: /GraphRenderer/, reason: 'UM-NFR-02：图渲染器（Canvas/SVG 内核）' },
  ],
};
