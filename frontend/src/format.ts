// Small display helpers.
import type { ElectionState } from './api.ts';

export function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—';
}

export const STATE_LABELS: Record<ElectionState, string> = {
  draft: 'Draft',
  keygen: 'Generating keys',
  frozen: 'Ready, not open yet',
  open: 'Open for voting',
  closed: 'Closed',
  mixing: 'Counting',
  tallied: 'Counted, not published yet',
  published: 'Results published',
};

export function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Runs ``fn`` on ``items`` with at most ``limit`` running at once, keeping the order. */
export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  async function lane() {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return out;
}
