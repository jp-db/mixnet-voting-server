<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';

import {
  api,
  type AdminElection,
  type LifecycleAction,
  type MixnetNode,
  type QuestionInput,
  type Voter,
} from '../../api.ts';
import MixServersEditor from '../../components/MixServersEditor.vue';
import QuestionsEditor from '../../components/QuestionsEditor.vue';
import StateBadge from '../../components/StateBadge.vue';
import { message, when } from '../../format.ts';
import AdminNav from './AdminNav.vue';

const props = defineProps<{ id: string }>();
const election = ref<AdminElection | null>(null);
const nodes = ref<MixnetNode[]>([]);
const voters = ref<Voter[]>([]);
const managers = ref<string[]>([]);
const error = ref('');
const notice = ref('');

// Editable copies (draft only)
const title = ref('');
const description = ref('');
const questions = ref<QuestionInput[]>([]);
const chain = ref<string[]>([]);
const roll = ref('');
const managersText = ref('');

const draft = computed(() => election.value?.state === 'draft');
let timer: ReturnType<typeof setInterval> | undefined;

function adopt(e: AdminElection) {
  election.value = e;
  title.value = e.title;
  description.value = e.description;
  questions.value = e.questions.map((q) => ({ ...q.spec, answers: [...q.spec.answers] }));
  chain.value = e.mix_servers.map((m) => m.node);
  // Key generation and counting run in the background: poll while they do.
  clearInterval(timer);
  if (e.state === 'keygen' || e.state === 'mixing') timer = setInterval(reload, 3000);
}

async function reload() {
  try {
    adopt(await api.admin.election(props.id));
  } catch (e) {
    error.value = message(e);
  }
}

onMounted(async () => {
  try {
    const [e, n, v, m] = await Promise.all([
      api.admin.election(props.id),
      api.admin.nodes(),
      api.admin.voters(props.id),
      api.admin.managers(props.id),
    ]);
    adopt(e);
    nodes.value = n;
    voters.value = v;
    managers.value = m;
    managersText.value = m.join('\n');
  } catch (e) {
    error.value = message(e);
  }
});
onUnmounted(() => clearInterval(timer));

async function run(what: string, fn: () => Promise<unknown>) {
  error.value = '';
  notice.value = '';
  try {
    await fn();
    notice.value = what;
  } catch (e) {
    error.value = message(e);
  }
}

const save = () =>
  run('Saved.', async () =>
    adopt(
      await api.admin.update(props.id, {
        title: title.value,
        description: description.value,
        questions: questions.value,
        mix_servers: chain.value,
      }),
    ),
  );

/** Lines of ``provider:external_id``, optionally followed by ``,email,display name``. */
function parseRoll(text: string) {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const [who, email, ...name] = line.split(',').map((s) => s.trim());
      const at = who.indexOf(':');
      if (at <= 0) throw new Error(`“${line}”: write provider:username, e.g. password:alice or cas:jdoe`);
      return { provider: who.slice(0, at), external_id: who.slice(at + 1), email: email || null, display_name: name.join(',') || null };
    });
}

const addVoters = () =>
  run('Voters added.', async () => {
    await api.admin.addVoters(props.id, parseRoll(roll.value));
    voters.value = await api.admin.voters(props.id);
    roll.value = '';
    await reload();
  });

const saveManagers = () =>
  run('Managers saved.', async () => {
    managers.value = await api.admin.setManagers(props.id, managersText.value.split('\n').map((s) => s.trim()).filter(Boolean));
  });

const ACTIONS: { action: LifecycleAction; label: string; when: string[]; confirm?: string }[] = [
  { action: 'freeze', label: 'Freeze and generate keys', when: ['draft'], confirm: 'Questions, mix servers and the voter roll can no longer change once keys exist (unless you unfreeze before opening). Continue?' },
  { action: 'freeze', label: 'Retry key generation', when: ['keygen'] },
  { action: 'unfreeze', label: 'Back to draft', when: ['keygen'] },
  { action: 'open', label: 'Open voting', when: ['frozen'] },
  { action: 'close', label: 'Close voting', when: ['open'], confirm: 'Close the election? No more ballots will be accepted.' },
  { action: 'count', label: 'Count', when: ['closed'] },
  { action: 'retry-count', label: 'Retry the count', when: ['mixing'] },
  { action: 'publish', label: 'Publish the results', when: ['tallied'], confirm: 'Publish the results, the transcripts and the mix records?' },
];

const actions = computed(() => ACTIONS.filter((a) => election.value && a.when.includes(election.value.state)));

function act(a: (typeof ACTIONS)[number]) {
  if (a.confirm && !window.confirm(a.confirm)) return;
  return run(`${a.label}: done.`, async () => adopt(await api.admin.act(props.id, a.action)));
}
</script>

