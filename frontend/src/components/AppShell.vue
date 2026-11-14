<template>
  <div class="dshell" :class="{ 'is-expert': expert, 'is-collapsed': sideCollapsed }">
    <!-- До 900 px бокового меню нет: разделы — в нижней панели (BottomBar). -->
    <aside v-if="!narrow" id="dside" class="dside" aria-label="Меню">
      <div class="dside__scroll">
        <div class="dside__head">
          <RouterLink class="dside__brand" to="/cabinet" aria-label="Толк — главная" v-tip.right="tip('Главная')">
            <TolkMark :size="28" />
            <span>толк</span>
          </RouterLink>
          <RouterLink
            v-if="expert"
            class="dside__mode"
            to="/cabinet/settings/interface"
            v-tip.right="'Включён экспертный режим — что он добавляет и как выключить'"
          >
            эксперт
          </RouterLink>
        </div>

        <RouterLink v-if="accessOk" class="dside__newchat" :to="chatTo" v-tip.right="tip('Новый чат')">
          <Plus aria-hidden="true" />
          <span class="srow__t">Новый чат</span>
        </RouterLink>

        <nav class="dside__nav" aria-label="Разделы">
          <RouterLink
            v-for="item in sections"
            :key="item.key"
            class="srow"
            :class="{ active: item.active }"
            :to="item.to"
            v-tip.right="tip(item.label)"
          >
            <component :is="item.icon" aria-hidden="true" />
            <span class="srow__t">{{ item.label }}</span>
          </RouterLink>
        </nav>

        <template v-if="accessOk">
          <h4 class="dside__h">Проекты</h4>
          <button
            class="srow srow--all"
            :class="{ 'is-current': !projectId }"
            type="button"
            :aria-current="projectId ? undefined : 'true'"
            v-tip.right="tip('Все проекты')"
            @click="selectProject(null)"
          >
            <Layers aria-hidden="true" />
            <span class="srow__t">Все проекты</span>
          </button>
          <!-- Свёрнутое меню: проекты — кружки с первой буквой, без папок клиентов. -->
          <template v-if="sideCollapsed">
            <button
              v-for="ws in workspaces"
              :key="ws.id"
              class="srow srow--ava"
              :class="{ 'is-current': ws.id === projectId }"
              type="button"
              :aria-current="ws.id === projectId ? 'true' : undefined"
              v-tip.right="[niceQuotes(ws.name), projectHint(ws)].filter(Boolean).join(' — ')"
              @click="selectProject(ws.id)"
            >
              <span class="pava" :class="`pava--${projectTone(ws)}`" :style="avaStyle(ws)" aria-hidden="true">
                {{ initial(ws.name) }}
              </span>
              <span class="srow__t">{{ niceQuotes(ws.name) }}</span>
              <span v-if="projectTone(ws) === 'warn'" class="srow__t">— счётчик Метрики недоступен</span>
              <span v-else-if="ws.locked" class="srow__t">— не входит в тариф</span>
            </button>
          </template>
          <template v-for="group in sideCollapsed ? [] : projectGroups" :key="group.id">
            <button
              v-if="group.name"
              class="dside__group"
              type="button"
              :aria-expanded="!group.collapsed"
              v-tip="group.collapsed ? 'Развернуть' : 'Свернуть'"
              @click="toggleGroup(group.id)"
            >
              <ChevronDown aria-hidden="true" />
              <span class="dside__group-t">{{ niceQuotes(group.name) }}</span>
              <small>{{ group.items.length }}</small>
            </button>
            <div v-for="ws in group.shown" :key="ws.id" class="srow-wrap" :class="{ 'is-nested': group.name }">
              <button
                class="srow"
                :class="{ 'is-current': ws.id === projectId }"
                type="button"
                :aria-current="ws.id === projectId ? 'true' : undefined"
                v-tip.right="projectHint(ws)"
                @click="selectProject(ws.id)"
              >
                <span class="sdot" :class="`sdot--${projectTone(ws)}`" v-bind="dotA11y(ws)"></span>
                <span class="srow__t">{{ niceQuotes(ws.name) }}</span>
                <Lock v-if="ws.locked" class="srow__lock" aria-label="не входит в тариф" />
              </button>
              <RouterLink
                v-if="ws.id === projectId"
                class="srow__gear"
                :to="`/w/${ws.id}/settings`"
                v-tip="'Настройки проекта'"
                aria-label="Настройки проекта"
              >
                <Settings aria-hidden="true" />
              </RouterLink>
            </div>
          </template>
          <RouterLink class="srow srow--quiet" to="/cabinet/connections" v-tip.right="tip('Добавить сайт')">
            <Plus aria-hidden="true" />
            <span class="srow__t">Добавить сайт</span>
          </RouterLink>

          <template v-for="block in sideCollapsed ? [] : chatBlocks" :key="block.title">
            <h4 class="dside__h">{{ block.title }}</h4>
            <RouterLink
              v-for="c in block.items"
              :key="c.id"
              class="srow srow--chat"
              :class="{ active: String(c.id) === String(route.params.chatId || '') }"
              :to="chatLink(c)"
              v-tip.truncate.right="c.title"
            >
              <span v-if="c.status === 'running'" class="sdot sdot--run" aria-label="ИИ отвечает"></span>
              <span v-else-if="c.pending_actions" class="sdot sdot--lime" aria-hidden="true"></span>
              <MessageSquare v-else aria-hidden="true" />
              <span class="srow__t">{{ c.title }}</span>
              <span v-if="c.pending_actions" class="srow__n" v-tip="`Ждут решения: ${c.pending_actions}`">
                {{ c.pending_actions }}
              </span>
            </RouterLink>
          </template>
          <RouterLink
            v-if="!sideCollapsed && chats.length > shownChats"
            class="srow srow--quiet"
            :to="{ path: chatTo, query: { chats: '1' } }"
          >
            <span class="srow__t">Все чаты</span>
          </RouterLink>
        </template>
      </div>

      <!-- В свёрнутом меню остаётся только шкала с процентом; тариф без лимита там не показываем. -->
      <RouterLink
        v-if="limitChip && !(sideCollapsed && limitChip.percent === null)"
        class="dside__foot"
        :class="`is-${limitLevel || 'ok'}`"
        to="/cabinet/plan"
        v-tip.right="
          sideCollapsed
            ? `Лимит ИИ: ${limitChip.percentText}. ${limitChip.text}`
            : 'Сколько помощника осталось в этом периоде. Нажмите, чтобы открыть тариф'
        "
      >
        <template v-if="limitChip.percent !== null">
          <span class="dside__limit"><span>Лимит ИИ</span><b>{{ limitChip.percentText }}</b></span>
          <span class="dside__bar" aria-hidden="true"><i :style="{ width: `${limitChip.percent}%` }"></i></span>
        </template>
        <small>{{ limitChip.text }}</small>
      </RouterLink>
    </aside>

    <div class="dmain">
      <header class="dbar">
        <button
          v-if="!narrow"
          class="dicon dbar__burger"
          type="button"
          :aria-label="burgerLabel"
          v-tip="burgerLabel"
          aria-controls="dside"
          :aria-expanded="!sideCollapsed"
          @click="toggleSide"
        >
          <Menu aria-hidden="true" />
        </button>
        <!-- На странице чата нижней панели нет — выйти из чата можно стрелкой. -->
        <button v-else-if="inChat" class="dicon dbar__back" type="button" aria-label="Назад" @click="leaveChat">
          <ArrowLeft aria-hidden="true" />
        </button>
        <RouterLink v-else class="dbar__mark" to="/cabinet" aria-label="Толк — главная">
          <TolkMark :size="28" />
        </RouterLink>
        <div class="dbar__scope">
          <ProjectSwitcher v-if="accessOk && currentSection && (project || currentSection.all)" />
        </div>
        <!-- На узком экране счётчик решений — в нижней панели, тема — в «Ещё» и в меню профиля. -->
        <RouterLink
          v-if="!narrow && accessOk && pendingCount > 0"
          class="dchip dchip--btn dchip--lime"
          :class="{ active: route.meta.section === 'pending' }"
          to="/cabinet/pending"
          v-tip="'Предложения ИИ, которые ждут вашего решения'"
        >
          Ждёт решения: {{ pendingCount }}
        </RouterLink>
        <LoadsIndicator v-if="accessOk" :quiet="!expert" />
        <button v-if="!narrow" class="dicon" type="button" :aria-label="themeLabel" v-tip="themeLabel" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <ProfileMenu @logout="logout" />
      </header>

      <div class="dpage">
        <div class="dhead">
          <!-- Описание и действия страницы приходят сюда из PageHeader (Teleport). -->
          <div class="dhead__row">
            <div class="dhead__title">
              <p id="dhead-back" class="dhead__back"></p>
              <div class="dhead__h">
                <h1>{{ pageTitleOverride || title }}</h1>
                <!-- Телефон: кнопка «⋯» с второстепенными действиями страницы (слот more в PageHeader). -->
                <div id="dhead-more" class="dhead__more"></div>
              </div>
              <div id="dhead-desc" class="dhead__desc"></div>
            </div>
            <div id="dhead-acts" class="dhead__acts"></div>
          </div>
          <!-- Вкладки не переносятся: лишние листаются вбок, край с продолжением затенён. -->
          <nav
            v-if="tabs.length > 1"
            class="dtabs"
            :class="{ 'has-before': tabStrip.before, 'has-after': tabStrip.after }"
            :aria-label="title"
          >
            <div ref="tabsEl" class="dtabs__scroll">
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
            </div>
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
              настраивать вручную, включите экспертный режим. Выключить его можно в меню профиля.
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

    <BottomBar v-if="narrow" :access-ok="accessOk" :chat-to="chatTo" :limit-chip="limitChip" @logout="logout" />

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
    <Modal :open="hotkeysOpen" title="Горячие клавиши" @close="hotkeysOpen = false">
      <dl class="hotkeys">
        <template v-for="h in HOTKEYS" :key="h.text">
          <dt>
            <template v-for="(k, i) in h.keys" :key="k">
              <span v-if="i">{{ h.combo ? '+' : 'затем' }}</span>
              <kbd>{{ k }}</kbd>
            </template>
          </dt>
          <dd>{{ h.text }}</dd>
        </template>
      </dl>
      <p class="hint">Ctrl + \ работает всегда, остальные — в экспертном режиме, когда курсор не в поле ввода.</p>
    </Modal>
    <UndoHost />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  ChevronDown,
  Layers,
  Lock,
  Menu,
  MessageSquare,
  Moon,
  Plus,
  Settings,
  SlidersHorizontal,
  Sun,
  X,
} from '@lucide/vue'
import BottomBar from './BottomBar.vue'
import TolkMark from './TolkMark.vue'
import UndoHost from './UndoHost.vue'
import LoadsIndicator from './LoadsIndicator.vue'
import PlanOffers from './PlanOffers.vue'
import ProfileMenu from './ProfileMenu.vue'
import ProjectSwitcher from './ProjectSwitcher.vue'
import Modal from './ui/Modal.vue'
import { SECTIONS, pageMatches, sectionTabs, sectionTo } from './shellNav'
import { useExpert } from '../composables/useExpert'
import { pageTitleOverride } from '../composables/usePageTitle'
import { resetLoads } from '../composables/useLoads'
import { loadPending, resetPending, usePending } from '../composables/usePending'
import { loadPlan, resetPlan, usePlan } from '../composables/usePlan'
import { featureLocked, loadProfile, resetProfile, useProfile } from '../composables/useProfile'
import { resetProjectFilter, useProjectFilter } from '../composables/useProjectFilter'
import { clearSession, getSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'
import { useViewport } from '../composables/useViewport'
import { revealInStrip, useScrollStrip } from '../composables/useScrollStrip'
import {
  loadWorkspaces,
  projectDotA11y as dotA11y,
  projectFolders,
  projectHint,
  projectTone,
  resetWorkspaces,
  useWorkspaces,
} from '../composables/useWorkspaces'
import { ACCESS_EVENT, ApiError, aiApi, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { formatDay, niceQuotes, pct } from '../utils/format'

/**
 * Оболочка кабинета «Диалог» — родительский маршрут всех его страниц. Слева — меню: «Новый чат», разделы,
 * проекты (это фильтр разделов) и лимит ИИ внизу. Сверху — выбранный проект, «Ждёт решения», тема и профиль.
 * На узком экране (до 900 px) бокового меню нет: разделы — в нижней панели (BottomBar), в верхней строке остаются
 * знак, проект, загрузки и профиль. Экспертный режим (меню профиля или Настройки → Интерфейс) открывает ручные разделы.
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
const { narrow } = useViewport()

// Подписка закончилась или почта не подтверждена — данных кабинета нет, проекты и загрузки не показываем.
const accessOk = computed(() => !subscription.value || subscription.value.state === 'ok')
const themeLabel = computed(() => (isDark.value ? 'Светлая тема' : 'Тёмная тема'))

// --- Меню ---

// Шире 900 px меню сворачивается в иконки — кнопкой «Меню» или Ctrl+\. Выбор запоминается; пока его нет,
// на 901–1279 px меню свёрнуто, шире — развёрнуто.
const SIDE_KEY = 'aimarketing-side'
const sidePref = ref(['collapsed', 'open'].includes(localStorage.getItem(SIDE_KEY)) ? localStorage.getItem(SIDE_KEY) : '')
const midQuery = window.matchMedia('(max-width: 1279px)')
const mid = ref(midQuery.matches)
const onMid = (event) => (mid.value = event.matches)
const sideCollapsed = computed(() => !narrow.value && (sidePref.value ? sidePref.value === 'collapsed' : mid.value))
const burgerLabel = computed(() => (sideCollapsed.value ? 'Развернуть меню (Ctrl+\\)' : 'Свернуть меню (Ctrl+\\)'))

function toggleSide() {
  if (narrow.value) return
  sidePref.value = sideCollapsed.value ? 'open' : 'collapsed'
  localStorage.setItem(SIDE_KEY, sidePref.value)
}

/** Подпись пункта в свёрнутом меню, где текста не видно; в развёрнутом подсказка не нужна. */
function tip(text) {
  return sideCollapsed.value ? text : undefined
}

// Кружок проекта: цвет постоянный для проекта (по id), буква — первая буква названия.
const AVA_COLORS = ['#5b8def', '#e08a4f', '#3fa36b', '#a66fd6', '#d4a72c', '#2fa7a7', '#d9667a', '#7d8a3a']

function avaStyle(ws) {
  return { '--ava': AVA_COLORS[Math.abs(Number(ws.id) || 0) % AVA_COLORS.length] }
}

function initial(name) {
  const letter = String(name || '').trim().match(/[\p{L}\p{N}]/u)
  return letter ? letter[0].toUpperCase() : '•'
}
// --- Чаты в меню: общие для «Все проекты», чаты проекта — внутри проекта ---

const RECENT_LIMIT = 5
const chats = ref([])
let chatsReq = 0

async function loadSideChats() {
  // На узком экране меню нет — чаты открываются со страницы чата («Все чаты»).
  if (!accessOk.value || narrow.value) return
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
const shownChats = computed(() => chatBlocks.value.reduce((n, b) => n + b.items.length, 0))

function chatLink(c) {
  return c.workspace_id ? `/w/${c.workspace_id}/chat/${c.id}` : `/cabinet/chat/${c.id}`
}

// Новый чат получает id в адресе, название появляется после первого ответа, решения меняют счётчик —
// поэтому перечитываем список при смене страницы и счётчика.
watch([projectId, () => route.path, pendingCount, accessOk, narrow], loadSideChats, { immediate: true })

const sections = computed(() =>
  SECTIONS.filter((s) => !s.hidden && (expert.value || !s.expert)).map((s) => ({
    ...s,
    to: sectionTo(s, projectId.value) || s.all,
    active: route.meta.section === s.key,
  })),
)

const chatTo = computed(() => sectionTo(SECTIONS.find((s) => s.key === 'chat'), projectId.value))

// Свёрнутые папки клиентов (экспертный режим) — не путать со свёрнутым в иконки меню (SIDE_KEY).
const GROUPS_KEY = 'aimarketing-side-collapsed'
const shutGroups = ref(new Set(readShutGroups()))

function readShutGroups() {
  try {
    const ids = JSON.parse(localStorage.getItem(GROUPS_KEY) || '[]')
    return Array.isArray(ids) ? ids : []
  } catch {
    return []
  }
}

function toggleGroup(id) {
  const next = new Set(shutGroups.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  shutGroups.value = next
  localStorage.setItem(GROUPS_KEY, JSON.stringify([...next]))
}

/**
 * Обычный режим — плоский список проектов. Экспертный — папки клиентов, в которых два проекта и больше: папку можно
 * свернуть, выбранный проект виден и в свёрнутой. Клиент с одним проектом и проекты без клиента идут выше папок
 * без заголовка: заголовок «Мебель Вектор» над единственным «Мебель Вектор» ничего не добавляет.
 */
const projectGroups = computed(() =>
  projectFolders(workspaces.value, grouped.value, expert.value).map((g) => {
    const shut = !!g.name && shutGroups.value.has(g.id)
    return { ...g, collapsed: shut, shown: shut ? g.items.filter((w) => w.id === projectId.value) : g.items }
  }),
)

// --- Верхняя строка и заголовок ---

const currentSection = computed(() => SECTIONS.find((s) => s.key === route.meta.section) || null)
const title = computed(() => route.meta.title || currentSection.value?.title || currentSection.value?.label || '')
const tabs = computed(() =>
  sectionTabs(route.meta.section, route.params.wsId ? projectId.value : null).filter(
    (t) => expert.value || !t.expert || pageMatches(t, route.path),
  ),
)
const expertGate = computed(() => !!route.meta.expert && !expert.value)

// Вкладки листаются вбок; открытая вкладка всегда на виду — в том числе после перехода по ссылке на телефоне.
const tabsEl = ref(null)
const { strip: tabStrip, measure: measureTabs } = useScrollStrip(tabsEl)

watch(
  [tabs, () => route.path, tabsEl],
  () => {
    revealInStrip(tabsEl.value, tabsEl.value?.querySelector('.dtabs__tab.active'))
    measureTabs()
  },
  { flush: 'post' },
)

// Телефон, страница чата: «назад» — туда, откуда пришли; открыли чат по ссылке — на главную.
const inChat = computed(() => route.meta.section === 'chat')

function leaveChat() {
  if (window.history.state?.back) router.back()
  else router.push(sectionTo(SECTIONS[0], projectId.value))
}

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
  if ((event.ctrlKey || event.metaKey) && !event.altKey && (event.code === 'Backslash' || event.key === '\\')) {
    // По физической клавише: в русской раскладке на ней «\» или «/» — всё равно сворачиваем меню. По символу —
    // для экранных клавиатур и удалённого доступа, которые не сообщают клавишу (пустой event.code).
    event.preventDefault()
    toggleSide()
  } else onHotkey(event)
}

// Горячие клавиши экспертного режима. Буквы — по физической клавише (event.code), чтобы работали и в русской
// раскладке; «/» и «?» — по символу или по клавише «/».
const hotkeysOpen = ref(false)
const HOTKEYS = [
  { keys: ['/'], text: 'Поиск на странице' },
  { keys: ['N'], text: 'Новый чат' },
  { keys: ['G', 'H'], text: 'Главная' },
  { keys: ['G', 'C'], text: 'Кампании' },
  { keys: ['G', 'Q'], text: 'Запросы' },
  { keys: ['?'], text: 'Этот список' },
  { keys: ['Esc'], text: 'Закрыть окно или меню' },
  { keys: ['Ctrl', '\\'], combo: true, text: 'Свернуть или развернуть меню слева' },
]
const GO_TO = { KeyH: 'summary', KeyC: 'campaigns', KeyQ: 'queries' }
const GO_WINDOW_MS = 1500
let goPressedAt = 0

function typingIn(el) {
  return el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))
}

function pageSearch() {
  return [...document.querySelectorAll('.cabinet input[type="search"]')].find(
    (el) => !el.disabled && el.getClientRects().length,
  )
}

function onHotkey(event) {
  if (!expert.value || event.defaultPrevented || event.repeat || event.isComposing) return
  if (event.ctrlKey || event.metaKey || event.altKey || typingIn(event.target)) return
  if (document.querySelector('[aria-modal="true"]')) return
  const afterG = Date.now() - goPressedAt < GO_WINDOW_MS
  goPressedAt = 0
  const slashKey = event.code === 'Slash'
  if (event.key === '?' || (slashKey && event.shiftKey)) {
    event.preventDefault()
    hotkeysOpen.value = true
  } else if (event.key === '/' || slashKey) {
    const input = pageSearch()
    if (!input) return
    event.preventDefault()
    input.focus()
    input.select()
  } else if (afterG && GO_TO[event.code]) {
    const to = sectionTo(SECTIONS.find((s) => s.key === GO_TO[event.code]), projectId.value)
    if (to && accessOk.value) router.push(to)
  } else if (event.code === 'KeyG') {
    goPressedAt = Date.now()
  } else if (event.code === 'KeyN' && accessOk.value) {
    router.push(chatTo.value)
  }
}

onMounted(() => {
  window.addEventListener(ACCESS_EVENT, onAccess)
  document.addEventListener('visibilitychange', pollPending)
  document.addEventListener('keydown', onKey)
  midQuery.addEventListener('change', onMid)
  pendingTimer = setInterval(pollPending, PENDING_MS)
  boot()
})

onBeforeUnmount(() => {
  window.removeEventListener(ACCESS_EVENT, onAccess)
  document.removeEventListener('visibilitychange', pollPending)
  document.removeEventListener('keydown', onKey)
  midQuery.removeEventListener('change', onMid)
  clearInterval(pendingTimer)
})
</script>
