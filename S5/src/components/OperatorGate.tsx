/*
 * UI-OP-001 / UI-AUTH-001: blocks the app tree until an operator name exists (first run), and
 * stands down while a session is present because S1's subject is the identity.
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sessionOptions } from '@/api/queries';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';

export function OperatorGate({ children }: { children: React.ReactNode }) {
  const name = useOperatorStore((s) => s.name);
  const openDialog = useUiStore((s) => s.openOperatorDialog);
  const session = useQuery(sessionOptions());
  const authenticated = !!session.data?.authenticated;

  useEffect(() => {
    if (authenticated) {
      const subject = session.data?.subject;
      if (subject && subject !== useOperatorStore.getState().name) useOperatorStore.getState().setName(subject);
      if (useUiStore.getState().operatorDialogOpen) useUiStore.getState().closeOperatorDialog();
      return;
    }
    if (!name && !useUiStore.getState().operatorDialogOpen) openDialog();
  }, [authenticated, name, openDialog, session.data?.subject]);

  return <>{children}</>;
}
