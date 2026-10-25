import { Component, type ErrorInfo, type ReactNode } from 'react';
import i18n from '@/i18n';

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Last-resort boundary so a render bug degrades to a localised panel, not a blank page (UI-APP-003). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Structured log with correlation-ready fields; never credential values (00 doc §observability).
    console.error('[Factory UI] render error', error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    // UI-APP-002 says every string comes from the translation resources: the literals below were
    // chosen by a ternary on the language, and `dhis2-btn` is a class this app never defines.
    return (
      <div role="alert" style={{ padding: 24 }}>
        <h2>{i18n.t('error.boundaryTitle')}</h2>
        <p className="muted mono" style={{ fontSize: 12 }}>
          {String(this.state.error.message)}
        </p>
        <button type="button" className="primary-link" onClick={this.reset}>
          {i18n.t('error.boundaryRetry')}
        </button>
      </div>
    );
  }
}
