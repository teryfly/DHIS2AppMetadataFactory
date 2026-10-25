/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>;
  export default component;
}

declare global {
  interface ImportMetaEnv {
    readonly VITE_API_BASE_URL?: string;
    readonly VITE_API_PATH_PREFIX?: string;
    readonly VITE_S1_PROXY?: string;
    readonly VITE_ENABLE_PWA?: string;
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }

  interface Window {
    __FACTORY_CONFIG__?: {
      apiBaseUrl?: string;
      sseBaseUrl?: string;
      apiPathPrefix?: string;
      traceCorrelation?: boolean;
    };
  }
}

export {};
