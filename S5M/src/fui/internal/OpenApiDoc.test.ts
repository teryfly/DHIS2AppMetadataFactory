import { describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { mount } from '@vue/test-utils';
import OpenApiDoc from './OpenApiDoc.vue';
import FuiOpenApiViewer from '../FuiOpenApiViewer.vue';
import { i18n, LOCALES } from '@/i18n';
import { stageTypeLabel } from '@/lib/labels';

/**
 * UM-CAP-003 component acceptance.
 *
 * Three things are checked here that the pure-module suite cannot see: the document really renders
 * on desktop, the schema tree opens under a keyboard-reachable `<button>` (and only then pays for
 * its children), and every label the viewer asks for exists in *both* locales — a missing key would
 * otherwise show a dotted path to a reviewer instead of a sentence.
 */

const DOC = `
openapi: 3.1.0
info: { title: ANC API, version: 2.1.0 }
tags: [ { name: patients, description: Register pages } ]
paths:
  /trackedEntityInstances:
    post:
      operationId: createPatient
      summary: Create one patient
      tags: [patients]
      x-registry-refs: [tei:Patient]
      parameters:
        - { name: fu, in: query, required: true, schema: { type: string } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/Patient" }
      responses:
        "201":
          description: created
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Patient" }
components:
  securitySchemes:
    apiToken: { type: apiKey, in: header, name: Authorization }
  schemas:
    Patient:
      type: object
      required: [id]
      properties:
        id: { type: string }
        address:
          type: object
          properties:
            city: { type: string }
            district:
              type: object
              properties: { name: { type: string } }
`;

const label = (key: string): string => String(i18n.global.t(key));

// The guided empty state resolves its stage through vue-query and the router; both are outside what
// this file is about, so they are stubbed the way `FuiGuidedEmpty.test.ts` stubs them.
vi.mock('@/lib/stageJump', () => ({
  useRequiredStageJump: () => ({
    to: ref('/stages/s-1'),
    stageType: ref('BASELINE_COMPILE'),
    available: ref(false),
    pending: ref(false),
  }),
}));
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }));

function render() {
  return mount(OpenApiDoc, { props: { doc: DOC, projectId: 'p-1' } });
}

describe('UM-CAP-003 · desktop document view', () => {
  it('lists operations by operationId under their tag, and marks the surface read-only', () => {
    const text = render().text();
    expect(text).toContain('createPatient');
    expect(text).toContain('patients');
    expect(text).toContain(label('api.doc.readOnly'));
    expect(text).toContain(label('api.doc.derivedExample'));
    // The document declares no 4xx/5xx response, and that is stated rather than invented.
    expect(text).toContain(label('api.doc.noErrorResponses'));
  });

  it('opens a nested schema through a real button with an accessible name', async () => {
    const w = render();
    const rows = () => w.findAll('.oast__name').map((n) => n.text());
    // Two trees are on screen (request body and the 201 response), each materialised one level deep:
    // `address` is a row, `city` and `district` are not — they exist only in the derived example text.
    expect(rows()).toEqual(['requestBody', 'id', 'address', '201', 'id', 'address']);

    const twisty = w
      .findAll('button[aria-expanded]')
      .find((b) => String(b.attributes('aria-label') ?? '').includes('address'));
    expect(twisty, 'a twisty button named after the node must exist').toBeTruthy();
    expect(twisty!.attributes('aria-expanded')).toBe('false');
    await twisty!.trigger('click');
    expect(twisty!.attributes('aria-expanded')).toBe('true');
    // Only the branch that was opened grows; the response tree keeps its own collapsed state.
    expect(rows()).toEqual(['requestBody', 'id', 'address', 'city', 'district', '201', 'id', 'address']);
  });

  it('keeps the shell empty below the desktop breakpoint, where the page shows cards instead', () => {
    // `isDesktop` is a VueUse breakpoint; jsdom reports 1024x768 viewport-less, i.e. not desktop.
    const shell = mount(FuiOpenApiViewer, { props: { doc: DOC } });
    expect(shell.text()).toBe('');
  });

  it('hands a missing artifact to the guided empty state, not a blank panel', () => {
    const w = mount(OpenApiDoc, { props: { doc: null, projectId: 'p-1' } });
    const expected = String(i18n.global.t('notGenerated.awaitingStage', { stage: stageTypeLabel('BASELINE_COMPILE') }));
    expect(w.text()).toContain(expected);
  });
});

describe('UM-CAP-003 · every viewer label exists in both locales', () => {
  const SOURCES = [
    'src/fui/FuiOpenApiViewer.vue',
    'src/fui/internal/OpenApiDoc.vue',
    'src/fui/internal/OpenApiSchemaTree.vue',
    'src/features/review/ApiReviewView.vue',
    'src/features/review/ApiDocView.vue',
  ];

  const keys = new Set<string>();
  for (const file of SOURCES) {
    const body = readFileSync(join(process.cwd(), file), 'utf8');
    for (const match of body.matchAll(/'((?:api|common|stageTypes)\.[A-Za-z0-9_.]+)'/g)) keys.add(match[1]);
  }

  // Interpolated keys are spelled from a token, so their members are named rather than scraped.
  const dynamic = [
    'api.doc.marker.recursive',
    'api.doc.marker.depth',
    'api.doc.marker.nodeCap',
    'api.doc.marker.unresolved',
    'api.doc.parse.empty',
    'api.doc.parse.unparseable',
    'api.doc.parse.shape',
    'api.doc.warning.specVersion',
    'api.doc.warning.specVersionUnknown',
    'api.doc.warning.noPaths',
    'api.doc.warning.noOperations',
  ];

  it('the viewer asks for a usable number of labels', () => {
    expect(keys.size).toBeGreaterThan(8);
  });

  it.each([...keys, ...dynamic])('%s resolves to text in zh-CN and en', (key) => {
    for (const locale of Object.keys(LOCALES)) {
      const bag = LOCALES[locale as keyof typeof LOCALES] as unknown as Record<string, unknown>;
      const value = key.split('.').reduce<unknown>((acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined), bag);
      expect(typeof value, `${locale} · ${key}`).toBe('string');
      expect((value as string).length, `${locale} · ${key}`).toBeGreaterThan(0);
    }
    expect(i18n.global.te(key)).toBe(true);
  });
});
