<script setup lang="ts">
// Picks the mix servers, in mixing order, from the registered nodes.
import { computed } from 'vue';

import type { MixnetNode } from '../api.ts';

const chain = defineModel<string[]>({ required: true });
const props = defineProps<{ nodes: MixnetNode[]; disabled?: boolean }>();

const unused = computed(() => props.nodes.filter((n) => !chain.value.includes(n.name)));
const defaults = computed(() =>
  props.nodes.filter((n) => n.is_default).sort((a, b) => (a.default_position ?? 0) - (b.default_position ?? 0)),
);

function move(i: number, by: number) {
  const next = [...chain.value];
  [next[i], next[i + by]] = [next[i + by], next[i]];
  chain.value = next;
}
</script>

<template>
  <div class="stack">
    <ol>
      <li v-for="(name, i) in chain" :key="name" class="row">
        <span style="flex: 1">{{ name }}</span>
        <template v-if="!disabled">
          <button type="button" :disabled="i === 0" @click="move(i, -1)">↑</button>
          <button type="button" :disabled="i === chain.length - 1" @click="move(i, 1)">↓</button>
          <button type="button" class="danger" @click="chain = chain.filter((n) => n !== name)">Remove</button>
        </template>
      </li>
    </ol>
    <div v-if="!disabled" class="row">
      <button v-for="n in unused" :key="n.id" type="button" @click="chain = [...chain, n.name]">Add {{ n.name }}</button>
      <button type="button" @click="chain = defaults.map((n) => n.name)">Use the defaults</button>
    </div>
    <p class="muted">Server 0 mixes first. The votes stay secret as long as one of these servers is honest.</p>
  </div>
</template>
