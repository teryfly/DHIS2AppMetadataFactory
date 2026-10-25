<script setup lang="ts">
import { Field as VanField } from 'vant';
import 'vant/es/field/style';

// One text input with its label — the FUI answer to `<van-field>` (UM-TECH-001).
// Props and listeners fall through to the Vant field, so `v-model`, `type="textarea"`, `rows`,
// `autosize`, `readonly`, `required` and `@keyup.enter` keep working unchanged.
//
// `help` is declared rather than left to fall through, because Vant has no such prop: written as an
// attribute it landed on the field's root as `help="…"`, rendered nothing at all, and four screens
// believed they were showing a hint (a field's guidance is the one text an operator cannot get from
// the input itself).
//
// `inheritAttrs: false` plus the explicit `v-bind="$attrs"` are not decoration. The wrapper div is
// now the template's single root, and Vue hands fall-through attributes to *the root*: without this,
// `v-model`, `label`, `type` and every listener landed on the div, the Vant field received nothing,
// and each form silently stopped collecting input (a wizard's next button that never enables, with
// no console error to explain it).
defineOptions({ inheritAttrs: false });
defineProps<{ help?: string }>();
</script>

<template>
  <div class="fui-field-wrap">
    <VanField v-bind="$attrs" class="fui-field" />
    <p v-if="help" class="fui-field__help">{{ help }}</p>
  </div>
</template>

<style scoped>
.fui-field :deep(.van-field__label) { color: var(--fui-text-secondary); }
.fui-field :deep(.van-field__body input),
.fui-field :deep(.van-field__body textarea) { color: var(--fui-text); font-size: inherit; }
/* UM-UX-001: the box a finger has to hit is the control, not the cell around it, so the input itself
   grows to the 44 px floor and the Vant row follows. */
.fui-field :deep(.van-field__control) { min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); }
/* The hint is secondary text, but never below the smallest size the token set allows (UM-NFR-03). */
.fui-field__help { margin: 4px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
</style>
