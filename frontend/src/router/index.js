import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import LegalView from '../views/LegalView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import ForgotPasswordView from '../views/ForgotPasswordView.vue'
import ResetPasswordView from '../views/ResetPasswordView.vue'
import OAuthDoneView from '../views/OAuthDoneView.vue'
import { ACCESS_EVENT } from '../api/web'
import { accessRedirect, loadProfile, useProfile } from '../composables/useProfile'
import { captureRef } from '../composables/useReferral'
import { getSession } from '../composables/useSession'

const DOCS_PAGES = new Set(['privacy', 'consent', 'offer', 'cookies'])

/** Раздел по всем проектам. */
const all = (section) => ({ section, scope: 'all' })
/** Раздел по одному проекту (/w/:id/…). */
const proj = (section) => ({ section, scope: 'project' })
/** Страница вне проектов: фильтр проекта на ней не сбрасывается. */
const neutral = (section, title) => ({ section, scope: 'neutral', title })

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/register', name: 'register', component: RegisterView },
    { path: '/forgot-password', name: 'forgot-password', component: ForgotPasswordView },
    { path: '/reset-password', name: 'reset-password', component: ResetPasswordView },
    { path: '/verify-email', name: 'verify-email', component: () => import('../views/VerifyEmailView.vue') },

    // Кабинет: одна оболочка (меню слева) на все страницы, меню при переходах не перерисовывается.
    // meta.section — пункт меню, meta.scope — по всем проектам / по проекту / вне проектов.
    {
      path: '/',
      component: () => import('../components/AppShell.vue'),
      children: [
        { path: 'cabinet', name: 'cabinet', component: () => import('../views/OverviewView.vue'), meta: all('summary') },
        {
          path: 'cabinet/chat/:chatId(\\d+)?',
          name: 'chat',
          component: () => import('../views/AiChatView.vue'),
          meta: all('chat'),
        },
        {
          path: 'cabinet/campaigns',
          name: 'campaigns',
          component: () => import('../views/CampaignsView.vue'),
          meta: all('campaigns'),
        },
        {
          path: 'cabinet/queries',
          name: 'queries',
          component: () => import('../views/QueriesView.vue'),
          meta: all('queries'),
        },
        {
          path: 'cabinet/negative-lists',
          name: 'negative-lists',
          component: () => import('../views/NegativeListsView.vue'),
          meta: neutral('queries'),
        },
        {
          path: 'cabinet/analytics',
          name: 'analytics',
          component: () => import('../views/AnalyticsView.vue'),
          meta: all('analytics'),
        },
        {
          path: 'cabinet/autopilot',
          name: 'autopilot',
          component: () => import('../views/RulesView.vue'),
          meta: all('autopilot'),
        },
        {
          path: 'cabinet/history',
          name: 'history',
          component: () => import('../views/HistoryView.vue'),
          meta: all('history'),
        },
        {
          path: 'cabinet/pending',
          name: 'pending',
          component: () => import('../views/PendingView.vue'),
          meta: neutral('pending', 'Ждёт решения'),
        },
        {
          path: 'cabinet/connections',
          name: 'connections',
          component: () => import('../views/AccountsView.vue'),
          meta: neutral('connections', 'Подключения'),
        },
        {
          path: 'cabinet/settings',
          name: 'settings',
          component: () => import('../views/SettingsView.vue'),
          props: { tab: 'profile' },
          meta: neutral('settings', 'Настройки'),
        },
        {
          path: 'cabinet/settings/notifications',
          name: 'settings-notifications',
          component: () => import('../views/SettingsView.vue'),
          props: { tab: 'notifications' },
          meta: neutral('settings', 'Настройки'),
        },
        {
          path: 'cabinet/plan',
          name: 'plan',
          component: () => import('../views/PlanView.vue'),
          meta: neutral('plan', 'Тариф'),
        },
        {
          path: 'cabinet/welcome',
          name: 'welcome',
          component: () => import('../views/WelcomeView.vue'),
          meta: neutral('welcome', 'Начало работы'),
        },

        // Проект (счётчик Метрики): те же разделы, но по одному сайту.
        {
          path: 'w/:wsId(\\d+)',
          component: () => import('../views/workspace/WorkspaceView.vue'),
          children: [
            { path: '', redirect: (to) => `/w/${to.params.wsId}/summary` },
            {
              path: 'summary',
              name: 'ws-summary',
              component: () => import('../views/workspace/WsSummary.vue'),
              meta: proj('summary'),
            },
            {
              path: 'chat/:chatId(\\d+)?',
              name: 'ws-chat',
              component: () => import('../views/workspace/WsChat.vue'),
              meta: proj('chat'),
            },
            {
              path: 'campaigns',
              name: 'ws-campaigns',
              component: () => import('../views/workspace/WsCampaigns.vue'),
              meta: proj('campaigns'),
            },
            {
              path: 'campaigns/:cid(\\d+)',
              name: 'ws-campaign',
              component: () => import('../views/workspace/WsCampaign.vue'),
              meta: proj('campaigns'),
            },
            {
              path: 'queries',
              name: 'ws-queries',
              component: () => import('../views/workspace/WsQueries.vue'),
              meta: proj('queries'),
            },
            {
              path: 'analytics',
              name: 'ws-analytics',
              component: () => import('../views/workspace/WsAnalytics.vue'),
              meta: proj('analytics'),
            },
            {
              path: 'slices',
              name: 'ws-slices',
              component: () => import('../views/workspace/WsSlices.vue'),
              meta: proj('analytics'),
            },
            {
              path: 'ad-stats',
              name: 'ws-ad-stats',
              component: () => import('../views/workspace/WsAdStats.vue'),
              meta: proj('analytics'),
            },
            {
              path: 'autopilot',
              name: 'ws-autopilot',
              component: () => import('../views/workspace/WsAutopilot.vue'),
              meta: proj('autopilot'),
            },
            {
              path: 'experiments',
              name: 'ws-experiments',
              component: () => import('../views/workspace/WsExperiments.vue'),
              meta: proj('autopilot'),
            },
            {
              path: 'history',
              name: 'ws-history',
              component: () => import('../views/workspace/WsHistory.vue'),
              meta: proj('history'),
            },
            {
              path: 'settings',
              name: 'ws-settings',
              component: () => import('../views/workspace/WsSettings.vue'),
              meta: proj('project-settings'),
            },
            // Старый адрес: «Действия» разделились на «Автопилот» и «Историю».
            { path: 'actions', redirect: (to) => `/w/${to.params.wsId}/autopilot` },
          ],
        },
      ],
    },

    // Старые адреса кабинета.
    { path: '/cabinet/overview', redirect: '/cabinet' },
    { path: '/cabinet/accounts', redirect: '/cabinet/connections' },
    { path: '/cabinet/clients', redirect: '/cabinet' },
    { path: '/cabinet/rules', redirect: '/cabinet/autopilot' },
    { path: '/cabinet/campaigns/:rest(.*)+', redirect: '/cabinet/campaigns' },
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
    // Переключение чатов внутри раздела не прокручивает страницу наверх.
    if (to.name === 'ws-chat' && from.name === 'ws-chat' && to.params.wsId === from.params.wsId) return false
    if (to.name === 'chat' && from.name === 'chat') return false
    // Якорь на секцию (#ai): страница дорисовывается после загрузки данных — ждём, пока секция появится.
    if (to.hash) {
      return new Promise((resolve) => {
        const started = Date.now()
        const tick = () => {
          if (document.querySelector(to.hash)) resolve({ el: to.hash, top: 80 })
          else if (Date.now() - started > 3000) resolve({ top: 0 })
          else setTimeout(tick, 150)
        }
        setTimeout(tick, 400)
      })
    }
    return { top: 0 }
  },
})

