<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';

import { api, type BoardEntry, type PublicElection } from '../api.ts';
import { message, when } from '../format.ts';

const props = defineProps<{ id: string }>();
const election = ref<PublicElection | null>(null);
const question = ref(0);
const entries = ref<BoardEntry[]>([]);
const nextAfter = ref<number | null>(null);
const error = ref('');

async function load(more = false) {
  try {
    const page = await api.board(props.id, question.value, more ? (nextAfter.value ?? 0) : 0);
    entries.value = more ? [...entries.value, ...page.entries] : page.entries;
    nextAfter.value = page.next_after;
  } catch (e) {
    error.value = message(e);
  }
}

onMounted(async () => {
  try {
    election.value = await api.election(props.id);
    await load();
  } catch (e) {
    error.value = message(e);
  }
});
watch(question, () => load());
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="election">
    <h1>Bulletin board</h1>
    <p><RouterLink :to="`/elections/${id}`">{{ election.title }}</RouterLink></p>
    <p class="muted">
      Every ballot cast, in order. Voters are shown by their numeric voter id, never by name; when a voter cast
      more than once, their last ballot counts.
    </p>
    <label v-if="election.questions.length > 1">
      <span>Question</span>
      <select v-model.number="question">
        <option v-for="q in election.questions" :key="q.index" :value="q.index">{{ q.index + 1 }}. {{ q.spec.title }}</option>
      </select>
    </label>
    <div class="card table-wrap">
      <table>
        <tr><th>#</th><th>Voter id</th><th>Tracker</th><th>Cast</th></tr>
        <tr v-for="b in entries" :key="b.seq">
          <td>{{ b.seq }}</td>
          <td class="mono">{{ b.voter_id }}</td>
          <td class="mono">{{ b.tracker }}</td>
          <td>{{ when(b.cast_at) }}</td>
        </tr>
      </table>
      <p v-if="entries.length === 0" class="muted">No ballots yet.</p>
      <button v-if="nextAfter !== null" @click="load(true)">Load more</button>
    </div>
  </template>
</template>
