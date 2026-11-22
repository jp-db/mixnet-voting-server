<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { api, type Account } from '../../api.ts';
import { message, when } from '../../format.ts';
import AdminNav from './AdminNav.vue';

const accounts = ref<Account[]>([]);
const error = ref('');
const notice = ref('');
const username = ref('');
const password = ref('');
const email = ref('');
const displayName = ref('');

async function load() {
  accounts.value = await api.admin.accounts();
}
onMounted(() => load().catch((e) => (error.value = message(e))));

async function setRole(a: Account, role: 'is_admin' | 'can_manage', value: boolean) {
  error.value = '';
  try {
    Object.assign(a, await api.admin.updateAccount(a.id, { [role]: value }));
  } catch (e) {
    error.value = message(e);
    await load();
  }
}

async function savePassword() {
  error.value = notice.value = '';
  try {
    await api.admin.setPasswordLogin({
      username: username.value,
      password: password.value,
      email: email.value || null,
      display_name: displayName.value || null,
    });
    notice.value = `The login ${username.value} is set. Give the password to its owner over a separate channel.`;
    username.value = password.value = email.value = displayName.value = '';
  } catch (e) {
    error.value = message(e);
  }
}
</script>

<template>
  <AdminNav />
  <h1>Accounts</h1>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="notice" class="success">{{ notice }}</p>
  <div class="card table-wrap">
    <p class="muted">An account appears after its first login. Voting needs no role: the voter roll decides.</p>
    <table>
      <tr><th>Identity</th><th>Name</th><th>Last login</th><th>Manager</th><th>Admin</th></tr>
      <tr v-for="a in accounts" :key="a.id">
        <td class="mono">{{ a.identity }}</td>
        <td>{{ a.display_name }}</td>
        <td>{{ when(a.last_login_at) }}</td>
        <td><input type="checkbox" :checked="a.can_manage" @change="setRole(a, 'can_manage', ($event.target as HTMLInputElement).checked)" /></td>
        <td><input type="checkbox" :checked="a.is_admin" @change="setRole(a, 'is_admin', ($event.target as HTMLInputElement).checked)" /></td>
      </tr>
    </table>
  </div>
  <form class="card stack" @submit.prevent="savePassword">
    <h2>Password login</h2>
    <p class="muted">Creates a username-and-password login, or sets a new password for an existing one.</p>
    <label><span>Username</span><input v-model="username" required pattern="[^:\s]+" /></label>
    <label><span>Password (at least 10 characters)</span><input v-model="password" type="password" minlength="10" autocomplete="new-password" required /></label>
    <label><span>Email (optional)</span><input v-model="email" type="email" /></label>
    <label><span>Name (optional)</span><input v-model="displayName" /></label>
    <button class="primary">Save</button>
  </form>
</template>
