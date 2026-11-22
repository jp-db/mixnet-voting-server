<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import { api, type Cast, type PublicElection, type VoterStatus } from '../api.ts';
import { cast } from '../crypto/client.ts';
import { message } from '../format.ts';
import { encode, InvalidAnswer } from '../questions/encodings.ts';

const props = defineProps<{ id: string }>();

type Step = 'choose' | 'review' | 'casting' | 'done';
const step = ref<Step>('choose');
const election = ref<PublicElection | null>(null);
const status = ref<VoterStatus | null>(null);
const error = ref('');
const progress = ref('');
const receipts = ref<Cast[]>([]);

/** Per question: the chosen answer indices, and whether the voter skips it. */
const chosen = reactive<Record<number, number[]>>({});
const skipped = reactive<Record<number, boolean>>({});

onMounted(async () => {
  try {
    election.value = await api.election(props.id);
    status.value = await api.voterStatus(props.id);
    for (const q of election.value.questions) {
      chosen[q.index] = [];
      skipped[q.index] = false;
    }
  } catch (e) {
    error.value = message(e);
  }
});

const blocked = computed(() => {
  if (!election.value || !status.value) return '';
  if (election.value.state !== 'open') return 'This election is not open for voting.';
  if (!status.value.eligible) return "You are not on this election's voter roll.";
  return '';
});

function toggle(index: number, answer: number, single: boolean) {
  const list = chosen[index];
  const at = list.indexOf(answer);
  if (single) chosen[index] = at >= 0 ? [] : [answer];
  else if (at >= 0) list.splice(at, 1);
  else list.push(answer);
}

/** Why the question's selection cannot be cast, or '' if it can. */
function problem(index: number): string {
  if (skipped[index] || !election.value) return '';
  try {
    encode(election.value.questions[index].spec, chosen[index]);
    return '';
  } catch (e) {
    return e instanceof InvalidAnswer ? e.message : message(e);
  }
}

const answered = computed(() => election.value?.questions.filter((q) => !skipped[q.index]) ?? []);
const canReview = computed(
  () => answered.value.length > 0 && answered.value.every((q) => problem(q.index) === ''),
);

async function castAll() {
  if (!election.value || !status.value?.voter_id) return;
  step.value = 'casting';
  error.value = '';
  try {
    const ballots = new Map<number, Uint8Array<ArrayBuffer>>();
    const trackers = new Map<number, string>();
    for (const [n, q] of answered.value.entries()) {
      progress.value = `Encrypting your answer to “${q.spec.title}” (${n + 1} of ${answered.value.length})…`;
      const pp = await api.params(props.id, q.index);
      const { ballot, tracker } = await cast(pp, status.value.voter_id, encode(q.spec, chosen[q.index]));
      ballots.set(q.index, ballot);
      trackers.set(q.index, tracker);
    }
    progress.value = 'Sending your ballots…';
    receipts.value = await api.cast(props.id, ballots);
    for (const r of receipts.value) {
      if (trackers.get(r.question_index) !== r.tracker) {
        throw new Error('the ballot box recorded a ballot other than the one sent: do not trust this receipt');
      }
    }
    step.value = 'done';
  } catch (e) {
    error.value = message(e);
    step.value = 'review';
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="election && status">
    <h1>{{ election.title }}</h1>
    <p v-if="blocked" class="error">{{ blocked }}</p>

    <template v-else-if="step === 'choose'">
      <div v-for="q in election.questions" :key="q.index" class="card stack">
        <h2>{{ q.index + 1 }}. {{ q.spec.title }}</h2>
        <p v-if="q.spec.description">{{ q.spec.description }}</p>
        <p class="muted">
          Choose {{ q.spec.min_choices === q.spec.max_choices ? q.spec.max_choices : `${q.spec.min_choices} to ${q.spec.max_choices}` }}.
          <template v-if="q.spec.min_choices === 0">Choosing none casts a blank vote.</template>
        </p>
        <fieldset :disabled="skipped[q.index]" style="border: 0; padding: 0; margin: 0">
          <label v-for="(answer, i) in q.spec.answers" :key="i" class="choice">
            <input
              :type="q.spec.max_choices === 1 ? 'radio' : 'checkbox'"
              :name="`q${q.index}`"
              :checked="chosen[q.index].includes(i)"
              @click="toggle(q.index, i, q.spec.max_choices === 1)"
            />
            {{ answer }}
          </label>
        </fieldset>
        <label class="choice muted"><input type="checkbox" v-model="skipped[q.index]" /> Do not vote on this question now</label>
        <p v-if="problem(q.index)" class="warning">{{ problem(q.index) }}</p>
      </div>
      <button class="primary" :disabled="!canReview" @click="step = 'review'">Review</button>
    </template>

    <template v-else-if="step === 'review' || step === 'casting'">
      <div class="card stack">
        <h2>Your answers</h2>
        <div v-for="q in answered" :key="q.index">
          <strong>{{ q.spec.title }}:</strong>
          {{ chosen[q.index].length ? chosen[q.index].map((i) => q.spec.answers[i]).join(', ') : 'blank vote' }}
        </div>
        <p class="muted">
          Your answers are encrypted on this device before they are sent. Nobody, not even the ballot box, can
          read them; the mix servers together decrypt only the shuffled votes. Voting again later replaces these ballots.
        </p>
        <p v-if="step === 'casting'">{{ progress }}</p>
        <div class="row">
          <button :disabled="step === 'casting'" @click="step = 'choose'">Change</button>
          <button class="primary" :disabled="step === 'casting'" @click="castAll">Cast my ballot</button>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="card stack">
        <h2 class="success">Your ballot was cast</h2>
        <p>Keep these trackers. Each is the hash of a ballot you sent: find it on the bulletin board to check it was recorded.</p>
        <table>
          <tr><th>Question</th><th>Tracker</th></tr>
          <tr v-for="r in receipts" :key="r.seq">
            <td>{{ election.questions[r.question_index]?.spec.title }}</td>
            <td class="mono">{{ r.tracker }}</td>
          </tr>
        </table>
        <RouterLink :to="`/elections/${id}`">Back to the election</RouterLink>
      </div>
    </template>
  </template>
</template>
