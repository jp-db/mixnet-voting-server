// Typed client for the ballot box's HTTP API (same origin: Vite proxies it in development).
import type { QuestionSpec } from './questions/spec.ts';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request(method: string, path: string, body?: BodyInit | object): Promise<Response> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const json = await response.json();
      detail = typeof json.detail === 'string' ? json.detail : JSON.stringify(json.detail);
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return response;
}

async function json<T>(method: string, path: string, body?: BodyInit | object): Promise<T> {
  const response = await request(method, path, body);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

async function bytes(path: string): Promise<Uint8Array<ArrayBuffer>> {
  return new Uint8Array(await (await request('GET', path)).arrayBuffer());
}

// --- types (mirroring the pydantic models) ------------------------------------------------

export type ElectionState =
  | 'draft' | 'keygen' | 'frozen' | 'open' | 'closed' | 'mixing' | 'tallied' | 'published';

export interface Me {
  provider: string;
  external_id: string;
  email: string | null;
  display_name: string | null;
  is_admin: boolean;
  can_manage: boolean;
}

export interface Provider {
  name: 'password' | 'cas';
  label: string;
  login_url: string;
}

export interface ElectionListItem {
  id: string;
  title: string;
  state: ElectionState;
  opened_at: string | null;
  closed_at: string | null;
}

export interface PublicQuestion {
  index: number;
  spec: QuestionSpec;
  crypto_id: string;
  params_key: string;
  keys: { position: number; fingerprint: string }[];
  ballots: number;
}

export interface PublicElection {
  id: string;
  title: string;
  description: string;
  state: ElectionState;
  opened_at: string | null;
  closed_at: string | null;
  published_at: string | null;
  mix_servers: { position: number; node: string }[];
  questions: PublicQuestion[];
  voters: number;
}

export interface Cast {
  question_index: number;
  seq: number;
  tracker: string;
  cast_at: string;
}

export interface VoterStatus {
  eligible: boolean;
  /** Decimal string (the API's convention for voter ids). */
  voter_id: string | null;
  cast: Cast[];
}

export interface BoardEntry {
  seq: number;
  voter_id: string;
  tracker: string;
  cast_at: string;
}

export interface TrackerInfo {
  question_index: number;
  seq: number;
  cast_at: string;
  superseded: boolean;
}

export interface QuestionResult {
  index: number;
  state: string;
  result: { counts: number[]; blank: number; valid: number; discarded: number } | null;
  discarded: { index: number; reason: string }[] | null;
  transcript_key: string | null;
  mix_steps: { position: number; record_key: string | null; info: MixInfo | null }[];
}

export interface MixInfo {
  n_in: number;
  n_out: number;
  rejections: { index: number; reason: string }[];
}

// Admin

export interface MixnetNode {
  id: string;
  name: string;
  url: string;
  is_default: boolean;
  default_position: number | null;
}

export interface Tally {
  state: 'pending' | 'done' | 'failed';
  error: string | null;
  result: QuestionResult['result'];
  discarded: QuestionResult['discarded'];
  first_batch_key: string | null;
  transcript_key: string | null;
  steps: { position: number; job_id: string | null; record_key: string | null; output_batch_key: string | null; info: MixInfo | null }[];
}

export interface AdminQuestion {
  id: string;
  index: number;
  spec: QuestionSpec;
  crypto_id: string | null;
  params_key: string | null;
  keys: { position: number; fingerprint: string }[];
  tally: Tally | null;
}

export interface AdminElection {
  id: string;
  title: string;
  description: string;
  state: ElectionState;
  error: string | null;
  created_at: string;
  frozen_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  tallied_at: string | null;
  published_at: string | null;
  mix_servers: { position: number; node_id: string; node: string }[];
  questions: AdminQuestion[];
  voter_count: number;
}

export interface Voter {
  id: string;
  voter_id: string;
  provider: string;
  external_id: string;
  email: string | null;
  display_name: string | null;
}

export interface Account {
  id: string;
  identity: string;
  email: string | null;
  display_name: string | null;
  is_admin: boolean;
  can_manage: boolean;
  last_login_at: string | null;
}

export type QuestionInput = Omit<QuestionSpec, 'kind' | 'encoding' | 'method'> &
  Partial<Pick<QuestionSpec, 'kind' | 'encoding' | 'method'>>;

export type LifecycleAction = 'freeze' | 'unfreeze' | 'open' | 'close' | 'count' | 'retry-count' | 'publish';

// --- calls ----------------------------------------------------------------------------------

const e = (id: string) => `/api/elections/${encodeURIComponent(id)}`;
const q = (id: string, index: number) => `${e(id)}/questions/${index}`;
const a = (path: string) => `/api/admin${path}`;

export const api = {
  // auth
  me: () => json<Me>('GET', '/auth/me'),
  providers: () => json<Provider[]>('GET', '/auth/providers'),
  passwordLogin: (username: string, password: string) =>
    json<void>('POST', '/auth/password/login', { username, password }),
  logout: () => json<void>('POST', '/auth/logout'),
  casLoginUrl: (next: string) => `/auth/cas/login?next=${encodeURIComponent(next)}`,

  // public
  elections: () => json<ElectionListItem[]>('GET', '/api/elections'),
  election: (id: string) => json<PublicElection>('GET', e(id)),
  params: (id: string, index: number) => bytes(`${q(id, index)}/params`),
  board: (id: string, index: number, after = 0, limit = 500) =>
    json<{ entries: BoardEntry[]; next_after: number | null }>(
      'GET',
      `${q(id, index)}/board?after=${after}&limit=${limit}`,
    ),
  ballot: (id: string, index: number, tracker: string) => bytes(`${q(id, index)}/ballots/${tracker}`),
  tracker: (id: string, tracker: string) => json<TrackerInfo>('GET', `${e(id)}/trackers/${tracker}`),
  results: (id: string) => json<{ election_id: string; questions: QuestionResult[] }>('GET', `${e(id)}/results`),
  transcript: (id: string, index: number) => bytes(`${q(id, index)}/transcript`),

  // voter
  voterStatus: (id: string) => json<VoterStatus>('GET', `${e(id)}/me`),
  cast: (id: string, ballots: Map<number, Uint8Array<ArrayBuffer>>) => {
    const form = new FormData();
    for (const [index, ballot] of ballots) form.append(`q${index}`, new Blob([ballot]), `q${index}`);
    return json<Cast[]>('POST', `${e(id)}/ballots`, form);
  },

  // admin
  admin: {
    nodes: () => json<MixnetNode[]>('GET', a('/mixnet-nodes')),
    addNode: (name: string, url: string, token: string) =>
      json<MixnetNode>('POST', a('/mixnet-nodes'), { name, url, token }),
    elections: () => json<{ id: string; title: string; state: ElectionState; created_at: string }[]>('GET', a('/elections')),
    election: (id: string) => json<AdminElection>('GET', a(`/elections/${id}`)),
    create: (body: { title: string; description: string; questions: QuestionInput[]; mix_servers?: string[] | null }) =>
      json<AdminElection>('POST', a('/elections'), body),
    update: (
      id: string,
      body: Partial<{ title: string; description: string; questions: QuestionInput[]; mix_servers: string[]; use_default_mix_servers: boolean }>,
    ) => json<AdminElection>('PATCH', a(`/elections/${id}`), body),
    voters: (id: string) => json<Voter[]>('GET', a(`/elections/${id}/voters`)),
    addVoters: (id: string, voters: { provider: string; external_id: string; email?: string | null; display_name?: string | null }[]) =>
      json<Voter[]>('POST', a(`/elections/${id}/voters`), { voters }),
    act: (id: string, action: LifecycleAction) => json<AdminElection>('POST', a(`/elections/${id}/${action}`)),
    managers: (id: string) => json<string[]>('GET', a(`/elections/${id}/managers`)),
    setManagers: (id: string, managers: string[]) => json<string[]>('PUT', a(`/elections/${id}/managers`), { managers }),
    accounts: () => json<Account[]>('GET', a('/accounts')),
    updateAccount: (id: string, body: { is_admin?: boolean; can_manage?: boolean }) =>
      json<Account>('PATCH', a(`/accounts/${id}`), body),
    setPasswordLogin: (body: { username: string; password: string; email?: string | null; display_name?: string | null }) =>
      json<void>('PUT', a('/password-logins'), body),
  },
};
