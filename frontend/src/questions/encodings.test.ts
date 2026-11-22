// node --test: checks the booth's encoder against the ballot box's test vectors.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { encode, InvalidAnswer } from './encodings.ts';
import type { QuestionSpec } from './spec.ts';

interface Vectors {
  encodings: Record<
    string,
    { spec: QuestionSpec; encode: { answer: number[]; coefficients?: number[]; error?: boolean }[] }[]
  >;
}

const vectors: Vectors = JSON.parse(
  readFileSync(new URL('../../../test-vectors/encodings.json', import.meta.url), 'utf8'),
);

for (const [name, cases] of Object.entries(vectors.encodings)) {
  test(`${name} matches the test vectors`, () => {
    for (const { spec, encode: expected } of cases) {
      for (const v of expected) {
        const what = `${spec.title}: ${JSON.stringify(v.answer)}`;
        if (v.error) assert.throws(() => encode(spec, v.answer), InvalidAnswer, what);
        else assert.deepEqual(encode(spec, v.answer), v.coefficients, what);
      }
    }
  });
}
