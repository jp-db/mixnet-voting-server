// Web Worker that encrypts and proves ballots, off the main thread: casting
// is too slow for the UI thread in WebAssembly. The module is loaded once
// per worker.
import { loadEvmlr } from '@evmlr/evmlr.mjs';

export interface CastRequest {
  id: number;
  pp: Uint8Array;
  voterId: number;
  vote: number | number[];
}

export type CastResponse =
  | { id: number; ok: true; ballot: Uint8Array; tracker: string }
  | { id: number; ok: false; error: string };

const evmlrReady = loadEvmlr();

async function sha256Hex(data: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

self.onmessage = async (event: MessageEvent<CastRequest>) => {
  const { id, pp, voterId, vote } = event.data;
  try {
    const evmlr = await evmlrReady;
    const ballot = evmlr.cast(pp, voterId, vote);
    // The tracker is the hash of the exact bytes sent: the voter can find it
    // on the bulletin board afterwards.
    const tracker = await sha256Hex(ballot);
    const response: CastResponse = { id, ok: true, ballot, tracker };
    self.postMessage(response, { transfer: [ballot.buffer] });
  } catch (err) {
    const response: CastResponse = { id, ok: false, error: String(err) };
    self.postMessage(response);
  }
};
