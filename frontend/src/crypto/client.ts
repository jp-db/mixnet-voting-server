// Main-thread client for the evmlr worker: one worker, requests matched by id.
import type { WorkerRequest, WorkerResponse } from './evmlr.worker';

type Pending = { resolve: (r: WorkerResponse & { ok: true }) => void; reject: (e: Error) => void };
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

let worker: Worker | null = null;
let nextId = 0;
const pending = new Map<number, Pending>();

function getWorker(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL('./evmlr.worker.ts', import.meta.url), { type: 'module' });
  worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
    const msg = event.data;
    const waiter = pending.get(msg.id);
    if (!waiter) return;
    pending.delete(msg.id);
    if (msg.ok) waiter.resolve(msg);
    else waiter.reject(new Error(msg.error));
  };
  worker.onerror = (event) => {
    for (const { reject } of pending.values()) reject(new Error(event.message || 'the evmlr worker failed'));
    pending.clear();
    worker = null;
  };
  return worker;
}

function call(request: DistributiveOmit<WorkerRequest, 'id'>): Promise<WorkerResponse & { ok: true }> {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    getWorker().postMessage({ ...request, id });
  });
}

/** Encrypts and proves a vote (coefficients from questions/encodings.ts) for the voter id from /me. */
export async function cast(pp: Uint8Array, voterId: string, vote: number[]) {
  const r = await call({ op: 'cast', pp, voterId, vote });
  if (r.op !== 'cast') throw new Error('unexpected worker reply');
  return { ballot: r.ballot, tracker: r.tracker };
}

/** The public verification of a count. Resolves to the votes; rejects if the count does not verify. */
export async function verify(pp: Uint8Array, ballots: Uint8Array[], transcript: Uint8Array) {
  const r = await call({ op: 'verify', pp, ballots, transcript });
  if (r.op !== 'verify') throw new Error('unexpected worker reply');
  return r.votes;
}

export async function info() {
  const r = await call({ op: 'info' });
  if (r.op !== 'info') throw new Error('unexpected worker reply');
  return { voteLen: r.voteLen, modulus: r.modulus };
}
