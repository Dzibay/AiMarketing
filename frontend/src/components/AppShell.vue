<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink class="topbar__brand" to="/cabinet" aria-label="AiMarketing — обзор кабинета">
        <span class="topbar__logo"><BrandMark :size="34" /></span>
        <span class="topbar__name">
          <strong>Ai<span>Marketing</span></strong>
          <small>{{ contextLabel }}</small>
        </span>
      </RouterLink>

      <nav v-if="activeGroup && activeGroup.pages.length > 1" class="topbar__tabs" :aria-label="activeGroup.label">
        <RouterLink
          v-for="page in activeGroup.pages"
          :key="page.to"
          class="topbar__tab"
          :class="{ active: pageMatches(page, route.path) }"
          :to="page.to"
        >
          {{ page.label }}
          <Lock v-if="featureLocked(subscription, page.feature)" class="topbar__lock" aria-label="не входит в тариф" />
          <span v-if="page.count" class="topbar__count">{{ page.count }}</span>
        </RouterLink>
      </nav>
      <span v-else class="topbar__spacer"></span>

      <div class="topbar__tools">
        <template v-if="accessOk">
          <WorkspacePicker />
          <LoadsIndicator />
        </template>
        <RouterLink
          v-if="accessOk && wsId && activeGroup?.key !== 'chat'"
          class="btn btn--accent topbar__ai"
          :to="`/w/${wsId}/chat`"
          title="Спросить ИИ"
        >
          <Sparkles aria-hidden="true" />
          <span>Спросить ИИ</span>
        </RouterLink>
        <button
          class="icon-btn topbar__theme"
          type="button"
          :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'"
          @click="toggleTheme"
        >
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <ProfileMenu @logout="logout" />
      </div>
    </header>

    <aside class="rail">
      <nav class="rail__group rail__group--nav" :aria-label="wsId ? 'Разделы пространства' : 'Разделы кабинета'">
        <template v-if="wsId">
          <RouterLink class="rail__btn" to="/cabinet">
            <House aria-hidden="true" />
            <span class="rail__tip">Обзор кабинета</span>
          </RouterLink>
          <span class="rail__sep" aria-hidden="true"></span>
        </template>
        <RouterLink
          v-for="group in groups"
          :key="group.key"
          class="rail__btn"
          :class="{ active: group === activeGroup }"
          :to="group.pages[0].to"
          :aria-label="group.label"
        >
          <component :is="group.icon" aria-hidden="true" />
          <span class="rail__tip">{{ group.label }}</span>
        </RouterLink>
        <RouterLink
          class="rail__btn rail__bottom"
          :class="{ active: settingsGroup === activeGroup }"
          :to="settingsGroup.pages[0].to"
          :aria-label="settingsGroup.label"
        >
          <Settings aria-hidden="true" />
          <span class="rail__tip">{{ settingsGroup.label }}</span>
        </RouterLink>
        <!-- Одна кнопка темы под настройками: подсвеченным в колонке остаётся только текущий раздел. -->
        <button class="rail__btn" type="button" :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
          <span class="rail__tip">{{ isDark ? 'Светлая тема' : 'Тёмная тема' }}</span>
        </button>
      </nav>
    </aside>

    <main class="cabinet shell__main">
      <div v-if="limitBanner" class="notice limit-banner" :class="limitBanner.cls" role="status">
        <span>{{ limitBanner.text }}</span>
        <RouterLink class="btn btn--ghost btn--xs" to="/cabinet/plan">{{ limitBanner.action }}</RouterLink>
        <button class="icon-btn" type="button" aria-label="Скрыть" @click="dismissBanner">
          <X aria-hidden="true" />
        </button>
      </div>
      <slot />
    </main>

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
        v-for="group in groups"
        :key="group.key"
        class="tabbar__item"
        :class="{ active: group === activeGroup }"
        :to="group.pages[0].to"
      >
        <component :is="group.icon" aria-hidden="true" />
        <span>{{ group.short || group.label }}</span>
      </RouterLink>
      <button
        class="tabbar__item"
        :class="{ active: moreOpen || settingsGroup === activeGroup }"
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
        <RouterLink
          class="menu-item"
          :class="{ active: settingsGroup === activeGroup }"
          role="menuitem"
          :to="settingsGroup.pages[0].to"
        >
          <Settings aria-hidden="true" />
          {{ settingsGroup.label }}
        </RouterLink>
        <template v-if="wsId">
          <hr />
          <template v-for="group in cabinetGroups" :key="group.key">
            <RouterLink v-for="page in group.pages" :key="page.to" class="menu-item" role="menuitem" :to="page.to">
              <component :is="group.icon" aria-hidden="true" />
              {{ page.label === 'Обзор' ? 'Обзор кабинета' : page.label }}
            </RouterLink>
          </template>
          <RouterLink class="menu-item" role="menuitem" :to="CABINET_SETTINGS.pages[0].to">
            <Settings aria-hidden="true" />
            Настройки кабинета
          </RouterLink>
        </template>
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
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Ellipsis, House, Lock, LogOut, Moon, Settings, Sparkles, Sun, X } from '@lucide/vue'
import BrandMark from './BrandMark.vue'
import LoadsIndicator from './LoadsIndicator.vue'
import PlanOffers from './PlanOffers.vue'
import ProfileMenu from './ProfileMenu.vue'
import WorkspacePicker from './WorkspacePicker.vue'
import Modal from './ui/Modal.vue'
import { CABINET_NAV, CABINET_SETTINGS, groupMatches, pageMatches, workspaceNav, workspaceSettings } from './shellNav'
import { resetLoads } from '../composables/useLoads'
import { loadPlan, resetPlan, usePlan } from '../composables/usePlan'
import { featureLocked, loadProfile, resetProfile, useProfile } from '../composables/useProfile'
import { clearSession, getSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'
import { loadWorkspaces, resetWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { ACCESS_EVENT, ApiError, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { formatDay, pct } from '../utils/format'

const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { workspaces } = useWorkspaces()
const { subscription } = useProfile()
const { plan } = usePlan()

// Подписка закончилась или почта не подтверждена — данных кабинета нет, переключатель и загрузки не показываем.
const accessOk = computed(() => !subscription.value || subscription.value.state === 'ok')

const moreOpen = ref(false)

const wsId = computed(() => (route.params.wsId ? Number(route.params.wsId) : null))
const currentWs = computed(() => workspaces.value.find((w) => w.id === wsId.value) || null)

const cabinetGroups = CABINET_NAV
const groups = computed(() =>
  wsId.value ? workspaceNav(wsId.value, currentWs.value?.campaigns_count) : CABINET_NAV,
)
const settingsGroup = computed(() => (wsId.value ? workspaceSettings(wsId.value) : CABINET_SETTINGS))
const activeGroup = computed(
  () => [...groups.value, settingsGroup.value].find((group) => groupMatches(group, route.path)) || null,
)

const contextLabel = computed(() => {
  if (!wsId.value) return 'Кабинет'
  return currentWs.value?.client_name || 'Пространство'
})

watch(
  () => route.fullPath,
  () => {
    moreOpen.value = false
  },
)

function logout() {
  clearSession()
  resetWorkspaces()
  resetLoads()
  resetProfile()
  resetPlan()
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
      text: `Лимит на этот период исчерпан — ИИ-ассистент, Вордстат и картинки снова заработают ${formatDay(u.period_end)} или сразу после перехода на тариф выше.`,
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
    text: 'ИИ-ассистент, Вордстат и генерация картинок остановлены до конца периода. Перейдите на тариф выше — они заработают сразу.',
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
    text = `ИИ-ассистент, Вордстат и генерация картинок снова заработают ${formatDay(u.period_end)}. Перейдите на тариф выше — они заработают сразу.`
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

onMounted(async () => {
  window.addEventListener(ACCESS_EVENT, onAccess)
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    await loadProfile()
    if (accessOk.value) await Promise.all([loadWorkspaces(), loadPlan().catch(() => null)])
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
})

onBeforeUnmount(() => window.removeEventListener(ACCESS_EVENT, onAccess))
</script>
