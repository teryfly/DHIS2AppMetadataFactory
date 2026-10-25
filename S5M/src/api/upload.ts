import { config } from '@/config/runtime';
import { buildUrl, languageTag, newCorrelationId, operatorHeader, readCookie } from '@/api/http';
import { AppError, toApiError } from '@/api/errors';
import { readJSON } from '@/lib/storage';
import { LS } from '@/lib/storage';

export type DataClassification = 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL' | 'RESTRICTED' | 'PHI_PII';

export const DATA_CLASSIFICATIONS: DataClassification[] = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'PHI_PII'];

export interface UploadOptions {
  mediaType: string;
  dataClassification: DataClassification;
  phiDeclared?: boolean;
}

/**
 * Multipart upload with real progress (the typed JSON client cannot report upload progress).
 * Attaches the same cross-cutting headers as the JSON client (UM-BR-04).
 *
 * Deliberately no `Idempotency-Key`: the upload route declares none, and S1's middleware honours a
 * key on any POST — so sending one would turn "uploaded the wrong file, uploading the fix" into a
 * 409 IDEMPOTENCY_CONFLICT that the console never offered the operator a way to clear.
 */
export function uploadInput<T>(
  projectId: string,
  file: File,
  opts: UploadOptions,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mediaType', opts.mediaType);
    fd.append('dataClassification', opts.dataClassification);
    if (opts.phiDeclared !== undefined) fd.append('phiDeclared', String(opts.phiDeclared));

    const xhr = new XMLHttpRequest();
    xhr.open('POST', buildUrl(`/projects/${encodeURIComponent(projectId)}/inputs`), true);
    xhr.responseType = 'text';
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Accept-Language', languageTag());
    if (config.traceCorrelation) xhr.setRequestHeader('X-Correlation-Id', newCorrelationId());
    // The two headers only `http.ts` adds, and this call does not go through it. Without the CSRF
    // echo S1 answers 403 CSRF_FAILED for every cookie-authenticated change
    // (factory_control/rest/middleware.py), and a Chinese operator name sent raw makes
    // setRequestHeader throw right here — the body never leaves the browser (CP-API-002).
    const csrf = readCookie(config.csrfCookieName);
    if (csrf) xhr.setRequestHeader(config.csrfHeader, csrf);
    const operator = readJSON<string>(LS.operator, '') || '';
    if (operator) xhr.setRequestHeader('X-Operator', operatorHeader(operator));

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    if (signal) signal.addEventListener('abort', () => xhr.abort());
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as T);
        } catch {
          resolve(xhr.responseText as unknown as T);
        }
      } else {
        let parsed: unknown;
        try {
          parsed = JSON.parse(xhr.responseText);
        } catch {
          parsed = { message: xhr.responseText };
        }
        reject(toApiError(parsed, xhr.status, {}));
      }
    };
    xhr.onerror = () => reject(AppError.network('upload network error'));
    xhr.onabort = () => reject(AppError.network('upload aborted'));
    xhr.send(fd);
  });
}
