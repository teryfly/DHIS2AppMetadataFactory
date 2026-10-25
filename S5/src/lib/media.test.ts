import { describe, expect, it } from 'vitest';
import { looksTextual } from './media';

describe('looksTextual — what this console can render', () => {
  it('renders what the media type says is text', () => {
    for (const type of ['text/plain', 'text/markdown', 'text/csv', 'application/json', 'application/yaml', 'image/svg+xml']) {
      expect(looksTextual(type, 'doc')).toBe(true);
    }
  });

  it('refuses the office and image formats it cannot decode', () => {
    for (const type of [
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/pdf',
      'image/png',
      'application/zip',
      'multipart/form-data',
    ]) {
      expect(looksTextual(type, 'spec.md')).toBe(false);
    }
  });

  it('falls back to the extension when the server only heard octet-stream', () => {
    expect(looksTextual('application/octet-stream', '需求表.csv')).toBe(true);
    expect(looksTextual('application/octet-stream', 'scan.jpg')).toBe(false);
    expect(looksTextual('', '')).toBe(false);
  });
});
