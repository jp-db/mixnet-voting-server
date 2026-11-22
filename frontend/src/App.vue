<script setup lang="ts">
import { useRouter } from 'vue-router';

import { logout, session } from './session.ts';

const router = useRouter();

async function onLogout() {
  await logout();
  await router.push('/');
}
</script>

<template>
  <header class="topbar">
    <RouterLink to="/" class="brand">Mixnet voting</RouterLink>
    <nav>
      <RouterLink v-if="session.me?.can_manage" to="/admin">Manage</RouterLink>
      <template v-if="session.me">
        <span class="who">{{ session.me.display_name || session.me.external_id }}</span>
        <button class="link" @click="onLogout">Log out</button>
      </template>
      <RouterLink v-else :to="{ path: '/login', query: { next: $route.fullPath } }">Log in</RouterLink>
    </nav>
  </header>
  <main class="page">
    <!-- Keyed by path: a view loads its data when mounted, so another election needs a fresh one. -->
    <RouterView :key="$route.path" />
  </main>
</template>
