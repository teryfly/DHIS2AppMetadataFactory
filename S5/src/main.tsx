import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/global.css';
import './i18n'; // initialise i18next (UI-APP-002) before first render
import { App } from './App';
import { loadRuntimeConfig } from './config/runtime';

async function bootstrap(): Promise<void> {
  const config = loadRuntimeConfig();

  // UI-NFR-05: boot the in-browser mock (with SSE simulation) when enabled, before render.
  if (config.enableMock) {
    const { startMockWorker } = await import('./mocks/browser');
    await startMockWorker();
  }

  const container = document.getElementById('root');
  if (!container) throw new Error('missing #root');
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
