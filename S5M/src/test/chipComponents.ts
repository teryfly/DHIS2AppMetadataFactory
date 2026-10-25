// Re-exported by the `@/fui` mocks in the tests that stub the FUI barrel to keep Vant out of jsdom.
// These two choice groups are plain buttons with no Vant behind them, so replacing them with a stub
// would only hide the option list a test believes it pressed.
export { default as FuiSegmented } from '@/fui/FuiSegmented.vue';
export { default as FuiCheckChips } from '@/fui/FuiCheckChips.vue';
