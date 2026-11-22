// Answers to evmlr vote coefficients, as in ballot_box.questions.encodings.
//
// The ballot box decodes what this produces after the count, and leaves out
// votes that do not decode, so the two must agree exactly. Both are checked
// against test-vectors/encodings.json (see encodings.test.ts).
import type { Answer, QuestionSpec } from './spec.ts';

export class InvalidAnswer extends Error {}

type Encoder = (spec: QuestionSpec, answer: Answer) => number[];

/** Coefficient i is 1 if answer i is chosen, else 0; the rest are 0. */
function nOfMV1(spec: QuestionSpec, answer: Answer): number[] {
  const chosen = new Set(answer);
  if (chosen.size !== answer.length) throw new InvalidAnswer('an answer is chosen twice');
  for (const i of chosen) {
    if (!Number.isInteger(i) || i < 0 || i >= spec.answers.length) throw new InvalidAnswer('no such answer');
  }
  if (chosen.size < spec.min_choices || chosen.size > spec.max_choices) {
    throw new InvalidAnswer(`choose between ${spec.min_choices} and ${spec.max_choices} answers`);
  }
  return spec.answers.map((_, i) => (chosen.has(i) ? 1 : 0));
}

const ENCODERS: Record<string, Encoder> = { 'n_of_m/v1': nOfMV1 };

/** The coefficients to pass to evmlr's cast. Throws InvalidAnswer for an answer the question does not allow. */
export function encode(spec: QuestionSpec, answer: Answer): number[] {
  const encoder = ENCODERS[spec.encoding];
  if (!encoder) throw new Error(`unknown encoding ${spec.encoding}`);
  return encoder(spec, answer);
}