// Кабинет закрыт, пока почта не подтверждена или подписка закончилась: ведём на нужный экран
// до того, как страница начнёт грузить данные (иначе — пачка ошибок 402/403).
const GATED = /^\/(cabinet|w)(\/|$)/

router.beforeEach(async (to) => {
  if (to.query.ref !== undefined) {
    // Реф-код запоминаем и убираем из адреса — чтобы не попадал в закладки и не пересылался дальше.
    captureRef(to.query.ref)
    const { ref: _ref, ...query } = to.query
    return { path: to.path, query, hash: to.hash, replace: true }
  }
  if (!GATED.test(to.path) || !getSession()) return true
  try {
    await loadProfile()
  } catch {
    return true // 401 и сбои разберёт оболочка кабинета
  }
  return accessRedirect(useProfile().subscription.value, to.path) || true
})

// Подписка закончилась или подтверждение включили, пока кабинет открыт: перечитываем профиль и уводим.
if (typeof window !== 'undefined') {
  window.addEventListener(ACCESS_EVENT, async (event) => {
    const code = event.detail?.code
    if (code !== 'subscription-expired' && code !== 'email-unverified') return
    try {
      await loadProfile(true)
    } catch {
      return
    }
    const target = accessRedirect(useProfile().subscription.value, router.currentRoute.value.path)
    if (target) router.replace(target)
  })
}

export default router
