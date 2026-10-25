import { describe, expect, it } from 'vitest';
import { formatBytes, looksTextual } from './media';

describe('looksTextual — what this device can actually render', () => {
  it('renders what the media type says is text', () => {
    for (const type of ['text/plain', 'text/markdown', 'text/csv', 'application/json', 'application/yaml', 'image/svg+xml']) {
      expect(looksTextual(type, 'doc')).toBe(true);
    }
  });

  it('refuses the office and image formats a phone cannot decode here', () => {
    for (const type of [
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/pdf',
      'image/png',
      'application/zip',
    ]) {
      expect(looksTextual(type, 'doc')).toBe(false);
    }
  });

  it('falls back to the extension when the server only heard octet-stream', () => {
    expect(looksTextual('application/octet-stream', '需求表.csv')).toBe(true);
    expect(looksTextual('application/octet-stream', 'spec.md')).toBe(true);
    expect(looksTextual('application/octet-stream', 'scan.jpg')).toBe(false);
    expect(looksTextual('', '')).toBe(false);
  });

  it('never treats a multipart body as one document', () => {
    expect(looksTextual('multipart/form-data', 'x.txt')).toBe(false);
  });
});

describe('formatBytes', () => {
  it('uses the unit an operator compares files in', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(formatBytes(undefined)).toBe('');
  });
});
