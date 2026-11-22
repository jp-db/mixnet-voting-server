// Answers to evmlr vote coefficients and back, as in ballot_box.questions.encodings.
//
// The booth encodes with this; the verifier decodes the counted votes to
// recompute the result. The ballot box does the same in Python, so the two
// must agree exactly. Both are checked against test-vectors/encodings.json
// (see encodings.test.ts).
import type { Answer, QuestionSpec } from './spec.ts';

export class InvalidAnswer extends Error {}

/** A decrypted vote that no voter using the booth could have produced. */
export class Malformed extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(reason);
    this.reason = reason;
  }
}

interface Encoding {
  encode(spec: QuestionSpec, answer: Answer): number[];
  decode(spec: QuestionSpec, vote: ArrayLike<number>, voteLen: number): Answer;
}

/** Coefficient i is 1 if answer i is chosen, else 0; the rest are 0. */
const nOfMV1: Encoding = {
  encode(spec, answer) {
    const chosen = new Set(answer);
    if (chosen.size !== answer.length) throw new InvalidAnswer('an answer is chosen twice');
    for (const i of chosen) {
      if (!Number.isInteger(i) || i < 0 || i >= spec.answers.length) throw new InvalidAnswer('no such answer');
    }
    if (chosen.size < spec.min_choices || chosen.size > spec.max_choices) {
      throw new InvalidAnswer(`choose between ${spec.min_choices} and ${spec.max_choices} answers`);
    }
    return spec.answers.map((_, i) => (chosen.has(i) ? 1 : 0));
  },

  decode(spec, vote, voteLen) {
    if (vote.length !== voteLen) throw new Malformed('wrong_length');
    const m = spec.answers.length;
    for (let i = 0; i < m; i++) if (vote[i] !== 0 && vote[i] !== 1) throw new Malformed('coefficient_not_0_or_1');
    for (let i = m; i < vote.length; i++) if (vote[i] !== 0) throw new Malformed('nonzero_padding');
    const answer: Answer = [];
    for (let i = 0; i < m; i++) if (vote[i]) answer.push(i);
    if (answer.length > spec.max_choices) throw new Malformed('too_many_choices');
    if (answer.length < spec.min_choices) throw new Malformed('too_few_choices');
    return answer;
  },
};

const ENCODINGS: Record<string, Encoding> = { 'n_of_m/v1': nOfMV1 };

function encodingOf(spec: QuestionSpec): Encoding {
  const encoding = ENCODINGS[spec.encoding];
  if (!encoding) throw new Error(`unknown encoding ${spec.encoding}`);
  return encoding;
}

/** The coefficients to pass to evmlr's cast. Throws InvalidAnswer for an answer the question does not allow. */
export function encode(spec: QuestionSpec, answer: Answer): number[] {
  return encodingOf(spec).encode(spec, answer);
}

/** Throws Malformed if the vote is not an encoded answer to the question. */
export function decode(spec: QuestionSpec, vote: ArrayLike<number>, voteLen: number): Answer {
  return encodingOf(spec).decode(spec, vote, voteLen);
}
