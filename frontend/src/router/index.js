import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import LegalView from '../views/LegalView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import ForgotPasswordView from '../views/ForgotPasswordView.vue'
import ResetPasswordView from '../views/ResetPasswordView.vue'
import OAuthDoneView from '../views/OAuthDoneView.vue'

const DOCS_PAGES = new Set(['privacy', 'consent', 'offer', 'cookies'])

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/register', name: 'register', component: RegisterView },
    { path: '/forgot-password', name: 'forgot-password', component: ForgotPasswordView },
    { path: '/reset-password', name: 'reset-password', component: ResetPasswordView },

    // Общий кабинет: все пространства, подключения, папки клиентов, инструменты и настройки.
    { path: '/cabinet', name: 'cabinet', component: () => import('../views/OverviewView.vue') },
    { path: '/cabinet/accounts', name: 'accounts', component: () => import('../views/AccountsView.vue') },
    { path: '/cabinet/clients', name: 'clients', component: () => import('../views/ClientsView.vue') },
    { path: '/cabinet/settings', name: 'settings', component: () => import('../views/SettingsView.vue') },
    { path: '/cabinet/rules', name: 'rules', component: () => import('../views/RulesView.vue') },
    {
      path: '/cabinet/negative-lists',
      name: 'negative-lists',
      component: () => import('../views/NegativeListsView.vue'),
    },
    // Старые адреса — в обзор.
    { path: '/cabinet/overview', redirect: '/cabinet' },
    { path: '/cabinet/analytics', redirect: '/cabinet' },
    { path: '/cabinet/campaigns/:rest(.*)*', redirect: '/cabinet' },

    // Рабочее пространство счётчика.
    {
      path: '/w/:wsId(\\d+)',
      component: () => import('../views/workspace/WorkspaceView.vue'),
      children: [
        { path: '', redirect: (to) => `/w/${to.params.wsId}/summary` },
        { path: 'summary', name: 'ws-summary', component: () => import('../views/workspace/WsSummary.vue') },
        {
          path: 'analytics',
          name: 'ws-analytics',
          component: () => import('../views/workspace/WsAnalytics.vue'),
        },
        {
          path: 'campaigns',
          name: 'ws-campaigns',
          component: () => import('../views/workspace/WsCampaigns.vue'),
        },
        {
          path: 'campaigns/:cid(\\d+)',
          name: 'ws-campaign',
          component: () => import('../views/workspace/WsCampaign.vue'),
        },
        { path: 'actions', name: 'ws-actions', component: () => import('../views/workspace/WsActions.vue') },
        { path: 'chat/:chatId(\\d+)?', name: 'ws-chat', component: () => import('../views/workspace/WsChat.vue') },
        {
          path: 'settings',
          name: 'ws-settings',
          component: () => import('../views/workspace/WsSettings.vue'),
        },
      ],
    },
    { path: '/w', redirect: '/cabinet' },

    { path: '/oauth/done', name: 'oauth-done', component: OAuthDoneView },
    { path: '/privacy', name: 'privacy', component: LegalView, props: { page: 'privacy' } },
    { path: '/consent', name: 'consent', component: LegalView, props: { page: 'consent' } },
    { path: '/offer', name: 'offer', component: LegalView, props: { page: 'offer' } },
    { path: '/cookies', name: 'cookies', component: LegalView, props: { page: 'cookies' } },
    { path: '/admin', redirect: '/admin-panel' },
    { path: '/admin-panel', name: 'admin', component: () => import('../views/AdminView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior(to, from, savedPosition) {
    if (DOCS_PAGES.has(to.name)) {
      if (DOCS_PAGES.has(from?.name)) return false
      if (savedPosition) return savedPosition
      return { top: 0, left: 0 }
    }
    if (savedPosition) return savedPosition
    // Переключение чатов внутри вкладки не прокручивает страницу наверх.
    if (to.name === 'ws-chat' && from.name === 'ws-chat' && to.params.wsId === from.params.wsId) return false
    // Якорь на секцию (#ai): страница дорисовывается после загрузки данных — ждём немного.
    if (to.hash) {
      return new Promise((resolve) => {
        setTimeout(() => resolve(document.querySelector(to.hash) ? { el: to.hash, top: 80 } : { top: 0 }), 400)
      })
    }
    return { top: 0 }
  },
})

export default router
