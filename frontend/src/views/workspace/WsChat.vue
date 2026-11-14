<template>
  <div
    ref="root"
    class="chat"
    :class="[build ? ['chat--board', `is-tab-${tab}`] : 'chat--solo', { 'chat--hello': showIntro }]"
  >
    <!-- h1 — название чата, раздел «ИИ-помощник» — строкой над ним; у нового чата h1 остаётся названием раздела. -->
    <PageHeader :title="chat?.title || ''">
      <template v-if="chat?.title" #back>
        <span class="chat__crumb">ИИ-помощник</span>
      </template>
      <template #actions>
        <UiSelect
          v-if="!general && expert"
          class="chat__rights"
          size="sm"
          :model-value="accessMode"
          :options="accessOptions"
          :disabled="accessBusy"
          aria-label="Права ИИ в этом проекте"
          v-tip="'Права ИИ в этом проекте'"
          @change="setAccessMode"
        />
        <button v-if="chats.length || chatsError" class="btn btn--ghost btn--sm" type="button" @click="chatsOpen = true">
          <History aria-hidden="true" />
          Все чаты
        </button>
        <UiMenu v-if="chat" size="sm" label="Действия с чатом">
          <UiMenuItem
            v-if="!general && !isFirst"
            :icon="ScanSearch"
            :disabled="!canReview"
            hint="в новом чате"
            @click="startReview()"
          >
            Разбор проекта
          </UiMenuItem>
          <UiMenuItem :icon="Pencil" @click="renameChat">Переименовать</UiMenuItem>
          <UiMenuItem :icon="Trash2" danger @click="deleteChat">Удалить чат</UiMenuItem>
        </UiMenu>
      </template>
    </PageHeader>

    <!-- Все чаты: выезжают слева по кнопке в шапке или по ссылке «Все чаты» в меню. -->
    <ChatList :open="chatsOpen" @close="chatsOpen = false" @new="newChat" />

    <section class="chat__main">
      <div v-if="build" class="seg seg--sm chat__tabs" role="tablist" aria-label="Чат или доска кампании">
        <button type="button" role="tab" :class="{ active: tab === 'chat' }" :aria-selected="tab === 'chat'" @click="tab = 'chat'">
          Чат
        </button>
        <button type="button" role="tab" :class="{ active: tab === 'board' }" :aria-selected="tab === 'board'" @click="tab = 'board'">
          Доска<span v-if="boardWaiting" class="ws-tabs__badge">{{ boardWaiting }}</span>
        </button>
      </div>

      <UiNotice v-if="!aiConfigured" tone="warn" class="chat__wide">
        ИИ временно недоступен: администратор ещё не подключил модель. Напишите в поддержку — подключим.
      </UiNotice>

      <div v-if="showIntro" class="chat__hello">
        <h2>{{ helloTitle }}</h2>
        <p>{{ helloText }}</p>
      </div>

      <ChatThread v-else @to-board="toBoard" />

      <div class="chat__foot">
        <div v-if="activeWrites.length > 1" class="chat__pending">
          <span>
            Ждут решения: {{ activeWrites.length }}<template v-if="laterCount">, отложено: {{ laterCount }}</template>
          </span>
          <button class="btn btn--accent btn--sm" type="button" :disabled="deciding" @click="decideAll(true)">Выполнить все</button>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="deciding" @click="decideAll(false)">Отклонить все</button>
        </div>

        <ChatComposer ref="composer" />
        <p class="chat__access">
          {{ accessText }}
          <RouterLink v-if="!general && expert" :to="{ path: `/w/${ws.id}/settings`, hash: '#access' }">Настроить права</RouterLink>
        </p>
      </div>

      <div v-if="showIntro" class="chat__intro">
        <div class="starters">
          <button
            v-if="!general && isFirst"
            class="starter starter--main"
            type="button"
            :disabled="!canSendStarter"
            v-tip="'ИИ задаст вопросы по продукту, гео, бюджету и цели, изучит спрос в Вордстате и предложит направления'"
            @click="send(NEW_CAMPAIGN_PROMPT)"
          >
            <Sparkles aria-hidden="true" />
            Собрать первую кампанию
          </button>
          <button
            v-else-if="!general"
            class="starter starter--main"
            type="button"
            :disabled="!canReview"
            v-tip="'Что работает, что затухает и почему, что стоит повторить. Сигналы за 30 дней считает код — ИИ получает короткую выжимку'"
            @click="startReview()"
          >
            <ScanSearch aria-hidden="true" />
            {{ reviewing ? 'Запускаем…' : 'Разбор проекта' }}
          </button>
          <button
            v-for="s in starters"
            :key="s.label"
            class="starter"
            type="button"
            v-tip="s.prompt"
            :disabled="!canSendStarter"
            @click="send(s.prompt)"
          >
            {{ s.label }}
          </button>
        </div>

        <p v-if="signalsError" class="hint">Сигналы не посчитались: {{ signalsError }}</p>
        <template v-else-if="signals?.suggestions.length">
          <h3 class="chat__subhead">Заметил в данных</h3>
          <div class="chat__signals">
            <template v-for="s in signals.suggestions" :key="`${s.kind}${s.account_id || ''}${s.experiment_id || ''}`">
              <RouterLink v-if="!s.prompt" class="quick-action quick-action--warn" to="/cabinet/connections">
                <strong>{{ s.title }}</strong>
                <small>{{ s.detail }}</small>
              </RouterLink>
              <button v-else type="button" class="quick-action" :disabled="!canReview" @click="startReview(s)">
                <strong>{{ s.title }}</strong>
                <small>{{ s.detail }}</small>
                <small v-if="s.campaigns.length" class="quick-action__more">{{ campaignsText(s) }}</small>
              </button>
            </template>
          </div>
        </template>
        <details v-if="signals?.notes.length" class="chat__notes">
          <summary>Заметки о данных: {{ signals.notes.length }}</summary>
          <ul>
            <li v-for="n in signals.notes" :key="n">{{ n }}</li>
          </ul>
        </details>
      </div>
    </section>

    <CampaignBoard
      v-if="build"
      ref="board"
      class="chat__board"
      :build="build"
      :actions="actions"
      :site="ws.site || ''"
      :ws-id="ws.id"
      :image-gen="caps.image_gen"
      :disabled="deciding || status === 'running'"
      :edit="editBoard"
      @decide="(id, payload) => respond(() => aiApi.items(id, payload))"
      @draft="(approve) => decide({ id: build.draft.action_id }, approve)"
    />
  </div>
