<script setup lang="ts">
// Edits a list of n-out-of-m questions. Answers are typed one per line.
import type { QuestionInput } from '../api.ts';

const questions = defineModel<QuestionInput[]>({ required: true });
defineProps<{ disabled?: boolean }>();

function add() {
  questions.value = [...questions.value, { title: '', description: '', answers: [], min_choices: 1, max_choices: 1 }];
}

function remove(i: number) {
  questions.value = questions.value.filter((_, j) => j !== i);
}

function setAnswers(q: QuestionInput, text: string) {
  q.answers = text.split('\n').map((a) => a.trim()).filter(Boolean);
}
</script>

<template>
  <div class="stack">
    <fieldset v-for="(q, i) in questions" :key="i" class="card stack" :disabled="disabled">
      <div class="row" style="justify-content: space-between">
        <strong>Question {{ i + 1 }}</strong>
        <button type="button" class="danger" v-if="questions.length > 1 && !disabled" @click="remove(i)">Remove</button>
      </div>
      <label><span>Question</span><input v-model="q.title" required /></label>
      <label><span>Description (optional)</span><input v-model="q.description" /></label>
      <label>
        <span>Answers, one per line</span>
        <textarea rows="4" :value="q.answers.join('\n')" @change="setAnswers(q, ($event.target as HTMLTextAreaElement).value)" />
      </label>
      <div class="row">
        <label><span>At least</span><input v-model.number="q.min_choices" type="number" min="0" /></label>
        <label><span>At most</span><input v-model.number="q.max_choices" type="number" min="1" /></label>
      </div>
      <p class="muted">At least 0 allows a blank vote.</p>
    </fieldset>
    <button type="button" v-if="!disabled" @click="add">Add a question</button>
  </div>
</template>
