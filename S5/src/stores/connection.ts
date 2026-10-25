/*
 * Ephemeral realtime connection status (UI-RT-005). Written by the SSE hook, read by the offline
 * banner in the top bar. Purely a UI fact about the transport, never business state.
 */
import { create } from 'zustand';

export type ConnectionStatus = 'connecting' | 'open' | 'offline' | 'idle';

interface ConnectionState {
  status: ConnectionStatus;
  setStatus: (status: ConnectionStatus) => void;
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  status: 'idle',
  setStatus: (status) => set({ status }),
}));