</template>

<script setup>
import { askConfirm, askText } from '../../composables/useConfirm'
import { History, Pencil, ScanSearch, Sparkles, Trash2 } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { aiApi, errorText, wsApi } from '../../api/web'
import { provideChat } from '../../composables/useChat'
import { takeHomeQuestion } from '../../composables/useHomeAsk'
import { pushToast } from '../../composables/useToasts'
import { niceQuotes, pct } from '../../utils/format'
import CampaignBoard from './CampaignBoard.vue'
import { useWs } from './context'
import { useExpert } from '../../composables/useExpert'
import ChatComposer from '../../components/chat/ChatComposer.vue'
import ChatList from '../../components/chat/ChatList.vue'
import ChatThread from '../../components/chat/ChatThread.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiNotice from '../../components/ui/UiNotice.vue'
import UiSelect from '../../components/ui/UiSelect.vue'

/**
 * Страница чата с ИИ: шапка, приветствие с подсказками и сигналами, права ИИ и доска кампании.
 * Сам разговор — в composables/useChat.js; список чатов, лента и поле ввода — в components/chat.
 */
const NEW_CAMPAIGN_PROMPT = 'Хочу собрать новую кампанию. Задай вопросы, которых не хватает, и веди меня по этапам'
// Подсказки пустого чата: на кнопке — коротко, ИИ уходит полный запрос (он же во всплывающей подсказке).
const SUGGESTIONS = [
  { label: 'Где сливается бюджет?', prompt: 'Где сливается бюджет? Найди кампании и фразы с расходом без конверсий' },
  { label: 'Минус-фразы из запросов', prompt: 'Почисти поисковые запросы: предложи минус-фразы' },
  { label: 'План на неделю', prompt: 'Составь план оптимизации на неделю' },
  { label: 'Новая кампания', prompt: NEW_CAMPAIGN_PROMPT },
]
// В проекте без кампаний нет ни статистики, ни запросов: спрашивать про них нечего.
const FIRST_SUGGESTIONS = [
  { label: 'Заполнить бриф', prompt: 'Помоги заполнить бриф: продукт, оффер, гео, бюджет и цель' },
  { label: 'Стратегия и бюджет для старта', prompt: 'Какую стратегию и какой бюджет выбрать для первой кампании?' },
]
// Общий чат: ИИ сравнивает проекты и предлагает правки в любом из них.
const GENERAL_SUGGESTIONS = [
  { label: 'Сравнить проекты за 7 дней', prompt: 'Сравни проекты за последние 7 дней: расход, конверсии, CPA' },
  { label: 'Где сливается бюджет?', prompt: 'Где во всех проектах сливается бюджет без конверсий?' },
  { label: 'Кто выбивается из KPI?', prompt: 'Какие проекты выбиваются из KPI и почему?' },
  {
    label: 'Что остановить, что масштабировать',
    prompt: 'Найди кампании, которые стоит остановить, и те, что стоит масштабировать',
  },
  { label: 'План работ на неделю', prompt: 'Составь план работ на неделю по всем проектам' },
]
const FULL_ACCESS_CONFIRM =
  'Включить полный доступ? ИИ сможет менять бюджеты и ставки, создавать, копировать и удалять кампании без подтверждения.'
