/*
 * UI-APP-005: runtime config is injected (window.__FACTORY_CONFIG__) and never baked; URLs are
 * derived for REST, openapi-fetch (which must not double the /api/v1 prefix) and SSE replay.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { loadRuntimeConfig, mergeConfig, openapiBaseUrl, restUrl, eventsUrl, __resetRuntimeConfigForTest } from './runtime';

afterEach(() => __resetRuntimeConfigForTest());

describe('runtime config', () => {
  it('reads injected window config over defaults', () => {
    const w = globalThis as unknown as { window: { __FACTORY_CONFIG__?: Record<string, unknown> } };
    w.window.__FACTORY_CONFIG__ = { apiBaseUrl: 'https://s1.example' };
    __resetRuntimeConfigForTest();
    expect(loadRuntimeConfig().apiBaseUrl).toBe('https://s1.example');
    delete w.window.__FACTORY_CONFIG__;
  });

  it('openapiBaseUrl does not append the /api/v1 prefix (paths already carry it)', () => {
    __resetRuntimeConfigForTest({ apiBaseUrl: 'https://s1.example' });
    expect(openapiBaseUrl()).toBe('https://s1.example');
  });

  it('openapiBaseUrl falls back to same origin when empty (tests + same-origin prod)', () => {
    __resetRuntimeConfigForTest({ apiBaseUrl: '' });
    expect(openapiBaseUrl()).toBe(globalThis.location.origin);
  });

  it('eventsUrl carries the replay-after sequence (UI-RT-001)', () => {
    __resetRuntimeConfigForTest({ apiBaseUrl: '', sseBaseUrl: '', apiPathPrefix: '/api/v1' });
    // Same-origin → a relative stream URL (valid for EventSource).
    expect(eventsUrl('WF-1', 42)).toBe('/api/v1/workflows/WF-1/events?after=42');
    expect(eventsUrl('WF 2')).toContain('/workflows/WF%202/events');
    // Explicit SSE base is honoured verbatim.
    __resetRuntimeConfigForTest({ apiBaseUrl: '', sseBaseUrl: 'https://stream.example', apiPathPrefix: '/api/v1' });
    expect(eventsUrl('WF-1')).toBe('https://stream.example/api/v1/workflows/WF-1/events');
  });

  it('restUrl keeps the api prefix once', () => {
    __resetRuntimeConfigForTest({ apiBaseUrl: 'https://s1.example', apiPathPrefix: '/api/v1' });
    expect(restUrl('/api/v1/projects')).toBe('https://s1.example/api/v1/projects');
    expect(restUrl('projects')).toBe('https://s1.example/api/v1/projects');
  });
});

describe('the built-in mock cannot be switched off by the served config (UI-NFR-05)', () => {
  it('keeps enableMock on when the bundle was built with it, and still takes the other values', () => {
    // regression: public/config.js ships enableMock:false, which used to win over the build-time
    // flag, so the mock-enabled e2e build talked to a real S1 through the preview proxy
    const merged = mergeConfig(
      { apiBaseUrl: '', sseBaseUrl: '', apiPathPrefix: '/api/v1', enableMock: true, traceCorrelation: true },
      { apiBaseUrl: 'http://s1:8000', enableMock: false },
    );
    expect(merged.enableMock).toBe(true);
    expect(merged.apiBaseUrl).toBe('http://s1:8000');
  });

  it('lets a deployment turn the mock on for a bundle built without it', () => {
    const merged = mergeConfig(
      { apiBaseUrl: '', sseBaseUrl: '', apiPathPrefix: '/api/v1', enableMock: false, traceCorrelation: true },
      { enableMock: true },
    );
    expect(merged.enableMock).toBe(true);
  });
});
