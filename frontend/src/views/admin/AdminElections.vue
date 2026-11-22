<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { api, type ElectionState, type MixnetNode, type QuestionInput } from '../../api.ts';
import MixServersEditor from '../../components/MixServersEditor.vue';
import QuestionsEditor from '../../components/QuestionsEditor.vue';
import StateBadge from '../../components/StateBadge.vue';
import { message, when } from '../../format.ts';
import AdminNav from './AdminNav.vue';

const router = useRouter();
const elections = ref<{ id: string; title: string; state: ElectionState; created_at: string }[]>([]);
const nodes = ref<MixnetNode[]>([]);
const error = ref('');
const creating = ref(false);
const title = ref('');
const description = ref('');
const questions = ref<QuestionInput[]>([{ title: '', description: '', answers: [], min_choices: 1, max_choices: 1 }]);
const chain = ref<string[]>([]);

onMounted(async () => {
  try {
    [elections.value, nodes.value] = await Promise.all([api.admin.elections(), api.admin.nodes()]);
    chain.value = nodes.value
      .filter((n) => n.is_default)
      .sort((a, b) => (a.default_position ?? 0) - (b.default_position ?? 0))
      .map((n) => n.name);
  } catch (e) {
    error.value = message(e);
  }
});

async function create() {
  error.value = '';
  try {
    const e = await api.admin.create({
      title: title.value,
      description: description.value,
      questions: questions.value,
      mix_servers: chain.value,
    });
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
      <tr><th>Title</th><th>State</th><th>Created</th></tr>
      <tr v-for="e in elections" :key="e.id">
        <td><RouterLink :to="`/admin/elections/${e.id}`">{{ e.title }}</RouterLink></td>
        <td><StateBadge :state="e.state" /></td>
        <td>{{ when(e.created_at) }}</td>
      </tr>
    </table>
  </div>
  <button v-if="!creating" class="primary" @click="creating = true">New election</button>
  <form v-else class="stack" @submit.prevent="create">
    <h2>New election</h2>
    <label><span>Title</span><input v-model="title" required /></label>
    <label><span>Description</span><textarea v-model="description" rows="2" /></label>
    <h3>Questions</h3>
    <p class="muted">Each question is counted separately, with its own keys and mix.</p>
    <QuestionsEditor v-model="questions" />
    <h3>Mix servers</h3>
    <MixServersEditor v-model="chain" :nodes="nodes" />
    <div class="row">
      <button type="button" @click="creating = false">Cancel</button>
      <button class="primary">Create draft</button>
    </div>
  </form>
</template>