// Общий чат живёт вне проекта: заглушка вместо проекта, чтобы шаблон не ветвился на каждом шаге.
const GENERAL_WS = {
  id: null,
  name: 'Все проекты',
  site: '',
  ai_access: { full_access: false, categories: {}, max_change_pct: 30 },
  ai_categories: [],
  campaigns_count: 1,
}

const props = defineProps({
  // Общий чат по всем проектам (раздел «ИИ-помощник» при «Все проекты»).
  general: { type: Boolean, default: false },
})

const route = useRoute()
const router = useRouter()
const wsCtx = props.general ? null : useWs()
const ws = props.general ? ref(GENERAL_WS) : wsCtx.ws
const patch = (fields) => wsCtx?.patch(fields)
const reloadWs = () => wsCtx?.reload()
const basePath = computed(() => (props.general ? '/cabinet/chat' : `/w/${ws.value.id}/chat`))

const chatCtx = provideChat({ general: props.general, ws, basePath, reloadWs })
const {
  chats,
  chatsError,
  chatId,
  chat,
  status,
  showIntro,
  actions,
  activeWrites,
  laterCount,
  build,
  caps,
  boardWaiting,
  deciding,
  decide,
  decideAll,
  respond,
  editBoard,
  draft,
  aiConfigured,
  reviewing,
  canReview,
  canSendStarter,
  fillModels,
  send,
  startReview,
  loadChats,
  loadChat,
} = chatCtx
const { expert } = useExpert()

const signals = ref(null)
const signalsError = ref('')
const accessBusy = ref(false)
const root = ref(null)
const composer = ref(null)
const board = ref(null)
// Список чатов выезжает слева по кнопке «Все чаты»; с доской на узком экране чат и доска — вкладками.
const chatsOpen = ref(false)
const tab = ref('chat')

const access = computed(() => ws.value.ai_access || { full_access: false, categories: {}, max_change_pct: 30 })
const accessText = computed(() => {
  const a = access.value
  if (a.full_access) return 'Полный доступ: ИИ выполняет действия без согласования.'
  const allowed = (ws.value.ai_categories || []).filter((c) => a.categories?.[c.key]).map((c) => c.label.toLowerCase())
  if (!allowed.length) return 'Каждое изменение в Директе — только после вашего подтверждения.'
  return `Без согласования (до ${pct(a.max_change_pct, 0)}): ${allowed.join('; ')}. Остальное — после подтверждения.`
})

// Права ИИ в шапке (эксперт): «с согласованием» или «полный доступ»; категории без согласования — в настройках.
const autoCategories = computed(() => (ws.value.ai_categories || []).filter((c) => access.value.categories?.[c.key]))
const accessMode = computed(() => (access.value.full_access ? 'full' : 'ask'))
const accessOptions = computed(() => {
  const auto = autoCategories.value.map((c) => c.label.toLowerCase())
  return [
    auto.length
      ? { value: 'ask', label: 'ИИ: частично сам', hint: `Сам: ${auto.join(', ')}. Остальное — после подтверждения` }
      : { value: 'ask', label: 'ИИ: с согласованием', hint: 'Каждое изменение — после вашего подтверждения' },
    { value: 'full', label: 'ИИ: полный доступ', hint: 'Выполняет любые действия без подтверждения' },
  ]
})

