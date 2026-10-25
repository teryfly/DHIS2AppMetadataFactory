<script setup lang="ts">
import { Progress as VanProgress } from 'vant';
import 'vant/es/progress/style';

// Determinate progress bar (FUI answer to `<van-progress>`, UM-TECH-001). The ARIA value is set
// here because a bare Vant bar exposes no role, which costs the accessibility score.
const props = withDefaults(defineProps<{ percentage?: number; pivotText?: string }>(), { percentage: 0, pivotText: undefined });
</script>

<template>
  <VanProgress
    class="fui-progress-bar"
    :percentage="props.percentage"
    :pivot-text="props.pivotText"
    role="progressbar"
    :aria-valuenow="props.percentage"
    aria-valuemin="0"
    aria-valuemax="100"
  />
</template>

<style scoped>
.fui-progress-bar { --van-progress-height: 6px; }
</style>
