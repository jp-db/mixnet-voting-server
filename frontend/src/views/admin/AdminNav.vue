<script setup lang="ts">
import { session } from '../../session.ts';
</script>

<template>
  <nav class="row" style="margin-bottom: 1rem">
    <RouterLink to="/admin">Elections</RouterLink>
    <template v-if="session.me?.is_admin">
      <RouterLink to="/admin/nodes">Mixnet nodes</RouterLink>
      <RouterLink to="/admin/accounts">Accounts</RouterLink>
    </template>
  </nav>
</template>