async function setAccessMode(mode) {
  const on = mode === 'full'
  if (on === !!access.value.full_access || accessBusy.value) return
  if (on && !(await askConfirm(FULL_ACCESS_CONFIRM, { danger: true }))) return
  accessBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { ai_access: { full_access: on } })
    patch(updated)
    pushToast(on ? 'Полный доступ включён' : 'Полный доступ выключен', on ? 'warn' : 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    accessBusy.value = false
  }
}

// Первая кампания: «Разбор» и сигналы без данных пусты, вместо них — сборка по этапам.
const isFirst = computed(() => !ws.value.campaigns_count)
// «Собрать первую кампанию» и «Разбор проекта» — главная кнопка перед подсказками, в подсказках их нет.
const starters = computed(() => {
  if (props.general) return GENERAL_SUGGESTIONS
  return isFirst.value ? FIRST_SUGGESTIONS : SUGGESTIONS
})
const helloTitle = computed(() => (props.general ? 'Чем помочь по всем проектам?' : `Чем помочь с «${niceQuotes(ws.value.name)}»?`))
const helloText = computed(() => {
  if (props.general) {
    return 'Сравню проекты, найду, где сливается бюджет, и предложу правки. Новую кампанию удобнее собирать в чате проекта.'
  }
  if (isFirst.value) {
    return 'Кампаний пока нет — соберу первую по этапам: цели, спрос, группы и фразы, объявления, настройки. Каждый этап согласуете на доске рядом с чатом.'
  }
  // Что ИИ может менять сам, говорит строка прав под полем ввода — здесь не повторяем (и не обещаем лишнего при полном доступе).
  return 'Вижу статистику, кампании, фразы, объявления и поисковые запросы проекта — отвечу и предложу, что поправить.'
})

/** Из плашки в чате — к разделу доски, где это решается (на узком экране — вкладка «Доска»). */
async function toBoard(actionId) {
  tab.value = 'board'
  await nextTick()
  board.value?.showAction(actionId)
}

function campaignsText(card) {
  const names = card.campaigns.map((c) => `«${c.name}»`).join(', ')
  return card.campaigns_total > card.campaigns.length ? `${names} и др.` : names
}

// Сигналы нужны только пустому чату: карточки «чем помочь» вместо догадок.
async function loadSignals() {
  signalsError.value = ''
  try {
    if (props.general) return
    signals.value = await aiApi.signals(ws.value.id)
  } catch (err) {
    signalsError.value = errorText(err)
  }
}

// Чат занимает окно от своей верхней границы до низа: поле ввода внизу и в коротком разговоре.
let topObserver = null
let topFrame = 0
function measureTop() {
  const el = root.value
  if (!el) return
  const top = `${Math.max(0, Math.ceil(el.getBoundingClientRect().top + window.scrollY))}px`
  if (el.style.getPropertyValue('--chat-top') !== top) el.style.setProperty('--chat-top', top)
}
// Замер меняет высоту страницы — из колбэка ResizeObserver переносим его на следующий кадр, иначе браузер ругается на цикл.
function measureTopLater() {
  cancelAnimationFrame(topFrame)
  topFrame = requestAnimationFrame(measureTop)
}

function onResize() {
  measureTop()
  composer.value?.autosize()
}

// --- Действия с чатом -------------------------------------------------------------------------

function newChat() {
  chatsOpen.value = false
  chatCtx.newChat()
}

