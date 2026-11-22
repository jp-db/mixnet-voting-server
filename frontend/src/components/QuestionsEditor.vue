<script setup lang="ts">
// Edits a list of n-out-of-m questions. Each answer is its own entry, added one at a time.
import { reactive } from 'vue';

import type { QuestionInput } from '../api.ts';

const questions = defineModel<QuestionInput[]>({ required: true });
defineProps<{ disabled?: boolean }>();

/** The answer being typed for each question, before it is added. */
const pending = reactive<Record<number, string>>({});
const problems = reactive<Record<number, string>>({});

function add() {
  questions.value = [...questions.value, { title: '', description: '', answers: [], min_choices: 1, max_choices: 1 }];
}

/** The typed-in answers are kept by position: clear them when questions move. */
function forgetPending() {
  for (const k of Object.keys(pending)) delete pending[Number(k)];
  for (const k of Object.keys(problems)) delete problems[Number(k)];
}

function remove(i: number) {
  questions.value = questions.value.filter((_, j) => j !== i);
  forgetPending();
}

function moveQuestion(i: number, by: number) {
  const next = [...questions.value];
  [next[i], next[i + by]] = [next[i + by], next[i]];
  questions.value = next;
  forgetPending();
}

function addAnswer(i: number) {
  const q = questions.value[i];
  const answer = (pending[i] ?? '').trim();
  problems[i] = '';
  if (!answer) return;
  if (q.answers.includes(answer)) {
    problems[i] = `“${answer}” is already an answer.`;
    return;
  }
  q.answers = [...q.answers, answer];
  pending[i] = '';
}

function removeAnswer(q: QuestionInput, j: number) {
  q.answers = q.answers.filter((_, k) => k !== j);
  if (q.max_choices > Math.max(q.answers.length, 1)) q.max_choices = Math.max(q.answers.length, 1);
  if (q.min_choices > q.max_choices) q.min_choices = q.max_choices;
}

function moveAnswer(q: QuestionInput, j: number, by: number) {
  const next = [...q.answers];
  [next[j], next[j + by]] = [next[j + by], next[j]];
  q.answers = next;
}
</script>

<template>
  <div class="stack">
    <p v-if="!questions.length" class="muted">No questions yet. The election cannot be frozen without at least one.</p>
    <fieldset v-for="(q, i) in questions" :key="i" class="card stack" :disabled="disabled">
      <div class="row" style="justify-content: space-between">
        <strong>Question {{ i + 1 }}</strong>
        <div v-if="!disabled" class="row">
          <button type="button" :disabled="i === 0" title="Move up" @click="moveQuestion(i, -1)">↑</button>
          <button type="button" :disabled="i === questions.length - 1" title="Move down" @click="moveQuestion(i, 1)">↓</button>
          <button type="button" class="danger" @click="remove(i)">Remove</button>
        </div>
      </div>
      <label><span>Question</span><input v-model="q.title" required /></label>
      <label><span>Description (optional)</span><input v-model="q.description" /></label>
      <div>
        <span class="muted">Answers</span>
        <ol v-if="q.answers.length" class="answers">
          <li v-for="(answer, j) in q.answers" :key="answer" class="row">
            <span style="flex: 1">{{ answer }}</span>
            <template v-if="!disabled">
              <button type="button" :disabled="j === 0" title="Move up" @click="moveAnswer(q, j, -1)">↑</button>
              <button type="button" :disabled="j === q.answers.length - 1" title="Move down" @click="moveAnswer(q, j, 1)">↓</button>
              <button type="button" class="danger" @click="removeAnswer(q, j)">Remove</button>
            </template>
          </li>
        </ol>
        <p v-else class="muted">No answers yet.</p>
        <div v-if="!disabled" class="row">
          <input
            v-model="pending[i]"
            :aria-label="`New answer for question ${i + 1}`"
            placeholder="New answer"
            style="flex: 1"
            @keydown.enter.prevent="addAnswer(i)"
          />
          <button type="button" :disabled="!(pending[i] ?? '').trim()" @click="addAnswer(i)">Add answer</button>
        </div>
        <p v-if="problems[i]" class="warning">{{ problems[i] }}</p>
      </div>
      <div class="row">
        <label><span>At least</span><input v-model.number="q.min_choices" type="number" min="0" :max="q.max_choices" /></label>
        <label><span>At most</span><input v-model.number="q.max_choices" type="number" min="1" :max="Math.max(q.answers.length, 1)" /></label>
      </div>
      <p class="muted">At least 0 allows a blank vote.</p>
    </fieldset>
    <button type="button" v-if="!disabled" @click="add">Add a question</button>
  </div>
</template>
