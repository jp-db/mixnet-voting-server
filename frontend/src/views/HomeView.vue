<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { api, type ElectionListItem } from '../api.ts';
import StateBadge from '../components/StateBadge.vue';
import { message, when } from '../format.ts';

const elections = ref<ElectionListItem[] | null>(null);
const error = ref('');

onMounted(async () => {
  try {
    elections.value = await api.elections();
  } catch (e) {
    error.value = message(e);
  }
});
</script>

<template>
  <h1>Elections</h1>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-else-if="elections === null" class="muted">Loading…</p>
  <p v-else-if="elections.length === 0" class="muted">No elections yet.</p>
  <div v-for="e in elections ?? []" :key="e.id" class="card">
    <div class="row">
      <RouterLink :to="`/elections/${e.id}`"><strong>{{ e.title }}</strong></RouterLink>
      <StateBadge :state="e.state" />
    </div>
    <p class="muted" v-if="e.opened_at">Opened {{ when(e.opened_at) }}<template v-if="e.closed_at"> · closed {{ when(e.closed_at) }}</template></p>
  </div>
</template>
