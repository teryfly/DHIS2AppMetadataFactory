import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import '@/i18n';
import { Icon, type IconName } from './icons';
import { ShellIcon, type ShellIconName } from './shellIcons';
import { RouteTabs } from './RouteTabs';
import { WorkflowStepper } from './WorkflowStepper';
import { SectionCard, StatCard } from './SectionCard';
import { PageHeader } from './PageHeader';
import { Disclosure } from './Disclosure';
import { EmptyState } from './EmptyState';
import { useUiStore } from '@/stores/ui';
import type { StageView } from '@/api/types';

const ICONS: IconName[] = [
  'home', 'inbox', 'rocket', 'folder', 'server', 'shield', 'users', 'settings', 'bell', 'help',
  'chevronDown', 'chevronRight', 'arrowLeft', 'arrowRight', 'check', 'alert', 'clock', 'play', 'copy',
  'download', 'external', 'close', 'sparkles', 'file', 'graph', 'layers', 'plus', 'book', 'search',
  'lock', 'refresh', 'more', 'info', 'workflow',
];
const SHELL_ICONS: ShellIconName[] = [
  'workflow', 'help', 'bell', 'users', 'lock', 'home', 'inbox', 'rocket', 'folder', 'server',
  'shield', 'settings', 'close',
];

beforeEach(() => {
  useUiStore.getState().closeHelp();
});

describe('design primitives', () => {
  it('renders every icon in both sets', () => {
    render(
      <div>
        {ICONS.map((name) => <Icon key={name} name={name} />)}
        {SHELL_ICONS.map((name) => <ShellIcon key={name} name={name} />)}
      </div>,
    );
    expect(document.querySelectorAll('svg')).toHaveLength(ICONS.length + SHELL_ICONS.length);
  });

  it('marks the active route and the current stage without claiming two current steps', () => {
    const stages = [
      { id: 's1', stageType: 'REQUIREMENTS', status: 'PASSED', attempt: 0 },
      { id: 's2', stageType: 'MAPPING', status: 'WAITING_APPROVAL', attempt: 1 },
      { id: 's3', stageType: 'HANDOFF', status: 'SKIPPED', attempt: 0 },
    ] as StageView[];
    render(
      <MemoryRouter>
        <RouteTabs
          label="Sections"
          tabs={[
            { key: 'a', to: '/a', label: 'Alpha', active: true },
            { key: 'b', to: '/b', label: 'Beta', active: false, badge: 2 },
          ]}
        />
        <WorkflowStepper projectId="P-1" stages={stages} currentStageId="s2" activeStageId="s1" />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: 'Alpha' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: /Beta/ })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('link', { name: /Requirements/ })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('link', { name: /Mapping/ })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('link', { name: /Handoff/ })).toBeInTheDocument();
    expect(document.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
  });

  it('discloses secondary work, shows cards and opens page help', async () => {
    render(
      <MemoryRouter>
        <PageHeader title="A page" description="What this page is for" helpKey="stage" />
        <SectionCard title="Section">Body</SectionCard>
        <StatCard label="Count" value={3} hint="items" />
        <Disclosure label="Manual authoring" description="Only when needed">
          Hidden form
        </Disclosure>
        <EmptyState title="Nothing yet" action={<button type="button">Create</button>}>
          It will appear here.
        </EmptyState>
      </MemoryRouter>,
    );

    expect(screen.getByText('What this page is for')).toBeInTheDocument();
    expect(screen.getByText('items')).toBeInTheDocument();
    const disclosure = screen.getByRole('button', { name: /Manual authoring/ });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(disclosure);
    expect(screen.getByText('Hidden form')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Open help for this page|打开本页帮助/ }));
    expect(useUiStore.getState().helpOpen).toBe(true);
    expect(useUiStore.getState().helpTopic).toBe('stage');
  });
});
