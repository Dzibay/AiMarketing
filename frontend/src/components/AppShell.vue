<template>
  <div class="dshell" :class="{ 'is-drawer': drawer, 'is-expert': expert }">
    <aside class="dside" aria-label="Меню" @click="closeDrawerOnLink">
      <div class="dside__scroll">
        <RouterLink class="dside__brand" to="/cabinet" aria-label="Толк — главная">
          <TolkMark :size="28" />
          <span>толк</span>
          <em v-if="expert" title="Включён экспертный режим">эксперт</em>
        </RouterLink>

        <RouterLink v-if="accessOk" class="dside__newchat" :to="chatTo">
          <Plus aria-hidden="true" />
          Новый чат
        </RouterLink>

        <nav class="dside__nav" aria-label="Разделы">
          <RouterLink
            v-for="item in sections"
            :key="item.key"
            class="srow"
            :class="{ active: item.active }"
            :to="item.to"
          >
            <component :is="item.icon" aria-hidden="true" />
            <span class="srow__t">{{ item.label }}</span>
          </RouterLink>
        </nav>

        <template v-if="accessOk">
          <h4 class="dside__h">Проекты</h4>
          <button class="srow" :class="{ active: !projectId }" type="button" @click="selectProject(null)">
            <Layers aria-hidden="true" />
            <span class="srow__t">Все проекты</span>
          </button>
          <template v-for="group in projectGroups" :key="group.id">
            <h5 v-if="group.name" class="dside__group">{{ group.name }}</h5>
            <div v-for="ws in group.items" :key="ws.id" class="srow-wrap">
              <button
                class="srow"
                :class="{ active: ws.id === projectId }"
                type="button"
                :title="projectHint(ws)"
                @click="selectProject(ws.id)"
              >
                <span class="sdot" :class="`sdot--${projectTone(ws)}`" aria-hidden="true"></span>
                <span class="srow__t">{{ ws.name }}</span>
                <Lock v-if="ws.locked" class="srow__lock" aria-label="не входит в тариф" />
              </button>
              <RouterLink
                v-if="ws.id === projectId"
                class="srow__gear"
                :to="`/w/${ws.id}/settings`"
                title="Настройки проекта"
                aria-label="Настройки проекта"
              >
                <Settings aria-hidden="true" />
              </RouterLink>
            </div>
          </template>
          <RouterLink class="srow srow--quiet" to="/cabinet/connections">
            <Plus aria-hidden="true" />
            <span class="srow__t">Добавить сайт</span>
          </RouterLink>

          <template v-for="block in chatBlocks" :key="block.title">
            <h4 class="dside__h">{{ block.title }}</h4>
            <RouterLink
              v-for="c in block.items"
              :key="c.id"
              class="srow srow--chat"
              :class="{ active: String(c.id) === String(route.params.chatId || '') }"
              :to="chatLink(c)"
              :title="c.title"
            >
              <span v-if="c.status === 'running'" class="sdot sdot--run" aria-label="ИИ отвечает"></span>
              <span v-else-if="c.pending_actions" class="sdot sdot--lime" aria-hidden="true"></span>
              <MessageSquare v-else aria-hidden="true" />
              <span class="srow__t">{{ c.title }}</span>
              <span v-if="c.pending_actions" class="srow__n" :title="`Ждут решения: ${c.pending_actions}`">
                {{ c.pending_actions }}
              </span>
            </RouterLink>
          </template>
          <RouterLink v-if="chats.length > RECENT_LIMIT" class="srow srow--quiet" :to="chatTo">
            <span class="srow__t">Все чаты</span>
          </RouterLink>
        </template>
      </div>

      <RouterLink
        v-if="limitChip"
        class="dside__foot"
        :class="`is-${limitLevel || 'ok'}`"
        to="/cabinet/plan"
        title="Сколько помощника осталось в этом периоде. Нажмите, чтобы открыть тариф"
      >
        <template v-if="limitChip.percent !== null">
          <span class="dside__limit"><span>Лимит ИИ</span><b>{{ limitChip.percentText }}</b></span>
          <span class="dside__bar" aria-hidden="true"><i :style="{ width: `${limitChip.percent}%` }"></i></span>
        </template>
        <small>{{ limitChip.text }}</small>
      </RouterLink>
    </aside>

    <button class="dscrim" type="button" aria-label="Закрыть меню" @click="drawer = false"></button>

    <div class="dmain">
      <header class="dbar">
        <button class="dicon dbar__burger" type="button" aria-label="Меню" @click="drawer = true">
          <Menu aria-hidden="true" />
        </button>
        <div class="dbar__scope">
          <template v-if="currentSection">
            <RouterLink
              v-if="project"
              class="dchip dchip--btn"
              :to="`/w/${project.id}/settings`"
              title="Открыть настройки проекта"
            >
              <span class="sdot" :class="`sdot--${projectTone(project)}`" aria-hidden="true"></span>
              <span class="dchip__t">{{ project.name }}</span>
              <Settings aria-hidden="true" />
            </RouterLink>
            <span v-else-if="currentSection.all" class="dchip">
              <Layers aria-hidden="true" />
              Все проекты
            </span>
          </template>
        </div>
        <RouterLink
          v-if="accessOk && pendingCount > 0"
          class="dchip dchip--btn dchip--lime"
          :class="{ active: route.meta.section === 'pending' }"
          to="/cabinet/pending"
          title="Предложения ИИ, которые ждут вашего решения"
        >
          Ждёт решения: {{ pendingCount }}
        </RouterLink>
        <LoadsIndicator v-if="accessOk && expert" />
        <button class="dicon" type="button" :aria-label="themeLabel" :title="themeLabel" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <ProfileMenu @logout="logout" />
      </header>

      <div class="dpage">
        <div class="dhead">
          <h1>{{ title }}</h1>
          <nav v-if="tabs.length > 1" class="dtabs" :aria-label="title">
            <RouterLink
              v-for="tab in tabs"
              :key="tab.to"
              class="dtabs__tab"
              :class="{ active: pageMatches(tab, route.path) }"
              :to="tab.to"
            >
              {{ tab.label }}
              <Lock v-if="featureLocked(subscription, tab.feature)" class="dtabs__lock" aria-label="не входит в тариф" />
            </RouterLink>
          </nav>
        </div>

        <main class="cabinet app__content">
          <div v-if="limitBanner" class="notice limit-banner" :class="limitBanner.cls" role="status">
            <span>{{ limitBanner.text }}</span>
            <RouterLink class="btn btn--ghost btn--xs" :to="limitBanner.to || '/cabinet/plan'">{{ limitBanner.action }}</RouterLink>
            <button class="icon-btn" type="button" aria-label="Скрыть" @click="dismissBanner">
              <X aria-hidden="true" />
            </button>
          </div>

          <!-- Ручной инструмент открыли по ссылке, а экспертный режим выключен: объясняем, где он, вместо пустой страницы. -->
          <section v-if="expertGate" class="dgate">
            <SlidersHorizontal aria-hidden="true" />
            <h2>Это раздел экспертного режима</h2>
            <p>
              В обычном режиме ИИ сам предлагает такие изменения — их видно на главной и в «Ждёт решения». Чтобы
              настраивать вручную, включите экспертный режим. Выключить его можно там же, в настройках.
            </p>
            <div>
              <button class="btn btn--accent" type="button" @click="setExpert(true)">Включить экспертный режим</button>
              <RouterLink class="btn btn--ghost" to="/cabinet">На главную</RouterLink>
            </div>
          </section>
          <RouterView v-else />
        </main>
      </div>
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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Layers, Lock, Menu, MessageSquare, Moon, Plus, Settings, SlidersHorizontal, Sun, X } from '@lucide/vue'
import TolkMark from './TolkMark.vue'
import LoadsIndicator from './LoadsIndicator.vue'
import PlanOffers from './PlanOffers.vue'
import ProfileMenu from './ProfileMenu.vue'
import Modal from './ui/Modal.vue'
import { SECTIONS, pageMatches, sectionTabs, sectionTo } from './shellNav'
import { useExpert } from '../composables/useExpert'
import { resetLoads } from '../composables/useLoads'
import { loadPending, resetPending, usePending } from '../composables/usePending'
import { loadPlan, resetPlan, usePlan } from '../composables/usePlan'
import { featureLocked, loadProfile, resetProfile, useProfile } from '../composables/useProfile'
import { resetProjectFilter, useProjectFilter } from '../composables/useProjectFilter'
import { clearSession, getSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'
import { loadWorkspaces, resetWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { ACCESS_EVENT, ApiError, aiApi, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { formatDay, pct } from '../utils/format'

/**
 * Оболочка кабинета «Диалог» — родительский маршрут всех его страниц. Слева — меню: «Новый чат», разделы,
 * проекты (это фильтр разделов) и лимит ИИ внизу. Сверху — выбранный проект, «Ждёт решения», тема и профиль.
 * На узком экране меню выезжает по кнопке. Экспертный режим (Настройки → Интерфейс) открывает ручные разделы.
 */
const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { subscription } = useProfile()
const { plan } = usePlan()
const { pendingCount } = usePending()
const { projectId, project, selectProject } = useProjectFilter()
const { workspaces, grouped } = useWorkspaces()
const { expert, setExpert } = useExpert()

// Подписка закончилась или почта не подтверждена — данных кабинета нет, проекты и загрузки не показываем.
const accessOk = computed(() => !subscription.value || subscription.value.state === 'ok')
const themeLabel = computed(() => (isDark.value ? 'Светлая тема' : 'Тёмная тема'))

// --- Меню ---

const drawer = ref(false)
watch(
  () => route.fullPath,
  () => {
    drawer.value = false
  },
)
// --- Чаты в меню: общие для «Все проекты», чаты проекта — внутри проекта ---

const RECENT_LIMIT = 6
const chats = ref([])
let chatsReq = 0

async function loadSideChats() {
  if (!accessOk.value) return
  const req = ++chatsReq
  const wsId = projectId.value
  try {
    const res = wsId ? await aiApi.chats(wsId) : await aiApi.generalChats()
    if (req === chatsReq) chats.value = res.items || []
  } catch {
    // Меню без чатов — не ошибка страницы.
    if (req === chatsReq) chats.value = []
  }
}

const chatBlocks = computed(() => {
  const waiting = chats.value.filter((c) => c.pending_actions > 0)
  const rest = chats.value.filter((c) => !c.pending_actions).slice(0, Math.max(0, RECENT_LIMIT - waiting.length))
  return [
    { title: 'Ждут вашего решения', items: waiting },
    { title: 'Недавние чаты', items: rest },
  ].filter((b) => b.items.length)
})

function chatLink(c) {
  return c.workspace_id ? `/w/${c.workspace_id}/chat/${c.id}` : `/cabinet/chat/${c.id}`
}

// Новый чат получает id в адресе, название появляется после первого ответа, решения меняют счётчик —
// поэтому перечитываем список при смене страницы и счётчика.
watch([projectId, () => route.path, pendingCount, accessOk], loadSideChats, { immediate: true })

// Ссылка на текущую страницу не меняет адрес — закрываем меню по самому нажатию.
function closeDrawerOnLink(event) {
  if (event.target.closest('a')) drawer.value = false
}

const sections = computed(() =>
  SECTIONS.filter((s) => !s.hidden && (expert.value || !s.expert)).map((s) => ({
    ...s,
    to: sectionTo(s, projectId.value) || s.all,
    active: route.meta.section === s.key,
  })),
)

const chatTo = computed(() => sectionTo(SECTIONS.find((s) => s.key === 'chat'), projectId.value))

/** В обычном режиме — плоский список проектов, в экспертном — по папкам клиентов. */
const projectGroups = computed(() => {
  if (expert.value && grouped.value.length > 1) return grouped.value
  return [{ id: 'all', name: '', items: workspaces.value }]
})

function projectTone(ws) {
  if (ws.locked) return 'off'
  return ws.counter_ok === false ? 'warn' : 'ok'
}

function projectHint(ws) {
  if (ws.locked) return 'Проект не входит в тариф'
  if (ws.counter_ok === false) return 'Счётчик Метрики недоступен или выключен'
  return ws.site || ''
}

// --- Верхняя строка и заголовок ---

const currentSection = computed(() => SECTIONS.find((s) => s.key === route.meta.section) || null)
const title = computed(() => route.meta.title || currentSection.value?.title || currentSection.value?.label || '')
const tabs = computed(() =>
  sectionTabs(route.meta.section, route.params.wsId ? projectId.value : null).filter(
    (t) => expert.value || !t.expert || pageMatches(t, route.path),
  ),
)
const expertGate = computed(() => !!route.meta.expert && !expert.value)

/** «12 декабря» — без года, место узкое. */
function dayMonth(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

const limitChip = computed(() => {
  const sub = subscription.value
  if (!sub?.plan) return null
  const until = sub.last_day ? ` до ${dayMonth(sub.last_day)}` : ''
  const u = plan.value?.usage
  const text = `Тариф ${sub.plan.name}${until}`
  if (u?.limited && u.used_percent !== null) {
    return { percent: Math.min(100, Math.max(0, u.used_percent)), percentText: pct(u.used_percent, 0), text }
  }
  return { percent: null, text }
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

// --- Конец тарифа (за 3 дня и в последний день) и лимит периода (80% / 100%), окно «Лимит исчерпан» ---

const BANNER_KEY = 'aimarketing-limit-banner'
const dismissed = ref(localStorage.getItem(BANNER_KEY) || '')

const limitLevel = computed(() => {
  const u = plan.value?.usage
  if (!u?.limited) return ''
  if (u.exhausted || u.used_percent >= 100) return 'over'
  return u.used_percent >= 80 ? 'near' : ''
})

/** Сколько дней до конца подписки с тарифом (последний день — 1), если осталось не больше 3. */
const endsIn = computed(() => {
  const sub = subscription.value
  if (!sub?.plan || !sub.ends_at || sub.state !== 'ok') return 0
  const left = (new Date(sub.ends_at).getTime() - Date.now()) / 86400000
  if (left <= 0 || left > 3) return 0
  return left <= 1 ? 1 : 3
})

const endBanner = computed(() => {
  const sub = subscription.value
  const days = endsIn.value
  if (!days || dismissed.value === `end:${sub.ends_at}:${days}`) return null
  const last = formatDay(sub.last_day)
  const trial = sub.plan.is_trial
  const what = trial ? 'Пробный период' : `Тариф «${sub.plan.name}»`
  return {
    key: `end:${sub.ends_at}:${days}`,
    cls: 'notice--warn',
    text:
      days === 1
        ? `${what} заканчивается — последний день ${last}. Затем ИИ-помощник и загрузка из Яндекса остановятся, данные сохранятся.`
        : `${what} действует по ${last}. Продлите заранее, чтобы ИИ-помощник и загрузка из Яндекса не остановились.`,
    action: trial ? 'Выбрать тариф' : 'Продлить',
    to: trial ? '/cabinet/plan#offers' : `/cabinet/plan?choose=${sub.plan.id}`,
  }
})

const limitBanner = computed(() => {
  // На странице тарифа срок, шкала и тарифы и так перед глазами.
  if (route.path === '/cabinet/plan') return null
  if (endBanner.value) return endBanner.value
  const u = plan.value?.usage
  const level = limitLevel.value
  if (!level || dismissed.value === `${u.period_start}:${level}`) return null
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
  const value = limitBanner.value?.key || `${plan.value?.usage?.period_start}:${limitLevel.value}`
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

function chooseOffer(offer) {
  upgrade.value = null
  router.push(offer?.id ? `/cabinet/plan?choose=${offer.id}` : '/cabinet/plan')
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

function onKey(event) {
  if (event.key === 'Escape') drawer.value = false
}

onMounted(() => {
  window.addEventListener(ACCESS_EVENT, onAccess)
  document.addEventListener('visibilitychange', pollPending)
  document.addEventListener('keydown', onKey)
  pendingTimer = setInterval(pollPending, PENDING_MS)
  boot()
})

onBeforeUnmount(() => {
  window.removeEventListener(ACCESS_EVENT, onAccess)
  document.removeEventListener('visibilitychange', pollPending)
  document.removeEventListener('keydown', onKey)
  clearInterval(pendingTimer)
})
</script>
