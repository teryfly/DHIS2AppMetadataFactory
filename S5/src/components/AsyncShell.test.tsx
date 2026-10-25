/*
 * The state shell six desktop tables were each hand-rolling (and four of them had left out).
 *
 * The property worth pinning is that only one arm ever renders: a list that is still loading must not
 * also claim to be empty, and a refused read must not show a header over an empty `<tbody>` — that
 * last combination is exactly what made an administration page look intact while it said nothing.
 */
import { describe, it, expect, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AsyncShell } from '@/components/AsyncShell';
import { ApiError } from '@/api/errors';

function shell(props: Partial<ComponentProps<typeof AsyncShell>>) {
  return render(
    <AppProviders>
      <AsyncShell loading={false} {...props}>
        <table aria-label="the rows"><tbody><tr><td>Ada</td></tr></tbody></table>
      </AsyncShell>
    </AppProviders>,
  );
}

const refused = new ApiError({ errorCode: 'NOT_FOUND', httpStatus: 404, message: 'gone', correlationId: 'cid-9' });
const flaked = new ApiError({ errorCode: 'HTTP_503', httpStatus: 503, message: 'busy', correlationId: 'cid-8', retryable: true });

describe('AsyncShell（桌面的统一状态壳）', () => {
  it('shows the rows and nothing else once the read has landed', () => {
    shell({});
    expect(screen.getByText('Ada')).toBeInTheDocument();
  });

  it('names the loading state instead of drawing an empty table under a header', () => {
    shell({ loading: true });
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText('Ada')).not.toBeInTheDocument();
  });

  it('refuses to look empty when S1 actually refused the read', () => {
    shell({ error: refused });
    expect(screen.getByText('Request failed')).toBeInTheDocument();
    // UI-REQ-002: the correlation id is the operator's half of a support conversation
    expect(screen.getByText(/cid-9/)).toBeInTheDocument();
    expect(screen.queryByText('Ada')).not.toBeInTheDocument();
    // …and a read S1 answered "does not exist" is not offered a retry that cannot succeed
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument();
  });

  it('retries through the caller when the read is worth asking again', async () => {
    const onRetry = vi.fn();
    shell({ error: flaked, onRetry });
    await userEvent.click(screen.getByRole('button', { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('says what an empty list means, and how to stop it being empty', () => {
    shell({
      empty: true,
      emptyTitle: 'No one holds a role in this project yet.',
      emptyHint: 'Add them below.',
      emptyAction: <a href="/admin/users">User accounts</a>,
    });
    expect(screen.getByText('No one holds a role in this project yet.')).toBeInTheDocument();
    expect(screen.getByText('Add them below.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'User accounts' })).toBeInTheDocument();
    expect(screen.queryByText('Ada')).not.toBeInTheDocument();
  });
});
