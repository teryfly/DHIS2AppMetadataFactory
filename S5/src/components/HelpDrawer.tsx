import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useUiStore } from '@/stores/ui';
import { ShellIcon } from './shellIcons';

const FLOW = ['inputs', 'requirements', 'design', 'gates', 'delivery', 'verify'] as const;
const TERMS = ['stage', 'gate', 'artifact', 'blueprint', 'registry', 'grant', 'exemption', 'handoff'] as const;

/** Global help: the workflow in one place, plus the vocabulary a first-time operator needs. */
export function HelpDrawer() {
  const { t } = useTranslation();
  const open = useUiStore((s) => s.helpOpen);
  const topic = useUiStore((s) => s.helpTopic);
  const close = useUiStore((s) => s.closeHelp);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, close]);

  if (!open) return null;

  const topicText = topic ? t(`help.page.${topic}`, { defaultValue: '' }) : '';

  return (
    <div
      className="help-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <aside className="help-drawer" role="dialog" aria-modal="true" aria-labelledby="help-title">
        <header className="help-drawer__header">
          <div>
            <h2 id="help-title">{t('help.title')}</h2>
            <p>{t('help.intro')}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            onClick={close}
            aria-label={t('help.close')}
          >
            <ShellIcon name="close" size={20} />
          </button>
        </header>

        <div className="help-drawer__body">
          {topicText ? (
            <section className="help-section help-section--topic">
              <h3>{t('help.currentPage')}</h3>
              <p>{topicText}</p>
            </section>
          ) : null}

          <section className="help-section">
            <h3>{t('help.flowTitle')}</h3>
            <ol className="help-flow">
              {FLOW.map((step, index) => (
                <li key={step}>
                  <span className="help-flow__index">{index + 1}</span>
                  <div>
                    <strong>{t(`help.flow.${step}.title`)}</strong>
                    <p>{t(`help.flow.${step}.body`)}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section className="help-section">
            <h3>{t('help.exampleTitle')}</h3>
            <p>{t('help.exampleBody')}</p>
            <Link to="/help/tutorial" onClick={close} className="help-drawer__link">
              <ShellIcon name="rocket" size={16} />
              {t('help.exampleLink')}
            </Link>
          </section>

          <section className="help-section">
            <h3>{t('help.glossaryTitle')}</h3>
            <dl className="help-terms">
              {TERMS.map((term) => (
                <div key={term}>
                  <dt>{t(`help.term.${term}.term`)}</dt>
                  <dd>{t(`help.term.${term}.body`)}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="help-section">
            <h3>{t('help.rolesTitle')}</h3>
            <p>{t('help.rolesBody')}</p>
          </section>
        </div>

        <footer className="help-drawer__footer">
          <Link to="/getting-started" onClick={close} className="help-drawer__link">
            <ShellIcon name="rocket" size={16} />
            {t('help.guideLink')}
          </Link>
          <button type="button" className="quiet-button" onClick={close}>
            {t('common.close')}
          </button>
        </footer>
      </aside>
    </div>
  );
}