async function renameChat() {
  const title = await askText('Название чата', { title: 'Переименовать чат', value: chat.value.title, required: true })
  if (title == null || !title.trim() || title.trim() === chat.value.title) return
  try {
    await chatCtx.renameChat(title.trim())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function deleteChat() {
  if (!await askConfirm(`Удалить чат «${chat.value.title}»? Действия, уже выполненные в Директе, останутся в журнале.`)) return
  try {
    await chatCtx.deleteChat()
    newChat()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

// --- Запрос из других вкладок (?ask=…) --------------------------------------------------------

function askPrompt(q) {
  switch (q.ask) {
    case 'problems':
      return 'Разбери проблемы проекта из сводки: где сливается бюджет, что с KPI и темпом расхода. Дай конкретные действия по важности.'
    case 'analytics':
      return `Проанализируй аналитику за период ${q.from || ''} — ${q.to || ''}: динамика расхода, конверсий и CPA, что изменилось и почему, какие кампании тянут результат вниз. Дай рекомендации.`
    case 'campaign':
      return `Разбери кампанию${q.name ? ` «${q.name}»` : ''} (campaign_row_id ${q.cid}): настройки и стратегия, структура, ключевые фразы, объявления, поисковые запросы и статистика. Найди проблемы и предложи правки.`
    case 'audit':
      return 'Проведи аудит всех кампаний проекта: структура, стратегии и бюджеты, минус-фразы, объявления, поисковые запросы, расход без конверсий. Дай список проблем по важности и план правок.'
    case 'experiment':
      return 'Предложи эксперимент: какую гипотезу стоит проверить в этом проекте — перезапуск копией, новое гео, другие ключи или тип кампании, — на каких кампаниях и почему. Опирайся на то, что уже работает, и на прошлые эксперименты.'
    case 'headlines':
      return `Масштабируй выгодные заголовки${q.name ? ` кампании «${q.name}»` : ''}${q.cid ? ` (campaign_row_id ${q.cid})` : ''}: разбери статистику заголовков и конверсии по ключевой цели, покажи лучшие и предложи тест с похожими заголовками. Согласуй каждый шаг.`
    case 'links':
      return 'Проверь ссылки кампаний по шаблону сайта и предложи исправить косяки.'
    default:
      return ''
  }
}

/** Вопросы из разделов «Все проекты» — в общий чат. */
function generalAskPrompt(q) {
  switch (q.ask) {
    case 'analytics':
      return 'Сравни проекты за последнюю неделю: где растёт расход без конверсий, у кого CPA или ДРР хуже KPI, какие кампании тянут результат вниз. Дай по каждому проекту главное действие.'
    // Первые вопросы из мастера «Начало работы».
    case 'first':
      return 'Посмотри мои проекты и кампании и скажи, с чего начать: где теряются деньги, что настроено не так и какие три действия дадут результат быстрее всего.'
    case 'cleanup':
      return 'Найди по всем проектам поисковые запросы, которые тратят деньги без конверсий, и предложи минус-фразы для них.'
    case 'kpi':
      return 'Проверь, в каких проектах не выбрана цель Метрики, не задан KPI или месячный бюджет, и предложи значения по данным за последний месяц.'
    default:
      return ''
  }
}

async function handleAsk() {
  const prompt =
    route.query.ask === 'text'
      ? takeHomeQuestion()
      : props.general
        ? generalAskPrompt(route.query)
        : askPrompt(route.query)
  if (!prompt) {
    await router.replace({ path: basePath.value })
    return
  }
  await router.replace({ path: basePath.value })
  try {
    fillModels(await aiApi.models())
  } catch {
    // Узнаем при отправке.
  }
  if (aiConfigured.value) await send(prompt)
  else draft.value = prompt
}

// Пустой открытый чат или новый: тогда и считаем сигналы — один раз за визит.
watch(
  showIntro,
  (show) => {
    if (show && !props.general && !isFirst.value && !signals.value && !signalsError.value) loadSignals()
  },
  { immediate: true },
)

watch(chatId, () => {
  chatsOpen.value = false
  tab.value = 'chat'
})

// «Все чаты» в боковом меню ведут сюда с ?chats=1: открываем список и убираем метку из адреса.
// Стоит после слежения за chatId — оно закрывает список при смене чата.
watch(
  () => route.query.chats,
  (open) => {
    if (!open) return
    chatsOpen.value = true
    const { chats: _chats, ...query } = route.query
    router.replace({ query })
  },
  { immediate: true },
)

onMounted(async () => {
  window.addEventListener('resize', onResize)
  const page = root.value?.closest('.dpage')
  if (page && typeof ResizeObserver !== 'undefined') {
    topObserver = new ResizeObserver(measureTopLater)
    topObserver.observe(page)
  }
  measureTop()
  // Поле ввода меряем после высоты страницы — как при изменении окна.
  composer.value?.autosize()
  loadChats()
  if (route.query.ask) {
    await handleAsk()
    return
  }
  if (chatId.value) loadChat(chatId.value)
  else
    aiApi
      .models()
      .then(fillModels)
      .catch(() => {})
})

onBeforeUnmount(() => {
  topObserver?.disconnect()
  cancelAnimationFrame(topFrame)
  window.removeEventListener('resize', onResize)
})
</script>
