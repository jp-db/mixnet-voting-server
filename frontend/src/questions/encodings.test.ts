// node --test: checks the booth's encoder and the verifier's decoder and count
// against the ballot box's test vectors.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { decode, encode, InvalidAnswer, Malformed } from './encodings.ts';
import type { QuestionSpec } from './spec.ts';
import { tally } from './tally.ts';

interface Case {
  spec: QuestionSpec;
  encode: { answer: number[]; coefficients?: number[]; error?: boolean }[];
  decode: { coefficients: number[]; answer?: number[]; malformed?: string }[];
  tally: { votes: number[][]; result: unknown; discarded: unknown };
}

const vectors: { vote_len: number; encodings: Record<string, Case[]> } = JSON.parse(
  readFileSync(new URL('../../../test-vectors/encodings.json', import.meta.url), 'utf8'),
);
const pad = (c: number[]) => [...c, ...Array(vectors.vote_len - c.length).fill(0)];

for (const [name, cases] of Object.entries(vectors.encodings)) {
  test(`${name} encodes as the ballot box expects`, () => {
    for (const { spec, encode: expected } of cases) {
      for (const v of expected) {
        const what = `${spec.title}: ${JSON.stringify(v.answer)}`;
        if (v.error) assert.throws(() => encode(spec, v.answer), InvalidAnswer, what);
        else assert.deepEqual(encode(spec, v.answer), v.coefficients, what);
      }
    }
  });

  test(`${name} decodes as the ballot box does`, () => {
    for (const { spec, decode: expected } of cases) {
      for (const v of expected) {
        const what = `${spec.title}: ${JSON.stringify(v.coefficients)}`;
        const run = () => decode(spec, pad(v.coefficients), vectors.vote_len);
        if (v.malformed) assert.throws(run, (e) => e instanceof Malformed && e.reason === v.malformed, what);
        else assert.deepEqual(run(), v.answer, what);
      }
    }
  });

  test(`${name} counts as the ballot box does`, () => {
    for (const { spec, tally: expected } of cases) {
      const { result, discarded } = tally(spec, expected.votes.map(pad), vectors.vote_len);
      assert.deepEqual(result, expected.result, spec.title);
      assert.deepEqual(discarded, expected.discarded, spec.title);
    }
  });
}
