/*
 * One form, two containers: a modal on its own page, an inline panel inside the guided setup
 * (UI-SET-001…006). The fields are the same component either way, which is the point — a second
 * copy of the provider or environment form inside the guide would be a second source of truth
 * about what a valid registration is, and the two would drift the first time a rule moved.
 *
 * The guide keeps its route when a step opens a form, and the form keeps its own save button, so
 * "完成这一步" means the same thing here as it does on the Administration page.
 */
import type { ReactNode } from 'react';
import { Modal, ModalTitle, ModalContent, ModalActions } from '@dhis2/ui';
import { Heading } from '@/components/typography';

interface Props {
  title: string;
  /** Inline panel instead of a modal; used by the getting-started steps. */
  embedded?: boolean;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}

export function FormShell({ title, embedded, onClose, children, footer }: Props) {
  if (embedded) {
    return (
      <section className="panel" aria-label={title} style={{ padding: 16, display: 'grid', gap: 12 }}>
        <Heading level={5} size="small">{title}</Heading>
        {children}
        <div className="row" style={{ gap: 8 }}>{footer}</div>
      </section>
    );
  }
  return (
    <Modal position="bottom" hide={false} onClose={onClose}>
      <ModalTitle>{title}</ModalTitle>
      <ModalContent>
        <div className="stack">{children}</div>
      </ModalContent>
      <ModalActions>{footer}</ModalActions>
    </Modal>
  );
}
