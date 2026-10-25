// Shared FUI component types (kept out of SFC <script setup> which cannot export).
export interface TimelineItem {
  key: string;
  title: string;
  subtitle?: string;
  status: 'done' | 'current' | 'pending' | 'failed' | 'blocked' | 'skipped';
  collapsible?: boolean;
}

export interface CheckItem {
  key: string;
  label: string;
  state: 'pass' | 'fail' | 'pending';
  note?: string;
}

/** One choice in FuiSegmented / FuiCheckChips; the label is already localised by the screen. */
export interface ChipOption {
  value: string;
  label: string;
}
