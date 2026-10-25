<script setup lang="ts">
import { Search as VanSearch } from 'vant';
import 'vant/es/search/style';

// Search input (FUI answer to `<van-search>`, UM-TECH-001). Declared so pages never reach for Vant
// just to get a search box: `v-model`, `placeholder`, `shape`, `show-action` and `@cancel` all
// fall through.
</script>

<template>
  <VanSearch class="fui-search-field" />
</template>

<style scoped>
.fui-search-field :deep(.van-search__content) { background: var(--fui-surface-2); }
.fui-search-field :deep(.van-field__body input) { color: var(--fui-text); }
</style>
