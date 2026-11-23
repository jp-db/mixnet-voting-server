<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { api, ApiError, type Provider } from '../api.ts';
import { message } from '../format.ts';
import { refreshSession } from '../session.ts';

const route = useRoute();
const router = useRouter();
const providers = ref<Provider[]>([]);
const username = ref('');
const password = ref('');
const error = ref('');
const busy = ref(false);

/** Only paths on this site, as the server enforces for CAS. */
const next = computed(() => {
  const n = typeof route.query.next === 'string' ? route.query.next : '/';
  return n.startsWith('/') && !n.startsWith('//') && !n.includes('\\') ? n : '/';
});
const hasPassword = computed(() => providers.value.some((p) => p.name === 'password'));
const casServices = computed(() => providers.value.filter((p) => p.kind === 'cas'));

onMounted(async () => {
  try {
    providers.value = await api.providers();
  } catch (e) {
    error.value = message(e);
  }
});

async function submit() {
  busy.value = true;
  error.value = '';
  try {
    await api.passwordLogin(username.value, password.value);
    await refreshSession();
    await router.replace(next.value);
  } catch (e) {
    error.value = e instanceof ApiError && e.status === 401 ? 'Wrong username or password.' : message(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <h1>Log in</h1>
  <p v-if="error" class="error">{{ error }}</p>
  <div v-if="casServices.length" class="card row">
    <a v-for="p in casServices" :key="p.name" class="button primary" :href="api.loginUrl(p, next)">Log in with {{ p.label }}</a>
  </div>
  <form v-if="hasPassword" class="card stack" @submit.prevent="submit">
    <label><span>Username</span><input v-model="username" autocomplete="username" required /></label>
    <label><span>Password</span><input v-model="password" type="password" autocomplete="current-password" required /></label>
    <button class="primary" :disabled="busy">Log in</button>
  </form>
</template>
