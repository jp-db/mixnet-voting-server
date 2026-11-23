<script setup lang="ts">
// The voting booth. With ``simulate`` it is the manager's rehearsal of it:
// before the keys exist it checks and shows the encoding of the answers;
// once frozen it also runs evmlr (WebAssembly) on the real parameters,
// checks the ballots in the browser and on the ballot box, and keeps nothing.
import { computed, onMounted, reactive, ref } from 'vue';

import { api, type Cast, type ElectionState, type TestBallotResult } from '../api.ts';
import { cast, checkBallot } from '../crypto/client.ts';
import { message, when } from '../format.ts';
import { encode, InvalidAnswer } from '../questions/encodings.ts';
import type { QuestionSpec } from '../questions/spec.ts';

const props = defineProps<{ id: string; simulate?: boolean }>();

interface BoothQuestion {
  index: number;
  spec: QuestionSpec;
  /** Whether the question has public parameters (from freezing on). */
  hasParams: boolean;
}

interface BoothElection {
  title: string;
  state: ElectionState;
  closes_at: string | null;
  questions: BoothQuestion[];
}

/** One question's outcome in a simulation. */
interface Rehearsal {
  index: number;
  coefficients: number[];
  ballotBytes?: number;
  encryptMs?: number;
  /** Voter id the browser's evmlr check read back from the ballot. */
  browserCheck?: string;
  server?: TestBallotResult;
}

type Step = 'choose' | 'review' | 'casting' | 'done';
const step = ref<Step>('choose');
const election = ref<BoothElection | null>(null);
const voterId = ref<string | null>(null);
const eligible = ref(false);
const error = ref('');
const progress = ref('');
const receipts = ref<Cast[]>([]);
const rehearsal = ref<Rehearsal[]>([]);

/** Per question: the chosen answer indices, and whether the voter skips it. */
const chosen = reactive<Record<number, number[]>>({});
const skipped = reactive<Record<number, boolean>>({});

/** A voter id for simulated ballots: random, in evmlr's WebAssembly range, never on a roll. */
function simulatedVoterId(): string {
  return String(1 + Math.floor(Math.random() * (2 ** 31 - 2)));
}

onMounted(async () => {
  try {
    if (props.simulate) {
      const e = await api.admin.election(props.id);
      election.value = {
        title: e.title,
        state: e.state,
        closes_at: e.closes_at,
        questions: e.questions.map((q) => ({ index: q.index, spec: q.spec, hasParams: q.params_key !== null })),
      };
      eligible.value = true;
      voterId.value = simulatedVoterId();
    } else {
      const e = await api.election(props.id);
      election.value = {
        title: e.title,
        state: e.state,
        closes_at: e.closes_at,
        questions: e.questions.map((q) => ({ index: q.index, spec: q.spec, hasParams: true })),
      };
      const status = await api.voterStatus(props.id);
      eligible.value = status.eligible;
      voterId.value = status.voter_id;
    }
    for (const q of election.value.questions) {
      chosen[q.index] = [];
      skipped[q.index] = false;
    }
  } catch (e) {
    error.value = message(e);
  }
});

/** The simulation encrypts only once every question has its parameters. */
const withCrypto = computed(() => !!election.value?.questions.length && election.value.questions.every((q) => q.hasParams));

