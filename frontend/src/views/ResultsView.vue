<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { api, ApiError, type PublicElection, type QuestionResult } from '../api.ts';
import { message } from '../format.ts';
import { verifyQuestion, type Verification } from '../verifier.ts';

const props = defineProps<{ id: string }>();
const election = ref<PublicElection | null>(null);
const results = ref<QuestionResult[]>([]);
const error = ref('');
const checks = reactive<Record<number, { running: boolean; progress: string; outcome: Verification | null; error: string }>>({});

onMounted(async () => {
  try {
    election.value = await api.election(props.id);
    results.value = (await api.results(props.id)).questions;
    for (const r of results.value) checks[r.index] = { running: false, progress: '', outcome: null, error: '' };
  } catch (e) {
    error.value = e instanceof ApiError && e.status === 404 ? 'The results are not published yet.' : message(e);
  }
});

const REASONS: Record<string, string> = {
  too_many_choices: 'more choices than allowed',
  too_few_choices: 'fewer choices than required',
  coefficient_not_0_or_1: 'not a valid selection',
  nonzero_padding: 'outside the list of answers',
  wrong_length: 'wrong length',
};

function pct(n: number, total: number) {
  return total ? `${Math.round((100 * n) / total)}%` : '0%';
}

async function check(r: QuestionResult) {
  const q = election.value!.questions[r.index];
  const c = checks[r.index];
  Object.assign(c, { running: true, outcome: null, error: '' });
  try {
    c.outcome = await verifyQuestion(props.id, r.index, q.spec, q.params_key, r, (t) => (c.progress = t));
  } catch (e) {
    c.error = message(e);
  } finally {
    c.running = false;
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="election && results.length">
    <h1>Results</h1>
    <p><RouterLink :to="`/elections/${id}`">{{ election.title }}</RouterLink></p>
    <div v-for="r in results" :key="r.index" class="card stack">
      <h2>{{ r.index + 1 }}. {{ election.questions[r.index].spec.title }}</h2>
      <template v-if="r.result">
        <table>
          <tr><th>Answer</th><th>Votes</th><th style="width: 40%"></th></tr>
          <tr v-for="(answer, i) in election.questions[r.index].spec.answers" :key="i">
            <td>{{ answer }}</td>
            <td>{{ r.result.counts[i] }}</td>
            <td><div class="bar" :style="{ width: pct(r.result.counts[i], r.result.valid) }"></div></td>
          </tr>
        </table>
        <p class="muted">
          {{ r.result.valid }} valid votes<template v-if="r.result.blank">, {{ r.result.blank }} blank</template>.
        </p>
        <p v-if="r.discarded?.length" class="warning">
          {{ r.discarded.length }} malformed {{ r.discarded.length === 1 ? 'vote was' : 'votes were' }} left out:
          {{ r.discarded.map((d) => `#${d.index} (${REASONS[d.reason] ?? d.reason})`).join(', ') }}.
        </p>
        <details>
          <summary>Mixing</summary>
          <table>
            <tr><th>Server</th><th>In</th><th>Out</th><th>Rejected</th></tr>
            <tr v-for="s in r.mix_steps" :key="s.position">
              <td>{{ s.position }}: {{ election.mix_servers[s.position]?.node }}</td>
              <td>{{ s.info?.n_in }}</td>
              <td>{{ s.info?.n_out }}</td>
              <td>{{ s.info?.rejections.map((x) => x.reason).join(', ') || 'none' }}</td>
            </tr>
          </table>
        </details>
      </template>
      <p v-else class="muted">No result for this question ({{ r.state }}).</p>

      <div v-if="r.result" class="stack">
        <button :disabled="checks[r.index]?.running" @click="check(r)">Verify this count in my browser</button>
        <p v-if="checks[r.index]?.running" class="muted">{{ checks[r.index].progress }}</p>
        <p v-if="checks[r.index]?.error" class="error">{{ checks[r.index].error }}</p>
        <template v-if="checks[r.index]?.outcome">
          <p v-if="checks[r.index].outcome!.ok" class="success">
            Verified: every proof holds against the {{ checks[r.index].outcome!.votes }} counted ballots, and recounting gives
            the published result.
          </p>
          <ul v-else class="error">
            <li v-for="p in checks[r.index].outcome!.problems" :key="p">{{ p }}</li>
          </ul>
        </template>
      </div>
    </div>
  </template>
</template>
