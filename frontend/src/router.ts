import { createRouter, createWebHistory } from 'vue-router';

import { refreshSession, session } from './session.ts';

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/HomeView.vue') },
    { path: '/login', component: () => import('./views/LoginView.vue') },
    { path: '/elections/:id', component: () => import('./views/ElectionView.vue'), props: true },
    { path: '/elections/:id/vote', component: () => import('./views/BoothView.vue'), props: true, meta: { login: true } },
    { path: '/elections/:id/board', component: () => import('./views/BoardView.vue'), props: true },
    { path: '/elections/:id/results', component: () => import('./views/ResultsView.vue'), props: true },
    { path: '/admin', component: () => import('./views/admin/AdminElections.vue'), meta: { manage: true } },
    { path: '/admin/elections/:id', component: () => import('./views/admin/AdminElection.vue'), props: true, meta: { manage: true } },
    {
      path: '/admin/elections/:id/booth',
      component: () => import('./views/BoothView.vue'),
      props: (route) => ({ id: route.params.id, simulate: true }),
      meta: { manage: true },
    },
    { path: '/admin/nodes', component: () => import('./views/admin/AdminNodes.vue'), meta: { manage: true } },
    { path: '/admin/accounts', component: () => import('./views/admin/AdminAccounts.vue'), meta: { manage: true } },
    { path: '/:pathMatch(.*)*', component: () => import('./views/NotFound.vue') },
  ],
});

router.beforeEach(async (to) => {
  if (!session.loaded) await refreshSession();
  if ((to.meta.login || to.meta.manage) && !session.me) return { path: '/login', query: { next: to.fullPath } };
  if (to.meta.manage && !session.me?.can_manage) return { path: '/' };
  return true;
});
