<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { api, type MixnetNode } from '../../api.ts';
import { message } from '../../format.ts';
import AdminNav from './AdminNav.vue';

const nodes = ref<MixnetNode[]>([]);
const error = ref('');
const name = ref('');
const url = ref('');
const token = ref('');

async function load() {
  nodes.value = await api.admin.nodes();
}
onMounted(() => load().catch((e) => (error.value = message(e))));

async function add() {
  error.value = '';
  try {
    await api.admin.addNode(name.value, url.value, token.value);
    name.value = url.value = token.value = '';
    await load();
  } catch (e) {
    error.value = message(e);
  }
}
</script>

<template>
  <AdminNav />
  <h1>Mixnet nodes</h1>
  <p class="muted">The default nodes come from the server's settings; register others here for managers to pick.</p>
  <p v-if="error" class="error">{{ error }}</p>
  <div class="card table-wrap">
    <table>
      <tr><th>Name</th><th>URL</th><th>Default</th></tr>
      <tr v-for="n in nodes" :key="n.id">
        <td>{{ n.name }}</td>
        <td class="mono">{{ n.url }}</td>
        <td>{{ n.is_default ? `yes, position ${n.default_position}` : '' }}</td>
      </tr>
    </table>
  </div>
  <form class="card stack" @submit.prevent="add">
    <h2>Register a node</h2>
    <p class="muted">Registering an existing name replaces its URL and token.</p>
    <label><span>Name</span><input v-model="name" required /></label>
    <label><span>API URL</span><input v-model="url" placeholder="https://mixnet.example.org" required /></label>
    <label><span>API token (the node's MIXNET_NODE_API_TOKEN)</span><input v-model="token" type="password" minlength="32" required /></label>
    <button class="primary">Register</button>
  </form>
</template>
