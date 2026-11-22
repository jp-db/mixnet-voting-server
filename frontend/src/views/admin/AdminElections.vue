<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { api, type ElectionState } from '../../api.ts';
import StateBadge from '../../components/StateBadge.vue';
import { message, when } from '../../format.ts';
import { session } from '../../session.ts';
import AdminNav from './AdminNav.vue';

const router = useRouter();
const elections = ref<{ id: string; title: string; state: ElectionState; created_at: string }[]>([]);
const error = ref('');
const creating = ref(false);
const title = ref('');
const description = ref('');

onMounted(async () => {
  try {
    elections.value = await api.admin.elections();
  } catch (e) {
    error.value = message(e);
  }
});

async function create() {
  error.value = '';
  try {
    // Questions, voters, mix servers and the schedule are set on the election's page.
    const e = await api.admin.create({ title: title.value, description: description.value });
    await router.push(`/admin/elections/${e.id}`);
  } catch (e) {
    error.value = message(e);
  }
}

async function clone(id: string) {
  error.value = '';
  try {
    const e = await api.admin.clone(id);
    await router.push(`/admin/elections/${e.id}`);
  } catch (e) {
    error.value = message(e);
  }
}
</script>

<template>
  <AdminNav />
  <h1>Elections you manage</h1>
  <p v-if="error" class="error">{{ error }}</p>
  <div class="card table-wrap" v-if="elections.length">
    <table>
      <tr><th>Title</th><th>State</th><th>Created</th><th v-if="session.me?.can_create"></th></tr>
      <tr v-for="e in elections" :key="e.id">
        <td><RouterLink :to="`/admin/elections/${e.id}`">{{ e.title }}</RouterLink></td>
        <td><StateBadge :state="e.state" /></td>
        <td>{{ when(e.created_at) }}</td>
        <td v-if="session.me?.can_create"><button class="link" @click="clone(e.id)">Clone</button></td>
      </tr>
    </table>
  </div>
  <template v-if="session.me?.can_create">
    <button v-if="!creating" class="primary" @click="creating = true">New election</button>
    <form v-else class="card stack" @submit.prevent="create">
      <h2>New election</h2>
      <label><span>Title</span><input v-model="title" required /></label>
      <label><span>Description</span><textarea v-model="description" rows="2" /></label>
      <p class="muted">The election starts as a draft. Add its questions, voters, mix servers and voting period on its page.</p>
      <div class="row">
        <button type="button" @click="creating = false">Cancel</button>
        <button class="primary">Create draft</button>
      </div>
    </form>
  </template>
</template>
