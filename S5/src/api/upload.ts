/*
 * UI-REQS-001: a business-input upload has to show progress, and neither `fetch` nor
 * openapi-fetch reports upload progress — `XMLHttpRequestUpload` is still the only browser API
 * that emits those events. So this one call leaves the typed client and re-attaches by hand the
 * headers its middleware would have added (Accept-Language, X-Operator, X-Correlation-Id and — for
 * a cookie-authenticated session — the CSRF double-submit token); forgetting one of them surfaces
 * either as a 400 OPERATOR_REQUIRED or as a 403 CSRF_FAILED on a route the typed client already
 * handles correctly, which is the worst kind of difference to discover in production.
 *
 * There is no Idempotency-Key here on purpose: the upload route declares none, and S1 honours a key
 * on any POST — so a key would turn "uploaded the wrong file, uploading the fix" into a 409 the
 * console offers no way to clear.
 *
 * The response is still validated and still becomes an `ApiError` through the same
 * `toApiError`, so an upload failure reads in the UI exactly like any other S1 failure.
 */
import { openapiBaseUrl, loadRuntimeConfig } from '@/config/runtime';
import { getAcceptLanguage } from '@/stores/language';
import { getOperatorName } from '@/stores/operator';
import { CSRF_HEADER, getCsrfToken } from '@/stores/csrf';
import { newUuid } from './idempotency';
import { operatorHeader, toApiError } from './client';
import { networkApiError } from './errors';
import type { InputView } from './types';

/** SRS 20.1, lowest to highest. The labels are S1's, not ours to invent (UI-BR-01). */
export const DATA_CLASSIFICATIONS = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'PHI_PII'] as const;
export type DataClassificationName = (typeof DATA_CLASSIFICATIONS)[number];

export interface UploadArgs {
  projectId: string;
  file: File;
  dataClassification: DataClassificationName;
  /** FR-REQ-001: PHI/PII is only accepted when the uploader declared it explicitly. */
  phiDeclared: boolean;
  onProgress?: (fraction: number) => void;
}

/** The multipart body S1's upload route reads (FR-REQ-001: the declaration is a field, not a header). */
export function buildUploadForm({ file, dataClassification, phiDeclared }: Omit<UploadArgs, 'projectId' | 'onProgress'>): FormData {
  const body = new FormData();
  body.append('file', file);
  body.append('mediaType', file.type);
  body.append('dataClassification', dataClassification);
  body.append('phiDeclared', String(phiDeclared));
  return body;
}

export function uploadInput({ projectId, file, dataClassification, phiDeclared, onProgress }: UploadArgs): Promise<InputView> {
  const body = buildUploadForm({ file, dataClassification, phiDeclared });

  const url = `${openapiBaseUrl()}/api/v1/projects/${encodeURIComponent(projectId)}/inputs`;
  return new Promise<InputView>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'text';
    xhr.setRequestHeader('Accept-Language', getAcceptLanguage());
    if (loadRuntimeConfig().traceCorrelation) xhr.setRequestHeader('X-Correlation-Id', newUuid());
    // UI-AUTH-001 / FR-SEC-005: the session rides an HttpOnly cookie the browser sends by itself, so
    // S1 needs the readable CSRF value named in a header to accept the change at all.
    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader(CSRF_HEADER, csrf);
    const operator = getOperatorName();
    // UI-OP-003: absent means S1 answers 400 OPERATOR_REQUIRED; the button is already
    // disabled in that case, so this is the guard, not the user-facing path.
    if (operator) xhr.setRequestHeader('X-Operator', operatorHeader(operator));

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / (event.total || 1));
    };
    xhr.onerror = () => reject(networkApiError(new Error('upload network failure')));
    xhr.onload = () => {
      const headers = new Headers(
        xhr
          .getAllResponseHeaders()
          .split('\n')
          .filter(Boolean)
          .map((line) => {
            const [name, ...rest] = line.trim().split(':');
            return [name as string, rest.join(':').trim()] as [string, string];
          }),
      );
      let parsed: unknown = null;
      try {
        parsed = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        parsed = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as InputView);
        return;
      }
      reject(toApiError(parsed, xhr.status, headers));
    };
    xhr.send(body);
  });
}
