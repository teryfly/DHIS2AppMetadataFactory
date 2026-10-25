import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadInput } from './upload';
import { LS, writeJSON } from '@/lib/storage';

// A bare `XMLHttpRequest` is the only browser API that reports upload progress, so this one call
// leaves the JSON client and re-attaches by hand the headers that client injects. jsdom's own XHR
// cannot show what a browser would reject (a non-latin1 header value throws inside
// `setRequestHeader`), so the headers are read off a recording fake instead.
class FakeXHR {
  static last: FakeXHR | null = null;
  headers: Record<string, string> = {};
  status = 201;
  responseText = '{"artifactId":"art-1"}';
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
  onload: (() => void) | null = null;
  open(): void {}
  setRequestHeader(key: string, value: string): void {
    this.headers[key] = value;
  }
  send(): void {
    FakeXHR.last = this;
    this.onload?.();
  }
  abort(): void {}
}

function sentHeaders(): Record<string, string> {
  expect(FakeXHR.last).not.toBeNull();
  return FakeXHR.last!.headers;
}

beforeEach(() => {
  FakeXHR.last = null;
  vi.stubGlobal('XMLHttpRequest', FakeXHR);
  document.cookie = 'factory_csrf=tok-9';
  writeJSON(LS.operator, '测试员');
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.cookie = 'factory_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
});

describe('UM-BR-04 · 上传带着与 JSON 客户端相同的横切头', () => {
  it('echoes the CSRF cookie and encodes a non-latin1 operator name', async () => {
    await uploadInput('prj-1', new File(['a'], 'x.csv', { type: 'text/csv' }), {
      mediaType: 'text/csv',
      dataClassification: 'INTERNAL',
    });
    expect(sentHeaders()).toMatchObject({
      // S1 refuses a cookie-authenticated change without it (rest/middleware.py) — the upload was
      // failing with 403 CSRF_FAILED while the progress bar still ran to 100%.
      'X-CSRF-Token': 'tok-9',
      'X-Operator': encodeURIComponent('测试员'),
      'Accept-Language': 'zh-CN',
    });
  });

  it('sends no Idempotency-Key: the upload route declares none, and a spent key would block the retry', async () => {
    await uploadInput('prj-1', new File(['a'], 'x.csv', { type: 'text/csv' }), {
      mediaType: 'text/csv',
      dataClassification: 'INTERNAL',
    });
    expect(sentHeaders()['Idempotency-Key']).toBeUndefined();
  });

  it('carries the classification and PHI declaration in the multipart body, not in a header', async () => {
    let form: FormData | null = null;
    class RecordingXHR extends FakeXHR {
      override send(body?: FormData): void {
        form = body ?? null;
        super.send();
      }
    }
    vi.stubGlobal('XMLHttpRequest', RecordingXHR);
    await uploadInput('prj-1', new File(['a'], 'x.md', { type: 'text/markdown' }), {
      mediaType: 'text/markdown',
      dataClassification: 'PHI_PII',
      phiDeclared: true,
    });
    expect(form).not.toBeNull();
    expect(form!.get('dataClassification')).toBe('PHI_PII');
    expect(form!.get('phiDeclared')).toBe('true');
    expect(form!.get('mediaType')).toBe('text/markdown');
  });
});
