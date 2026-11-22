// Main-thread client for the casting worker.
import type { CastRequest, CastResponse } from './cast.worker';

export interface CastResult {
  ballot: Uint8Array;
  /** SHA-256 of the ballot, in hex: shown to the voter and listed on the board. */
  tracker: string;
}

export class Booth {
  private worker = new Worker(new URL('./cast.worker.ts', import.meta.url), { type: 'module' });
  private nextId = 0;
  private pending = new Map<number, { resolve: (r: CastResult) => void; reject: (e: Error) => void }>();

  constructor() {
    this.worker.onmessage = (event: MessageEvent<CastResponse>) => {
      const msg = event.data;
      const waiter = this.pending.get(msg.id);
      if (!waiter) return;
      this.pending.delete(msg.id);
      if (msg.ok) waiter.resolve({ ballot: msg.ballot, tracker: msg.tracker });
      else waiter.reject(new Error(msg.error));
    };
  }

  /** Encrypts and proves a vote (the coefficients from questions/encodings.ts) for the voter id from /me. */
  cast(pp: Uint8Array, voterId: string, vote: number[]): Promise<CastResult> {
    const id = this.nextId++;
    const request: CastRequest = { id, pp, voterId, vote };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage(request);
    });
  }

  terminate(): void {
    this.worker.terminate();
    for (const { reject } of this.pending.values()) reject(new Error('booth closed'));
    this.pending.clear();
  }
}