const blocked = computed(() => {
  const e = election.value;
  if (!e) return '';
  if (!e.questions.length) return 'This election has no questions yet.';
  if (props.simulate) return '';
  if (e.state !== 'open') return 'This election is not open for voting.';
  if (e.closes_at && new Date(e.closes_at) <= new Date()) return 'Voting has closed.';
  if (!eligible.value) return "You are not on this election's voter roll.";
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
  if (!election.value) return;
  if (props.simulate) return rehearse();
  step.value = 'casting';
  error.value = '';
  try {
    if (!voterId.value) {
      // The election is open to the voter's login method: join its roll, which gives the voter id.
      progress.value = 'Joining the voter roll…';
      voterId.value = (await api.join(props.id)).voter_id;
      if (!voterId.value) throw new Error('the ballot box gave no voter id');
    }
    const ballots = new Map<number, Uint8Array<ArrayBuffer>>();
    const trackers = new Map<number, string>();
    for (const [n, q] of answered.value.entries()) {
      progress.value = `Encrypting your answer to “${q.spec.title}” (${n + 1} of ${answered.value.length})…`;
      const pp = await api.params(props.id, q.index);
      const { ballot, tracker } = await cast(pp, voterId.value, encode(q.spec, chosen[q.index]));
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

/** The simulation: the booth's own steps, then the checks the ballot box would make, and nothing kept. */
async function rehearse() {
  const id = voterId.value;
  if (!election.value || !id) return;
  step.value = 'casting';
  error.value = '';
  const out: Rehearsal[] = answered.value.map((q) => ({ index: q.index, coefficients: encode(q.spec, chosen[q.index]) }));
  try {
    if (withCrypto.value) {
      const ballots = new Map<number, Uint8Array<ArrayBuffer>>();
      const trackers = new Map<number, string>();
      for (const [n, r] of out.entries()) {
        const q = election.value.questions[r.index];
        progress.value = `Encrypting the answer to “${q.spec.title}” with evmlr (${n + 1} of ${out.length})…`;
        const pp = await api.params(props.id, q.index);
        const started = performance.now();
        const { ballot, tracker } = await cast(pp, id, r.coefficients);
        r.encryptMs = Math.round(performance.now() - started);
        r.ballotBytes = ballot.length;
        progress.value = `Checking the ballot for “${q.spec.title}” in the browser…`;
        r.browserCheck = await checkBallot(pp, ballot);
        if (r.browserCheck !== id) throw new Error(`question ${q.index + 1}: the ballot claims voter ${r.browserCheck}, not ${id}`);
        ballots.set(q.index, ballot);
        trackers.set(q.index, tracker);
      }
      progress.value = 'Having the ballot box check the ballots (it keeps nothing)…';
      for (const result of await api.admin.testBallots(props.id, ballots)) {
        const r = out.find((x) => x.index === result.question_index);
        if (!r) continue;
        r.server = result;
        if (result.voter_id !== id || result.tracker !== trackers.get(result.question_index)) {
          throw new Error(`question ${result.question_index + 1}: the ballot box saw a different ballot`);
        }
      }
    }
    rehearsal.value = out;
    step.value = 'done';
  } catch (e) {
    error.value = message(e);
    step.value = 'review';
  }
}

function again() {
  for (const q of election.value?.questions ?? []) {
    chosen[q.index] = [];
    skipped[q.index] = false;
  }
  rehearsal.value = [];
  receipts.value = [];
  if (props.simulate) voterId.value = simulatedVoterId();
  step.value = 'choose';
}

/** The coefficients that carry the answer (the rest of the 512 are 0). */
function shown(r: Rehearsal): string {
  return `[${r.coefficients.join(', ')}]`;
}
</script>

<template>
  <div v-if="simulate" class="card stack simulation">
    <strong>Booth simulation</strong>
    <p class="muted" v-if="withCrypto">
      The ballots are encrypted with evmlr on the real parameters and checked as casting would check them,
      as simulated voter {{ voterId }}. Nothing is put on the board.
    </p>
    <p class="muted" v-else>
      The election has no keys yet: the simulation checks the answers and shows how they are encoded.
      Once it is frozen, the simulation also encrypts and checks the ballots with evmlr. Nothing is recorded.
    </p>
    <RouterLink :to="`/admin/elections/${id}`">Back to the election</RouterLink>
  </div>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="election">
    <h1>{{ election.title }}</h1>
    <p v-if="blocked" class="error">{{ blocked }}</p>

    <template v-else-if="step === 'choose'">
      <p v-if="election.closes_at && !simulate" class="muted">Voting closes {{ when(election.closes_at) }}.</p>
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
        <h2>{{ simulate ? 'The answers' : 'Your answers' }}</h2>
        <div v-for="q in answered" :key="q.index">
          <strong>{{ q.spec.title }}:</strong>
          {{ chosen[q.index].length ? chosen[q.index].map((i) => q.spec.answers[i]).join(', ') : 'blank vote' }}
        </div>
        <p class="muted" v-if="!simulate">
          Your answers are encrypted on this device before they are sent. Nobody, not even the ballot box, can
          read them; the mix servers together decrypt only the shuffled votes. Voting again later replaces these ballots.
        </p>
        <p v-if="step === 'casting'">{{ progress }}</p>
        <div class="row">
          <button :disabled="step === 'casting'" @click="step = 'choose'">Change</button>
          <button class="primary" :disabled="step === 'casting'" @click="castAll">
            {{ simulate ? (withCrypto ? 'Simulate casting' : 'Check and encode') : 'Cast my ballot' }}
          </button>
        </div>
      </div>
    </template>

    <template v-else-if="simulate">
      <div class="card stack">
        <h2 class="success">{{ withCrypto ? 'The ballots would be accepted' : 'The answers are valid' }}</h2>
        <div class="table-wrap">
          <table>
            <tr>
              <th>Question</th>
              <th>Encoded vote</th>
              <template v-if="withCrypto"><th>Ballot</th><th>Checks</th><th>Tracker</th></template>
            </tr>
            <tr v-for="r in rehearsal" :key="r.index">
              <td>{{ election.questions[r.index]?.spec.title }}</td>
              <td class="mono">{{ shown(r) }}</td>
              <template v-if="withCrypto">
                <td>{{ ((r.ballotBytes ?? 0) / 1024).toFixed(0) }} KB, encrypted in {{ r.encryptMs }} ms</td>
                <td>browser ✓ · ballot box {{ r.server ? '✓' : '—' }}</td>
                <td class="mono">{{ r.server?.tracker }}</td>
              </template>
            </tr>
          </table>
        </div>
        <p class="muted">
          The encoded vote is the first coefficients of the polynomial evmlr encrypts (1 for each chosen answer);
          the other coefficients up to 512 are 0.
          <template v-if="withCrypto">These ballots were discarded: the board has not changed.</template>
        </p>
        <div class="row"><button class="primary" @click="again">Simulate again</button></div>
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
