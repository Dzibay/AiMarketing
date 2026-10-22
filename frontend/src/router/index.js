import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import LegalView from '../views/LegalView.vue'
import AdminView from '../views/AdminView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import CabinetView from '../views/CabinetView.vue'
import OAuthDoneView from '../views/OAuthDoneView.vue'

const DOCS_PAGES = new Set(['privacy', 'consent', 'offer', 'cookies'])

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/register', name: 'register', component: RegisterView },
    { path: '/cabinet', name: 'cabinet', component: CabinetView },
    { path: '/oauth/done', name: 'oauth-done', component: OAuthDoneView },
    { path: '/privacy', name: 'privacy', component: LegalView, props: { page: 'privacy' } },
    { path: '/consent', name: 'consent', component: LegalView, props: { page: 'consent' } },
    { path: '/offer', name: 'offer', component: LegalView, props: { page: 'offer' } },
    { path: '/cookies', name: 'cookies', component: LegalView, props: { page: 'cookies' } },
    { path: '/admin', redirect: '/admin-panel' },
    { path: '/admin-panel', name: 'admin', component: AdminView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior(to, from, savedPosition) {
    if (DOCS_PAGES.has(to.name)) {
      if (DOCS_PAGES.has(from?.name)) return false
      if (savedPosition) return savedPosition
      return { top: 0, left: 0 }
    }
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
})

export default router
