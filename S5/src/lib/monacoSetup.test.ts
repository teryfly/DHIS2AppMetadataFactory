/*
 * UI-MAN-002: the JSON editor has to be configured against S2's section Schema. The editor itself
 * is Monaco (not mountable in jsdom), so what is tested here is the wiring: the model path the
 * schema attaches to, and that a schema is handed to Monaco's json defaults rather than dropped.
 */
import { describe, it, expect, vi } from 'vitest';
import { applySectionSchema, sectionModelPath, validateJson } from './monacoSetup';
import { SECTION_BY_STAGE_TYPE } from '@/features/workspace/ManualAuthoring';

function fakeMonaco() {
  const setDiagnosticsOptions = vi.fn();
  return {
    setDiagnosticsOptions,
    monaco: { languages: { json: { jsonDefaults: { setDiagnosticsOptions } } } } as never,
  };
}

describe('section schema wiring (UI-MAN-002)', () => {
  it('attaches the schema to the model path the editor opens', () => {
    const { monaco, setDiagnosticsOptions } = fakeMonaco();
    applySectionSchema(monaco, 'requirements', { type: 'object' });
    const options = setDiagnosticsOptions.mock.calls[0]?.[0] as {
      validate: boolean;
      schemas: { uri: string; fileMatch: string[] }[];
    };
    expect(options.validate).toBe(true);
    expect(options.schemas[0]?.fileMatch).toEqual([sectionModelPath('requirements')]);
  });

  it('maps only the stages that really edit a section', () => {
    expect(SECTION_BY_STAGE_TYPE['REQUIREMENTS']).toBe('requirements');
    // DISCOVERY reads the environment; handing its content a requirements schema would mark
    // correct JSON as invalid, so an unmapped stage gets no schema at all.
    expect(SECTION_BY_STAGE_TYPE['DISCOVERY']).toBeUndefined();
    expect(SECTION_BY_STAGE_TYPE['COMMIT']).toBeUndefined();
  });

  it('still reports a syntax error for text that is not JSON', () => {
    expect(validateJson('{"a":1,}')).not.toBeNull();
    expect(validateJson('{"a":1}')).toBeNull();
  });
});
