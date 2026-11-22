// Counting decoded votes, as in ballot_box.questions.methods and .tally.
import { decode, Malformed } from './encodings.ts';
import type { Answer, QuestionSpec } from './spec.ts';

export interface ChoiceCount {
  counts: number[];
  blank: number;
  valid: number;
  discarded: number;
}

export interface Discarded {
  /** Position of the vote in the verified output. */
  index: number;
  reason: string;
}

function choiceCountV1(spec: QuestionSpec, answers: Answer[]): Omit<ChoiceCount, 'discarded'> {
  const counts = spec.answers.map(() => 0);
  for (const answer of answers) for (const i of answer) counts[i]++;
  return { counts, blank: answers.filter((a) => a.length === 0).length, valid: answers.length };
}

const METHODS: Record<string, typeof choiceCountV1> = { 'choice_count/v1': choiceCountV1 };

/** The result of the valid votes, and the malformed ones left out (never an error). */
export function tally(
  spec: QuestionSpec,
  votes: ArrayLike<number>[],
  voteLen: number,
): { result: ChoiceCount; discarded: Discarded[] } {
  const method = METHODS[spec.method];
  if (!method) throw new Error(`unknown counting method ${spec.method}`);
  const answers: Answer[] = [];
  const discarded: Discarded[] = [];
  votes.forEach((vote, index) => {
    try {
      answers.push(decode(spec, vote, voteLen));
    } catch (e) {
      if (!(e instanceof Malformed)) throw e;
      discarded.push({ index, reason: e.reason });
    }
  });
  return { result: { ...method(spec, answers), discarded: discarded.length }, discarded };
}
