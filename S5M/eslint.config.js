import pluginVue from 'eslint-plugin-vue';
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript';

// UM-TECH-001: the page layer may only use FUI components, never a third-party UI library
// directly. The two rules below are the load-bearing pair — the tag rule catches `<van-x>` in a
// template, and no-restricted-imports catches `import { Field } from 'vant'` used to dodge it.
//
// M3 burn-down complete: all 87 sites are gone (see docs/reports/fui-purity.md) and
// src/test/fuiPurity.test.ts asserts zero, so this is `error`.
const FUI_SEVERITY = 'error';

const FUI_ONLY = {
  'vue/no-restricted-syntax': [
    FUI_SEVERITY,
    { selector: 'VElement[name=/^van-/i]', message: 'UM-TECH-001：页面只能使用 FUI 组件（src/fui），不得直接引用 Vant' },
  ],
  'vue/no-restricted-class': [FUI_SEVERITY, '/^van-/'],
};

// UM-TECH-002: heavy capabilities load per route. Their kernels and the pure modules they alone
// consume live under these directories; importing one from anywhere else would drag it into the
// first-screen graph and break UM-NFR-02.
const HEAVY_DIRS = ['src/fui/internal/**', 'src/lib/json/**', 'src/lib/openapi/**', 'src/lib/graph/**'];
const HEAVY_SPECIFIERS = ['codemirror', '@codemirror/*', 'ajv', 'ajv-formats', 'jsonc-parser', 'js-yaml', '@antv/*'];

// UM-REV-005: nothing in this app may reach a target DHIS2 instance, directly or proxied. The
// only outbound primitives allowed are the three files below; src/test/isolation.test.ts is the
// enforcement of record (ESLint cannot see window.fetch), this is the fast feedback.
const S1_ONLY = {
  'no-restricted-globals': ['error', 'fetch', 'XMLHttpRequest', 'EventSource'],
  'no-restricted-properties': [
    'error',
    { object: 'navigator', property: 'sendBeacon', message: 'UM-REV-005/UM-IF-001：只允许经 src/api 调 S1' },
  ],
};

const OUTBOUND_ALLOW_LIST = ['src/api/http.ts', 'src/api/upload.ts', 'src/sse/transport.ts'];

export default defineConfigWithVueTs(
  { ignores: ['dist/**', 'dist-e2e/**', 'node_modules/**', 'src/api/generated/**', 'coverage/**', 'dev-dist/**', 'test-results/**', 'playwright-report/**'] },
  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommended,
  {
    rules: {
      'vue/multi-word-component-names': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'vue/attributes-order': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/first-attribute-linebreak': 'off',
    },
  },
  // Vant may be referenced only inside the FUI layer, and only from the file that wraps it.
  {
    files: ['src/!(fui)/**/*.{ts,vue}', 'src/main.ts', 'src/App.vue'],
    rules: {
      ...FUI_ONLY,
      'no-restricted-imports': [
        FUI_SEVERITY,
        {
          paths: ['vant', 'vant/lib', 'vant/lib/index.css'],
          patterns: [{ group: ['vant', 'vant/**'], message: 'UM-TECH-001：只有 src/fui 下的组件可以引用 vant' }],
        },
      ],
    },
  },
  {
    files: ['src/**/*.{ts,vue}'],
    ignores: HEAVY_DIRS,
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: HEAVY_SPECIFIERS,
              message: 'UM-TECH-002：重型内核只能由懒加载核心（src/fui/internal）或其专用纯逻辑模块引用',
              allowTypeImports: true,
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/**/*.{ts,vue}'],
    ignores: OUTBOUND_ALLOW_LIST,
    rules: S1_ONLY,
  },
);

