import { afterEach } from 'vitest';
import { config } from '@vue/test-utils';

// jsdom lacks these APIs that Vant / our layout rely on.
if (!window.matchMedia) {
  window.matchMedia = () =>
    ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }) as unknown as MediaQueryList;
}
if (!('IntersectionObserver' in globalThis)) {
  globalThis.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof IntersectionObserver;
}
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

config.global.stubs = {};

afterEach(() => {
  // reset DOM between tests
});
