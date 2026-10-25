/*
 * One design decision, read the way an approver reads it.
 *
 * Two screens show this and must say the same thing about the same row, so the card lives on its own
 * rather than inside either page: the approval page's third question (UI-GATE-001, 为什么（决策与理由）)
 * and the project's own decision list (UI-REV-007) are the same object seen from two directions, and
 * a decision that reads differently in the two places is a decision nobody can compare.
 *
 * `selected`, `alternatives`, `risks` and `confirmation` are free-form JSON in the contract: S2
 * stores what the Blueprint said, and the shape differs between an architecture decision and a
 * mapping one. They print as text lines rather than as JSON, because this screen exists for the
 * person deciding, not for the person debugging the blueprint.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Card, Tag } from '@dhis2/ui';
import { decisionText } from '@/lib/format';
import type { DecisionView } from '@/api/types';

export function DecisionCard({ decision, projectId }: { decision: DecisionView; projectId?: string }) {
  const { t } = useTranslation();
  const kind = decision.type === 'ARCHITECTURE' ? t('decisions.typeArchitecture') : decision.type === 'MAPPING' ? t('decisions.typeMapping') : decision.type ?? '';
  const impact = decision.impactLevel === 'HIGH' ? t('decisions.impactHigh') : decision.impactLevel === 'MEDIUM' ? t('decisions.impactMedium') : decision.impactLevel === 'LOW' ? t('decisions.impactLow') : '';
  const chosen = decisionText(decision.selected);
  const confirmed = decisionText(decision.confirmation);
  const alternatives = (decision.alternatives ?? []).map(decisionText).filter((line) => line !== '');
  const risks = (decision.risks ?? []).map(decisionText).filter((line) => line !== '');

  return (
    <Card>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <strong className="mono">{decision.decisionId}</strong>
          <span className="row" style={{ gap: 8 }}>
            {kind ? <Tag neutral>{kind}</Tag> : null}
            {/* HIGH is the one that colours the chip, because it is the level that makes a decision
                worth reading before approving (BP-DEC-004). */}
            {impact ? <Tag neutral={decision.impactLevel !== 'HIGH'} negative={decision.impactLevel === 'HIGH'}>{t('decisions.impact')}: {impact}</Tag> : null}
          </span>
        </div>

        {decision.rationale ? <p style={{ margin: 0 }}>{decision.rationale}</p> : null}

        {chosen ? (
          <div className="stack" style={{ gap: 2 }}>
            <span className="muted" style={{ fontSize: 12 }}>{t('decisions.selected')}</span>
            <span>{chosen}</span>
          </div>
        ) : null}

        {alternatives.length ? (
          <div className="stack" style={{ gap: 2 }}>
            <span className="muted" style={{ fontSize: 12 }}>{t('decisions.alternatives')}</span>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {alternatives.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
        ) : null}

        {risks.length ? (
          <div className="stack" style={{ gap: 2 }}>
            <span className="muted" style={{ fontSize: 12 }}>{t('decisions.risks')}</span>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {risks.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
        ) : null}

        <div className="row" style={{ gap: 16, flexWrap: 'wrap', fontSize: 13 }}>
          {/* S2 stores confidence as a 0–1 ratio; a bare 0.78 next to every other percentage on the
              screen is two units for one idea. */}
          {decision.confidence !== null && decision.confidence !== undefined ? (
            <span className="muted">{t('decisions.confidence')}: {Math.round(decision.confidence * 100)}%</span>
          ) : null}
          {confirmed ? <span className="muted">{t('decisions.confirmation')}: {confirmed}</span> : null}
          {decision.subject && projectId ? (
            <Link to={`/projects/${encodeURIComponent(projectId)}/graph?businessKey=${encodeURIComponent(String(decision.subject))}`} className="mono">
              {t('decisions.subject')}: {String(decision.subject)}
            </Link>
          ) : null}
        </div>

        {(decision.affectedArtifacts ?? []).length ? (
          <div className="stack" style={{ gap: 2 }}>
            <span className="muted" style={{ fontSize: 12 }}>{t('decisions.affected')}</span>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              {(decision.affectedArtifacts ?? []).map((artifact) =>
                projectId ? (
                  <Link key={artifact} to={`/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifact)}`} className="mono">
                    {artifact}
                  </Link>
                ) : (
                  <span key={artifact} className="mono">{artifact}</span>
                ),
              )}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
