<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { api, ApiError, type PublicElection, type TrackerInfo, type VoterStatus } from '../api.ts';
import StateBadge from '../components/StateBadge.vue';
import { message, when } from '../format.ts';
import { session } from '../session.ts';

const props = defineProps<{ id: string }>();
const election = ref<PublicElection | null>(null);
const status = ref<VoterStatus | null>(null);
const error = ref('');
const tracker = ref('');
const trackerResult = ref<TrackerInfo | null>(null);
const trackerError = ref('');

onMounted(async () => {
  try {
    election.value = await api.election(props.id);
    if (session.me) status.value = await api.voterStatus(props.id);
  } catch (e) {
    error.value = e instanceof ApiError && e.status === 404 ? 'No such election.' : message(e);
  }
});

async function checkTracker() {
  trackerResult.value = null;
  trackerError.value = '';
  const t = tracker.value.trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(t)) {
    trackerError.value = 'A tracker is 64 hexadecimal characters.';
    return;
  }
  try {
    trackerResult.value = await api.tracker(props.id, t);
  } catch (e) {
    trackerError.value = e instanceof ApiError && e.status === 404 ? 'No ballot with this tracker is on the board.' : message(e);
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="election">
    <div class="row"><h1>{{ election.title }}</h1><StateBadge :state="election.state" /></div>
    <p v-if="election.description">{{ election.description }}</p>
    <p class="muted">
      {{ election.voters }} voters on the roll<template v-if="election.open_to.length"
        >; everyone who logs in with {{ election.open_to.map((o) => o.label).join(' or ') }} may also vote</template
      >.
      <template v-if="election.opened_at">Opened {{ when(election.opened_at) }}.</template>
      <template v-else-if="election.opens_at">Voting opens {{ when(election.opens_at) }}.</template>
      <template v-if="election.closed_at"> Closed {{ when(election.closed_at) }}.</template>
      <template v-else-if="election.closes_at"> Voting closes {{ when(election.closes_at) }}.</template>
    </p>

    <div class="card stack" v-if="election.state === 'open'">
      <template v-if="!session.me">
        <p>Log in to vote.</p>
        <RouterLink class="button primary" :to="{ path: '/login', query: { next: `/elections/${id}/vote` } }">Log in</RouterLink>
      </template>
      <p v-else-if="status && !status.eligible">You are not on this election's voter roll.</p>
      <template v-else-if="status">
        <p v-if="status.cast.length">You have voted. You may vote again: only your last ballot counts.</p>
        <RouterLink class="button primary" :to="`/elections/${id}/vote`">{{ status.cast.length ? 'Vote again' : 'Vote' }}</RouterLink>
      </template>
    </div>

    <div v-if="status?.cast.length" class="card">
      <h2>Your ballots</h2>
      <p class="muted">Each tracker is the hash of the ballot you sent. Find it on the board to check it was recorded.</p>
      <table>
        <tr><th>Question</th><th>Tracker</th><th>Cast</th></tr>
        <tr v-for="c in status.cast" :key="c.seq">
          <td>{{ election.questions[c.question_index]?.spec.title }}</td>
          <td class="mono">{{ c.tracker }}</td>
          <td>{{ when(c.cast_at) }}</td>
        </tr>
      </table>
    </div>

    <div class="row">
      <RouterLink class="button" :to="`/elections/${id}/board`">Bulletin board</RouterLink>
      <RouterLink v-if="election.state === 'published'" class="button primary" :to="`/elections/${id}/results`">Results</RouterLink>
    </div>

    <form class="card stack" @submit.prevent="checkTracker">
      <h2>Check a tracker</h2>
      <div class="row">
        <input v-model="tracker" placeholder="64 hexadecimal characters" class="mono" style="flex: 1" />
        <button>Check</button>
      </div>
      <p v-if="trackerError" class="error">{{ trackerError }}</p>
      <p v-if="trackerResult" :class="trackerResult.superseded ? 'warning' : 'success'">
        On the board for “{{ election.questions[trackerResult.question_index]?.spec.title }}”, cast {{ when(trackerResult.cast_at) }}.
        <template v-if="trackerResult.superseded">A later ballot of the same voter replaces it.</template>
        <template v-else>It is the voter's latest ballot for this question.</template>
      </p>
    </form>

    <div class="card">
      <h2>Questions</h2>
      <div v-for="q in election.questions" :key="q.index" class="stack">
        <h3>{{ q.index + 1 }}. {{ q.spec.title }}</h3>
        <p class="muted">
          Choose {{ q.spec.min_choices === q.spec.max_choices ? q.spec.max_choices : `${q.spec.min_choices} to ${q.spec.max_choices}` }}
          of {{ q.spec.answers.length }}. {{ q.ballots }} ballots on the board.
        </p>
        <details>
          <summary>Election parameters</summary>
          <p>evmlr election id <code>{{ q.crypto_id }}</code></p>
          <p>Parameters (SHA-256) <code>{{ q.params_key }}</code></p>
          <table>
            <tr><th>Mix server</th><th>Public key (SHA-256)</th></tr>
            <tr v-for="k in q.keys" :key="k.position">
              <td>{{ k.position }}: {{ election.mix_servers[k.position]?.node }}</td>
              <td class="mono">{{ k.fingerprint }}</td>
            </tr>
          </table>
        </details>
      </div>
    </div>
  </template>
</template>
