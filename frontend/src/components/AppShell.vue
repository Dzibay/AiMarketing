<template>
  <div class="app" :class="{ 'is-collapsed': collapsed }">
    <aside class="side" aria-label="Меню">
      <div class="side__top">
        <RouterLink class="side__brand" to="/cabinet" aria-label="AiMarketing — сводка">
          <span class="side__logo"><BrandMark :size="30" /></span>
          <strong class="side__name">Ai<span>Marketing</span></strong>
        </RouterLink>
        <button
          class="side__collapse"
          type="button"
          :aria-label="collapsed ? 'Развернуть меню' : 'Свернуть меню'"
          :title="collapsed ? 'Развернуть меню' : 'Свернуть меню'"
          @click="toggleCollapsed"
        >
          <PanelLeftOpen v-if="collapsed" aria-hidden="true" />
          <PanelLeftClose v-else aria-hidden="true" />
        </button>
      </div>

      <ProjectPicker v-if="accessOk" class="side__picker" :compact="collapsed" />

      <nav class="side__nav" aria-label="Разделы">
        <template v-for="item in sections" :key="item.key">
          <RouterLink
            v-if="item.to"
            class="side__item"
            :class="{ active: item.active }"
            :to="item.to"
            :title="collapsed ? item.label : undefined"
          >
            <component :is="item.icon" aria-hidden="true" />
            <span class="side__label">{{ item.label }}</span>
            <span v-if="item.badge" class="side__badge">{{ item.badge }}</span>
          </RouterLink>
          <span v-else class="side__item is-disabled" :title="item.hint" aria-disabled="true">
            <component :is="item.icon" aria-hidden="true" />
            <span class="side__label">
              {{ item.label }}
              <small>{{ item.hint }}</small>
            </span>
          </span>
        </template>
      </nav>

      <nav class="side__nav side__nav--service" aria-label="Служебные разделы">
        <RouterLink
          v-for="item in SERVICE"
          :key="item.key"
          class="side__item"
          :class="{ active: route.meta.section === item.key }"
          :to="item.to"
          :title="collapsed ? item.label : undefined"
        >
          <component :is="item.icon" aria-hidden="true" />
          <span class="side__label">{{ item.label }}</span>
        </RouterLink>
        <button
          class="side__item"
          type="button"
          :title="collapsed ? themeLabel : undefined"
          @click="toggleTheme"
        >
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
          <span class="side__label">{{ themeLabel }}</span>
        </button>
      </nav>
    </aside>

    <div class="app__main">
      <!-- На телефоне меню нет: логотип, переключатель проекта и профиль — в верхней строке. -->
      <div class="mobile-top">
        <RouterLink class="side__logo" to="/cabinet" aria-label="AiMarketing — сводка"><BrandMark :size="28" /></RouterLink>
        <ProjectPicker v-if="accessOk" class="mobile-top__picker" />
        <ProfileMenu @logout="logout" />
      </div>

      <header class="page-head">
        <div class="page-head__title">
          <h1>{{ title }}</h1>
          <p v-if="scopeLabel">{{ scopeLabel }}</p>
        </div>
        <div class="page-head__tools">
          <RouterLink
            v-if="limitChip"
            class="limit-chip"
            :class="`is-${limitLevel || 'ok'}`"
            to="/cabinet/plan"
            title="Расход лимита и тариф"
          >
            <span v-if="limitChip.percent !== null" class="limit-chip__bar" aria-hidden="true">
              <span :style="{ width: `${limitChip.percent}%` }"></span>
            </span>
            <span class="limit-chip__text">{{ limitChip.text }}</span>
          </RouterLink>
          <RouterLink
            v-if="accessOk"
            class="pending-chip"
            :class="{ 'has-items': pendingCount > 0, active: route.meta.section === 'pending' }"
            to="/cabinet/pending"
            title="Предложения ИИ, которые ждут вашего решения"
          >
            <Inbox aria-hidden="true" />
            <span class="pending-chip__label">Ждёт решения:</span>
            <strong>{{ pendingCount }}</strong>
          </RouterLink>
          <LoadsIndicator v-if="accessOk" />
          <ProfileMenu class="page-head__profile" @logout="logout" />
        </div>
      </header>

      <nav v-if="tabs.length > 1" class="page-tabs" :aria-label="title">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.to"
          class="page-tabs__tab"
          :class="{ active: pageMatches(tab, route.path) }"
          :to="tab.to"
        >
          {{ tab.label }}
          <Lock v-if="featureLocked(subscription, tab.feature)" class="page-tabs__lock" aria-label="не входит в тариф" />
        </RouterLink>
      </nav>

      <main class="cabinet app__content">
        <div v-if="limitBanner" class="notice limit-banner" :class="limitBanner.cls" role="status">
          <span>{{ limitBanner.text }}</span>
          <RouterLink class="btn btn--ghost btn--xs" to="/cabinet/plan">{{ limitBanner.action }}</RouterLink>
          <button class="icon-btn" type="button" aria-label="Скрыть" @click="dismissBanner">
            <X aria-hidden="true" />
          </button>
        </div>
        <RouterView />
      </main>
    </div>

    <Modal :open="!!upgrade" :title="upgrade?.title || ''" wide @close="upgrade = null">
      <template v-if="upgrade">
        <p>{{ upgrade.text }}</p>
        <PlanOffers
          v-if="upgradeOffers.length"
          :offers="upgradeOffers"
          :features="plan?.features || {}"
          upgrades-only
          compact
          @choose="chooseOffer"
        />
        <p v-else-if="plan" class="hint">Тарифов для перехода пока нет — напишите в поддержку, подберём условия.</p>
        <p v-else class="empty">Загружаем тарифы…</p>
      </template>
      <template #footer>
        <button class="btn btn--ghost" type="button" @click="upgrade = null">Закрыть</button>
        <RouterLink class="btn btn--accent" to="/cabinet/plan" @click="upgrade = null">Все тарифы</RouterLink>
      </template>
    </Modal>

    <nav class="tabbar" aria-label="Разделы">
      <RouterLink
        v-for="item in tabbarItems"
        :key="item.key"
        class="tabbar__item"
        :class="{ active: item.active }"
        :to="item.to"
      >
        <component :is="item.icon" aria-hidden="true" />
        <span>{{ item.short || item.label }}</span>
      </RouterLink>
      <button
        class="tabbar__item"
        :class="{ active: moreOpen || moreActive }"
        type="button"
        :aria-expanded="moreOpen"
        @click="moreOpen = !moreOpen"
      >
        <Ellipsis aria-hidden="true" />
        <span>Ещё</span>
      </button>
    </nav>

    <template v-if="moreOpen">
      <div class="tabbar-backdrop" @click="moreOpen = false"></div>
      <div class="tabbar-sheet" role="menu">
        <template v-for="item in moreItems" :key="item.key">
          <RouterLink v-if="item.to" class="menu-item" :class="{ active: item.active }" role="menuitem" :to="item.to">
            <component :is="item.icon" aria-hidden="true" />
            {{ item.label }}
          </RouterLink>
          <span v-else class="menu-item is-disabled" role="menuitem" aria-disabled="true">
            <component :is="item.icon" aria-hidden="true" />
            {{ item.label }}<small class="menu-item__note">{{ item.hint }}</small>
          </span>
        </template>
        <hr />
        <RouterLink
          v-for="item in SERVICE"
          :key="item.key"
          class="menu-item"
          :class="{ active: route.meta.section === item.key }"
          role="menuitem"
          :to="item.to"
        >
          <component :is="item.icon" aria-hidden="true" />
          {{ item.label }}
        </RouterLink>
        <button class="menu-item" type="button" role="menuitem" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
          {{ themeLabel }}
        </button>
        <hr />
        <button class="menu-item is-danger" type="button" role="menuitem" @click="logout">
          <LogOut aria-hidden="true" />
          Выйти
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Ellipsis, Inbox, Lock, LogOut, Moon, PanelLeftClose, PanelLeftOpen, Sun, X } from '@lucide/vue'
import BrandMark from './BrandMark.vue'
import LoadsIndicator from './LoadsIndicator.vue'
import PlanOffers from './PlanOffers.vue'
import ProfileMenu from './ProfileMenu.vue'
import ProjectPicker from './ProjectPicker.vue'
import Modal from './ui/Modal.vue'
import { SECTIONS, SERVICE, TABBAR_KEYS, pageMatches, sectionTabs, sectionTo } from './shellNav'
import { resetLoads } from '../composables/useLoads'
import { loadPending, resetPending, usePending } from '../composables/usePending'
import { loadPlan, resetPlan, usePlan } from '../composables/usePlan'
import { featureLocked, loadProfile, resetProfile, useProfile } from '../composables/useProfile'
import { resetProjectFilter, useProjectFilter } from '../composables/useProjectFilter'
import { clearSession, getSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'
import { loadWorkspaces, resetWorkspaces } from '../composables/useWorkspaces'
import { ACCESS_EVENT, ApiError, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { formatDay, pct } from '../utils/format'

/**
 * Оболочка кабинета — родительский маршрут всех его страниц: меню слева (не меняется от проекта),
 * шапка страницы с вкладками раздела, шкалой лимита и «Ждёт решения», нижняя панель на телефоне.
 */
const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { subscription } = useProfile()
const { plan } = usePlan()
const { pendingCount } = usePending()
const { projectId, project } = useProjectFilter()

// Подписка закончилась или почта не подтверждена — данных кабинета нет, переключатель и загрузки не показываем.
const accessOk = computed(() => !subscription.value || subscription.value.state === 'ok')
const themeLabel = computed(() => (isDark.value ? 'Светлая тема' : 'Тёмная тема'))

// --- Меню ---

const COLLAPSE_KEY = 'aimarketing-side-collapsed'
const collapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
}

const sections = computed(() =>
  SECTIONS.map((s) => ({
    ...s,
    to: sectionTo(s, projectId.value),
    active: route.meta.section === s.key,
  })),
)

const tabbarItems = computed(() =>
  TABBAR_KEYS.map((key) => sections.value.find((s) => s.key === key)).filter((s) => s.to),
)
const moreItems = computed(() => sections.value.filter((s) => !TABBAR_KEYS.includes(s.key)))
const moreActive = computed(
  () => moreItems.value.some((s) => s.active) || SERVICE.some((s) => s.key === route.meta.section),
)

const moreOpen = ref(false)
watch(
  () => route.fullPath,
  () => {
    moreOpen.value = false
  },
)

// --- Шапка страницы ---

const currentSection = computed(() => SECTIONS.find((s) => s.key === route.meta.section) || null)
const title = computed(() => route.meta.title || currentSection.value?.label || '')
/** Под заголовком раздела — по какому проекту он открыт; у служебных страниц — ничего. */
const scopeLabel = computed(() => {
  if (!currentSection.value) return ''
  if (route.params.wsId) return project.value?.name || ''
  return 'Все проекты'
})
const tabs = computed(() => sectionTabs(route.meta.section, route.params.wsId ? projectId.value : null))

/** «12 декабря» — без года, шапка узкая. */
function dayMonth(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

const limitChip = computed(() => {
  const sub = subscription.value
  if (!sub?.plan) return null
  const until = sub.last_day ? ` до ${dayMonth(sub.last_day)}` : ''
  const u = plan.value?.usage
  if (u?.limited && u.used_percent !== null) {
    return {
      percent: Math.min(100, Math.max(0, u.used_percent)),
      text: `Использовано ${pct(u.used_percent, 0)} · ${sub.plan.name}${until}`,
    }
  }
  return { percent: null, text: `${sub.plan.name}${until}` }
})

function logout() {
  clearSession()
  resetWorkspaces()
  resetLoads()
  resetProfile()
  resetPlan()
  resetPending()
  resetProjectFilter()
  router.replace('/login')
}

// --- Лимит периода: предупреждение на 80% и 100%, окно «Лимит исчерпан» с тарифами выше ---

const BANNER_KEY = 'aimarketing-limit-banner'
const dismissed = ref(localStorage.getItem(BANNER_KEY) || '')

const limitLevel = computed(() => {
  const u = plan.value?.usage
  if (!u?.limited) return ''
  if (u.exhausted || u.used_percent >= 100) return 'over'
  return u.used_percent >= 80 ? 'near' : ''
})

const limitBanner = computed(() => {
  const u = plan.value?.usage
  const level = limitLevel.value
  // На странице тарифа шкала и так перед глазами.
  if (!level || route.path === '/cabinet/plan' || dismissed.value === `${u.period_start}:${level}`) return null
  if (level === 'over') {
    return {
      cls: 'notice--warn',
      text: `Лимит на этот период исчерпан — ИИ-помощник, Вордстат и картинки снова заработают ${formatDay(u.period_end)} или сразу после перехода на тариф выше.`,
      action: 'Повысить тариф',
    }
  }
  return {
    cls: '',
    text: `Использовано ${pct(u.used_percent)} лимита на этот период. Новый период — с ${formatDay(u.period_end)}`,
    action: 'Тарифы',
  }
})

function dismissBanner() {
  const value = `${plan.value?.usage?.period_start}:${limitLevel.value}`
  dismissed.value = value
  localStorage.setItem(BANNER_KEY, value)
}

const UPGRADE = {
  'units-exhausted': {
    title: 'Лимит на этот период исчерпан',
    text: 'ИИ-помощник, Вордстат и генерация картинок остановлены до конца периода. Перейдите на тариф выше — они заработают сразу.',
  },
  'feature-locked': {
    title: 'Функция не входит в ваш тариф',
    text: 'Она есть на тарифах ниже.',
  },
  'projects-limit': {
    title: 'Проекты по тарифу закончились',
    text: 'Перейдите на тариф с большим числом проектов или отправьте лишний проект в архив.',
  },
}

const upgrade = ref(null)
const upgradeOffers = computed(() => (plan.value?.offers || []).filter((o) => !o.current))

async function onAccess(event) {
  const { code, message } = event.detail || {}
  const preset = UPGRADE[code]
  if (!preset) return
  const u = plan.value?.usage
  let text = preset.text
  if (code === 'units-exhausted' && u?.period_end) {
    text = `ИИ-помощник, Вордстат и генерация картинок снова заработают ${formatDay(u.period_end)}. Перейдите на тариф выше — они заработают сразу.`
  } else if (code === 'feature-locked' && message) {
    text = `${message}. Перейдите на тариф, где она есть.`
  }
  upgrade.value = { code, title: preset.title, text }
  try {
    await loadPlan(true)
  } catch {
    // в окне останется кнопка «Все тарифы»
  }
}

function chooseOffer() {
  upgrade.value = null
  router.push('/cabinet/plan')
}

// --- Загрузка: профиль, проекты, тариф и «Ждёт решения» (раз в минуту, пока вкладка открыта) ---

const PENDING_MS = 60 * 1000
let pendingTimer = null

function pollPending() {
  if (document.visibilityState === 'visible' && accessOk.value && getSession()) loadPending()
}

async function boot() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    await loadProfile()
    if (accessOk.value) {
      await Promise.all([loadWorkspaces(), loadPlan().catch(() => null), loadPending()])
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      resetWorkspaces()
      resetProfile()
      resetPlan()
      router.replace('/login')
      return
    }
    // Доступ закрылся на ходу — на нужный экран уведёт обработчик в роутере.
    if (err instanceof ApiError && (err.status === 402 || err.status === 403)) return
    pushToast(errorText(err), 'error')
  }
}

// Доступ открылся (подтвердили почту, продлили тариф) — догружаем то, что пропустили при входе.
watch(accessOk, (ok, was) => {
  if (ok && !was) boot()
})

onMounted(() => {
  window.addEventListener(ACCESS_EVENT, onAccess)
  document.addEventListener('visibilitychange', pollPending)
  pendingTimer = setInterval(pollPending, PENDING_MS)
  boot()
})

onBeforeUnmount(() => {
  window.removeEventListener(ACCESS_EVENT, onAccess)
  document.removeEventListener('visibilitychange', pollPending)
  clearInterval(pendingTimer)
})
</script>