<template>
  <AdminNav />
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="notice" class="success">{{ notice }}</p>
  <template v-if="election">
    <div class="row">
      <h1>{{ election.title }}</h1>
      <StateBadge :state="election.state" />
      <RouterLink v-if="!['draft', 'keygen'].includes(election.state)" :to="`/elections/${id}`">Public page</RouterLink>
    </div>
    <p v-if="election.error" class="error">{{ election.error }}</p>

    <div class="card stack">
      <h2>Lifecycle</h2>
      <p class="muted">
        Created {{ when(election.created_at) }}<template v-if="election.frozen_at"> · keys {{ when(election.frozen_at) }}</template
        ><template v-if="election.opened_at"> · opened {{ when(election.opened_at) }}</template
        ><template v-if="election.closed_at"> · closed {{ when(election.closed_at) }}</template
        ><template v-if="election.tallied_at"> · counted {{ when(election.tallied_at) }}</template
        ><template v-if="election.published_at"> · published {{ when(election.published_at) }}</template>
      </p>
      <p v-if="election.state === 'keygen'" class="muted">The mix servers are generating their keys…</p>
      <div class="row">
        <button v-for="a in actions" :key="a.action + a.label" :class="{ primary: !a.action.includes('un') }" @click="act(a)">
          {{ a.label }}
        </button>
      </div>
    </div>

    <form class="card stack" @submit.prevent="save">
      <h2>Election</h2>
      <label><span>Title</span><input v-model="title" :disabled="!draft" required /></label>
      <label><span>Description</span><textarea v-model="description" :disabled="!draft" rows="2" /></label>
      <h3>Questions</h3>
      <QuestionsEditor v-model="questions" :disabled="!draft" />
      <h3>Mix servers</h3>
      <MixServersEditor v-model="chain" :nodes="nodes" :disabled="!draft" />
      <button v-if="draft" class="primary">Save</button>
    </form>

    <div class="card stack" v-if="election.questions.some((q) => q.keys.length || q.tally)">
      <h2>Keys and counting</h2>
      <div v-for="q in election.questions" :key="q.id" class="stack">
        <h3>{{ q.index + 1 }}. {{ q.spec.title }}</h3>
        <p v-if="q.crypto_id" class="muted">evmlr election id <code>{{ q.crypto_id }}</code></p>
        <table v-if="q.keys.length">
          <tr><th>Server</th><th>Public key (SHA-256)</th><th v-if="q.tally">Mix</th></tr>
          <tr v-for="k in q.keys" :key="k.position">
            <td>{{ k.position }}: {{ election.mix_servers[k.position]?.node }}</td>
            <td class="mono">{{ k.fingerprint }}</td>
            <td v-if="q.tally">
              <template v-if="q.tally.steps[k.position]?.record_key">
                {{ q.tally.steps[k.position].info?.n_in }} → {{ q.tally.steps[k.position].info?.n_out }}
              </template>
              <span v-else-if="q.tally.steps[k.position]" class="muted">mixing…</span>
              <span v-else class="muted">waiting</span>
            </td>
          </tr>
        </table>
        <template v-if="q.tally">
          <p v-if="q.tally.state === 'failed'" class="error">Count failed: {{ q.tally.error }}</p>
          <p v-else-if="q.tally.state === 'pending'" class="muted">Counting…</p>
          <p v-else class="success">
            Counted and verified: {{ q.tally.result?.counts.map((c, i) => `${q.spec.answers[i]} ${c}`).join(', ') }}<template
              v-if="q.tally.result?.blank"
              >, blank {{ q.tally.result.blank }}</template
            >.
            <template v-if="q.tally.discarded?.length"> {{ q.tally.discarded.length }} malformed left out.</template>
          </p>
        </template>
      </div>
    </div>

    <div class="card stack">
      <h2>Voter roll ({{ election.voter_count }})</h2>
      <div class="table-wrap" v-if="voters.length">
        <table>
          <tr><th>Login</th><th>Name</th><th>Email</th><th>Voter id</th></tr>
          <tr v-for="v in voters" :key="v.id">
            <td>{{ v.provider }}:{{ v.external_id }}</td>
            <td>{{ v.display_name }}</td>
            <td>{{ v.email }}</td>
            <td class="mono">{{ v.voter_id }}</td>
          </tr>
        </table>
      </div>
      <form v-if="draft" class="stack" @submit.prevent="addVoters">
        <label>
          <span>Add voters, one per line: <code>provider:username</code>, optionally <code>,email,name</code></span>
          <textarea v-model="roll" rows="4" placeholder="cas:jdoe,jdoe@example.edu,Jane Doe" class="mono" />
        </label>
        <button :disabled="!roll.trim()">Add to the roll</button>
      </form>
    </div>

    <form class="card stack" @submit.prevent="saveManagers">
      <h2>Managers</h2>
      <label>
        <span>One identity per line (<code>provider:username</code>); each must have logged in once</span>
        <textarea v-model="managersText" rows="3" class="mono" />
      </label>
      <button>Save managers</button>
    </form>
  </template>
</template>
