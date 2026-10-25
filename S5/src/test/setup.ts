/*
 * Vitest global setup: jest-dom matchers + MSW server for hermetic data-layer tests
 * (UI-NFR-03/04/05). Every test starts from the same seed fixtures.
 */
import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import { server } from '@/mocks/server';
import { __resetRuntimeConfigForTest } from '@/config/runtime';

// Workspace routes resolve S1 through a chained read (project → workflow → stage) which can exceed
// the 1s default under a loaded CI box; give async queries headroom to avoid timing flakes.
configure({ asyncUtilTimeout: 8000 });

// jsdom lacks these; the app and DHIS2 components reference them.
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom reports no layout, so an observer would never fire: the app's measured widgets (the object
// graph's canvas, monaco's automatic layout) call back on a zero-size rect, which is enough for a
// unit test to reach their markup.
class StubResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    const entry = {
      target,
      contentRect: { width: 800, height: 600 } as DOMRectReadOnly,
      borderBoxSize: [{ blockSize: 600, inlineSize: 800 }],
      contentBoxSize: [{ blockSize: 600, inlineSize: 800 }],
      devicePixelContentBoxSize: [{ blockSize: 600, inlineSize: 800 }],
    } as unknown as ResizeObserverEntry;
    this.callback([entry], this as unknown as ResizeObserver);
  }

  unobserve() {}
  disconnect() {}
}

if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === 'undefined') {
  Object.defineProperty(globalThis, 'ResizeObserver', { writable: true, value: StubResizeObserver });
}
if (typeof (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver === 'undefined') {
  class StubIntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  Object.defineProperty(globalThis, 'IntersectionObserver', { writable: true, value: StubIntersectionObserver });
}

// jsdom has no EventSource; the workspace opens an SSE stream. A dormant stub keeps realtime code
// paths importable in unit tests (the browser mock installs a functional one separately).
if (typeof (globalThis as { EventSource?: unknown }).EventSource === 'undefined') {
  class StubEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readyState = 0;
    onopen: ((ev: Event) => void) | null = null;
    onerror: ((ev: Event) => void) | null = null;
    onmessage: ((ev: MessageEvent) => void) | null = null;
    addEventListener() {}
    removeEventListener() {}
    close() {}
  }
  (globalThis as { EventSource?: unknown }).EventSource = StubEventSource;
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => {
  server.close();
  __resetRuntimeConfigForTest();
});
