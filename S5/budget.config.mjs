// UI-NFR-06 machine-readable budget. Edit deliberately: every number and every exemption here is a
// transcription of a clause in the S5 SRS, so the list is auditable rather than inferred from file
// names. Measured values are recorded in docs/reports/bundle-budget.md by the check itself.
export default {
  // "首屏 JavaScript（gzip）≤ 200 KB，单个功能块 ≤ 300 KB"
  firstScreenJsBytes: 204_800,
  lazyChunkBytes: 307_200,
  // Not a UI-NFR-06 clause: first-screen CSS is what the operator paints before any route chunk
  // arrives, so it gets its own ceiling to keep the number from drifting back toward the whole
  // component library.
  firstScreenCssBytes: 120_000,
  // "（编辑器、图库除外，它们仅在需要时加载）" — a named allow-list, printed on every run so an
  // exempt chunk cannot grow unnoticed. Patterns are anchored to the chunk names they mean: a loose
  // /Flow/ here matched `useProjectWorkflow` and exempted a chunk that had nothing to do with the
  // graph library.
  exempt: [
    { pattern: /^monaco[-.]|monaco-/i, reason: 'UI-NFR-06：Monaco 编辑器块' },
    { pattern: /^ObjectGraph[-.]|^xyflow[-.]/i, reason: 'UI-NFR-06：对象依赖图（React Flow）' },
  ],
};
