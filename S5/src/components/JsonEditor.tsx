/*
 * Monaco JSON editor used for manual authoring (UI-MAN-002) and read-only raw views (UI-REV-001).
 * Bundled Monaco (no CDN) so the offline image has no network dependency. Language features load
 * through Vite workers configured in main.tsx (MonacoEnvironment).
 */
import Editor from '@monaco-editor/react';
import '@/lib/monacoSetup';

interface Props {
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  height?: string;
  language?: string;
  /** Monaco model path; also the fileMatch key a section Schema attaches to. */
  modelPath?: string;
  onMount?: (editor: Parameters<NonNullable<React.ComponentProps<typeof Editor>['onMount']>>[1]) => void;
}

export function JsonEditor({ value, onChange, readOnly, height = '360px', language = 'json', modelPath, onMount }: Props) {
  return (
    <Editor
      path={modelPath}
      onMount={(_editor, monaco) => onMount?.(monaco)}
      height={height}
      language={language}
      value={value}
      theme="light"
      onChange={(v) => onChange?.(v ?? '')}
      options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2, readOnly: !!readOnly }}
    />
  );
}
