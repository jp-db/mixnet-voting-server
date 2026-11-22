// The public check of a published count, in the browser: fetch the
// parameters, every ballot on the board and the transcript, run evmlr's
// verification, then decode and recount the votes and compare with what the
// ballot box published.
import { api, type BoardEntry, type QuestionResult } from './api.ts';
import { info, verify } from './crypto/client.ts';
import { sha256Hex } from './crypto/hash.ts';
import { mapLimit } from './format.ts';
import type { QuestionSpec } from './questions/spec.ts';
import { tally } from './questions/tally.ts';

export interface Verification {
  ok: boolean;
  /** What failed, if anything. */
  problems: string[];
  votes: number;
}

async function wholeBoard(electionId: string, index: number): Promise<BoardEntry[]> {
  const out: BoardEntry[] = [];
  let after = 0;
  for (;;) {
    const page = await api.board(electionId, index, after, 1000);
    out.push(...page.entries);
    if (page.next_after === null) return out;
    after = page.next_after;
  }
}

export async function verifyQuestion(
  electionId: string,
  index: number,
  spec: QuestionSpec,
  paramsKey: string,
  published: QuestionResult,
  onProgress: (text: string) => void,
): Promise<Verification> {
  const problems: string[] = [];
  onProgress('Downloading the parameters and the board…');
  const pp = await api.params(electionId, index);
  if ((await sha256Hex(pp)) !== paramsKey) problems.push('the parameters do not match their published hash');

  const board = await wholeBoard(electionId, index);
  let fetched = 0;
  const ballots = await mapLimit(board, 6, async (entry) => {
    const ballot = await api.ballot(electionId, index, entry.tracker);
    if ((await sha256Hex(ballot)) !== entry.tracker) problems.push(`ballot #${entry.seq} does not match its tracker`);
    onProgress(`Downloading ballots: ${++fetched} of ${board.length}`);
    return ballot;
  });

  const transcript = await api.transcript(electionId, index);
  if (published.transcript_key && (await sha256Hex(transcript)) !== published.transcript_key) {
    problems.push('the transcript does not match its published hash');
  }

  onProgress('Checking every proof of shuffle against the board…');
  let votes: Uint16Array[];
  try {
    votes = await verify(pp, ballots, transcript);
  } catch (e) {
    return { ok: false, problems: [...problems, `the count does not verify: ${String(e)}`], votes: 0 };
  }

  onProgress('Recounting…');
  const { voteLen } = await info();
  const mine = tally(spec, votes, voteLen);
  if (JSON.stringify(mine.result) !== JSON.stringify(published.result)) {
    problems.push('recounting the verified votes gives another result than the published one');
  }
  if (JSON.stringify(mine.discarded) !== JSON.stringify(published.discarded)) {
    problems.push('the votes left out as malformed differ from the published list');
  }
  return { ok: problems.length === 0, problems, votes: votes.length };
}
