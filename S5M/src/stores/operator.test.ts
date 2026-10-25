import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useOperatorStore } from './operator';
import { useAuthStore } from './auth';
import type { SessionView } from '@/api/types';

function signedIn(over: Partial<SessionView>): void {
  useAuthStore().session = { authenticated: true, authMode: 'local', mustChangePassword: false, ...over } as SessionView;
}

describe('operator identity (UM-AC-01, UM-ME-001)', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it('without a session the typed name is the whole identity', () => {
    const operator = useOperatorStore();
    expect(operator.hasOperator).toBe(false);
    expect(operator.display).toBe('');
    operator.setName('张三');
    expect(operator.hasOperator).toBe(true);
    expect(operator.display).toBe('张三');
  });

  it('a session is an identity on its own, so nothing waits on a typed name', () => {
    const operator = useOperatorStore();
    signedIn({ subject: 'arch', displayName: 'Architect' });
    expect(operator.hasOperator).toBe(true);
    expect(operator.display).toBe('Architect');
  });

  it('falls back to the subject when the account has no display name', () => {
    signedIn({ subject: 'arch', displayName: '' });
    expect(useOperatorStore().display).toBe('arch');
  });
});
