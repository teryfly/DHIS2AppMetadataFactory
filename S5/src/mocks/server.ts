import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/** Node MSW server for Vitest unit/integration tests (UI-NFR-05). */
export const server = setupServer(...handlers);
