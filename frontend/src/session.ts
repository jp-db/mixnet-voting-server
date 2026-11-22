// Who is logged in, shared by every view.
import { reactive } from 'vue';

import { api, ApiError, type Me } from './api.ts';

export const session = reactive<{ me: Me | null; loaded: boolean }>({ me: null, loaded: false });

export async function refreshSession(): Promise<Me | null> {
  try {
    session.me = await api.me();
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) throw e;
    session.me = null;
  }
  session.loaded = true;
  return session.me;
}

export async function logout(): Promise<void> {
  await api.logout();
  session.me = null;
}
