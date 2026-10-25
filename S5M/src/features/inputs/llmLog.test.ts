/*
 * S5IF-INP08/INP09 on the mobile console: the attempt list, and the request that is fetched
 * only when the operator expands an attempt. The page is what makes a parse failure diagnosable
 * while it is still running, so the in-flight request is part of the fixture.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createMemoryHistory, createRouter } from 'vue-router';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { i18n } from '@/i18n';
import LlmLogView from './LlmLogView.vue';

const summary = {
  exchangeId: 'aexg-1',
  sequence: 1,
  status: 'OK',
  provider: 'mock',
  providerClass: 'LOCAL_ONLY',
  model: 'mock-agent',
  requestId: 'req-1',
  inputTokens: 12,
  outputTokens: 34,
  latencyMs: 1200,
  createdAt: new Date().toISOString(),
};

const log = {
  parseStatus: 'PARSED',
  parseRunId: 'ipr-1',
  total: 1,
  limit: 50,
  offset: 0,
  items: [summary],
};

const detail = {
  ...summary,
  inputArtifactId: 'INP-1',
  request: {
    messages: [{ role: 'system', content: 'system rules', images: [] }],
    schemaName: 'extraction',
    temperature: 0,
    maxOutputTokens: 8000,
  },
  response: {
    text: '{"items": []}',
    finishReason: 'stop',
    model: 'mock-agent',
    requestId: 'req-1',
    usage: { inputTokens: 12, outputTokens: 34 },
    storageTruncated: false,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('The LLM conversation log (S5IF-INP08)', () => {
  it('lists the attempt and fetches the conversation when expanded', async () => {
    i18n.global.locale.value = 'en';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.endsWith('/llm-log')
          ? log
          : url.includes('/llm-log/')
            ? detail
            : { items: [], total: 0, limit: 50, offset: 0 };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:id/inputs/:artifactId/llm-log', component: LlmLogView },
      ],
    });
    await router.push('/projects/PRJ-1/inputs/INP-1/llm-log');
    await router.isReady();

    const wrapper = mount(LlmLogView, {
      global: {
        plugins: [
          i18n,
          router,
          [
            VueQueryPlugin,
            { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) },
          ],
        ],
      },
    });
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain('LLM conversation'));
    expect(wrapper.text()).toContain('#1');
    expect(wrapper.text()).toContain('1.2 s');

    const show = wrapper.findAll('button').find((button) => button.text().includes('Show conversation'));
    expect(show).toBeTruthy();
    await show!.trigger('click');
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain('system rules'));
    expect(wrapper.text()).toContain('{"items": []}');
  });
});
