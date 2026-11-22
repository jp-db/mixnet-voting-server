// Web Worker that runs evmlr (WebAssembly) off the main thread: casting and
// verifying take seconds. The module is loaded once per worker.
import { loadEvmlr } from '@evmlr/evmlr.mjs';

import { sha256Hex } from './hash.ts';

export type WorkerRequest =
  | { id: number; op: 'cast'; pp: Uint8Array; voterId: string; vote: number[] }
  | { id: number; op: 'check'; pp: Uint8Array; ballot: Uint8Array }
  | { id: number; op: 'verify'; pp: Uint8Array; ballots: Uint8Array[]; transcript: Uint8Array }
  | { id: number; op: 'info' };

export type WorkerResponse =
  | { id: number; ok: true; op: 'cast'; ballot: Uint8Array<ArrayBuffer>; tracker: string }
  | { id: number; ok: true; op: 'check'; voterId: string }
  | { id: number; ok: true; op: 'verify'; votes: Uint16Array[] }
  | { id: number; ok: true; op: 'info'; voteLen: number; modulus: number }
  | { id: number; ok: false; error: string };

const evmlrReady = loadEvmlr();

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data;
  try {
    const evmlr = await evmlrReady;
    if (msg.op === 'cast') {
      const ballot = evmlr.cast(msg.pp, msg.voterId, msg.vote);
      // The tracker is the hash of the exact bytes sent: the board lists it.
      const tracker = await sha256Hex(ballot);
      const response: WorkerResponse = { id: msg.id, ok: true, op: 'cast', ballot, tracker };
      self.postMessage(response, { transfer: [ballot.buffer] });
    } else if (msg.op === 'check') {
      const voterId = String(evmlr.checkBallot(msg.pp, msg.ballot));
      const response: WorkerResponse = { id: msg.id, ok: true, op: 'check', voterId };
      self.postMessage(response);
    } else if (msg.op === 'verify') {
      const votes = evmlr.verify(msg.pp, msg.ballots, msg.transcript);
      const response: WorkerResponse = { id: msg.id, ok: true, op: 'verify', votes };
      self.postMessage(response);
    } else {
      const response: WorkerResponse = { id: msg.id, ok: true, op: 'info', voteLen: evmlr.voteLen, modulus: evmlr.modulus };
      self.postMessage(response);
    }
  } catch (err) {
    const response: WorkerResponse = { id: msg.id, ok: false, error: String(err) };
    self.postMessage(response);
  }
};
