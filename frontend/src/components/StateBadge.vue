<script setup lang="ts">
import type { ElectionState } from '../api.ts';
import { STATE_LABELS } from '../format.ts';

defineProps<{ state: ElectionState }>();
</script>

<template>
  <span class="badge" :class="state">{{ STATE_LABELS[state] }}</span>
</template>
