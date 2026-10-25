/*
 * Browser mock bootstrap (UI-NFR-05). Only imported dynamically when the runtime config or
 * VITE_ENABLE_MOCK turns the mock on; excluded from the production bundle otherwise.
 */
import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';
import { installMockEventSource, type MockSseControl } from './events';

export const worker = setupWorker(...handlers);

let sseControl: MockSseControl | null = null;

export async function startMockWorker(): Promise<void> {
  await worker.start({
    onUnhandledRequest: 'warn',
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
  });
  sseControl = installMockEventSource();
  console.info('[Factory UI] MSW mock active. S1 is faked locally.');
}

export function mockSse(): MockSseControl | null {
  return sseControl;
}
