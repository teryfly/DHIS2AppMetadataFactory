# UM-TECH-001 清偿进度（页面层 Vant 泄漏）

生成：`npm run report:fui`。计数口径：`src/fui` 之外的每一处 `<van-*>` 标签、`:deep(.van-*)` 选择器或 `vant` 导入算一处；
闭合标签与开标签是同一处，不重复计数。`src/fui` 内部引用 Vant 是设计允许的（FUI 就是那层包装）。

**当前剩余：0 处**

（已全部清偿）

防线：`eslint.config.js`（`vue/no-restricted-syntax` + `no-restricted-imports`，归零后由 warn 翻 error）
与 `src/test/fuiPurity.test.ts` 的 ratchet 常数。
