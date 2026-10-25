import { useEffect, useId, useRef, type ReactNode } from 'react';
import { ShellIcon } from './shellIcons';

/**
 * A small accessible dialog used by the shell. It is deliberately native markup rather than the
 * component library's modal: the two shell dialogs must stay in the entry graph, and pulling the
 * library in for them put its whole chunk on first paint.
 */
export function Dialog({
  title,
  closeLabel,
  onClose,
  children,
  footer,
  width = 480,
}: {
  title: ReactNode;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  width?: number;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      className="dialog-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} style={{ maxWidth: width }}>
        <header className="dialog__header">
          <h2 id={titleId}>{title}</h2>
          <button ref={closeRef} type="button" className="icon-button" onClick={onClose} aria-label={closeLabel}>
            <ShellIcon name="close" size={20} />
          </button>
        </header>
        <div className="dialog__body">{children}</div>
        <footer className="dialog__footer">{footer}</footer>
      </div>
    </div>
  );
}
