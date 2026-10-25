import { BrowserRouter } from 'react-router-dom';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';

export function App() {
  return (
    <AppProviders>
      <BrowserRouter>
        <OperatorGate>
          <AppRoutes />
        </OperatorGate>
      </BrowserRouter>
    </AppProviders>
  );
}
