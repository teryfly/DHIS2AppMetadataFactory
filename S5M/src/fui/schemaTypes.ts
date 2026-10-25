// Minimal JSON-Schema (draft 2020-12) shape — S2 section schemas use this subset
// (type / properties / items / enum / required / definitions). Kept loose to tolerate drift.
export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  enum?: unknown[];
  required?: string[];
  minimum?: number;
  maximum?: number;
  format?: string;
  default?: unknown;
  definitions?: Record<string, JsonSchema>;
  $ref?: string;
  [k: string]: unknown;
}
