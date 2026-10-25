import { computed } from 'vue';
import { useBreakpoints } from '@vueuse/core';

// Mobile-first: <1024 mobile (bottom tabs), >=1024 desktop (sidebar), >=1440 three columns.
const bp = useBreakpoints({ lg: 1024, xl: 1440 });

export const isDesktop = bp.greaterOrEqual('lg');
export const isWide = bp.greaterOrEqual('xl');
export const isMobile = computed(() => !isDesktop.value);

export type Layout = 'mobile' | 'desktop' | 'wide';
export const layout = computed<Layout>(() => (isWide.value ? 'wide' : isDesktop.value ? 'desktop' : 'mobile'));
