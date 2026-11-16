<template>
  <div ref="scroller" class="chat__messages" @scroll="onScroll">
    <div class="chat__thread">
      <UiNotice v-if="loadError" tone="error">{{ loadError }}</UiNotice>
      <div v-else-if="chatId && !chat" class="chat__loading" role="status" aria-label="Загружаем чат…">
        <span class="ui-skel chat__loading-q" />
        <span class="ui-skel" />
        <span class="ui-skel" />
        <span class="ui-skel chat__loading-short" />
      </div>

      <template v-for="item in items" :key="item.key">
        <div v-if="item.role === 'user'" class="msg msg--user" :data-msg="item.id">
          <div class="msg__bubble"><template v-for="(part, i) in mentionParts(item)" :key="i"><RouterLink v-if="part.ref" class="mention" :to="`/w/${part.ref.id}/summary`">{{ part.text }}</RouterLink><template v-else>{{ part.text }}</template></template></div>
          <p v-if="item.context?.label" class="msg__context">Про: {{ item.context.label }}</p>
          <div v-if="item.created || item.content" class="msg__meta">
            <time v-if="item.created" class="msg__time" :datetime="item.created">{{ formatDateTime(item.created) }}</time>
            <UiMenu v-if="item.content" class="msg__menu" size="sm" label="Действия с сообщением">
              <UiMenuItem :icon="BookmarkPlus" @click="saveAsTask(item)">
                Сохранить как задачу
              </UiMenuItem>
            </UiMenu>
          </div>
        </div>

        <p v-else-if="item.role === 'note'" class="chat-note">
          <PencilLine aria-hidden="true" />
          Правки на доске: {{ item.content }}
        </p>

        <UiNotice v-else-if="item.role === 'error'" tone="error" class="chat-error">
          {{ item.content }}
          <template v-if="errorInFeed && item.key === lastMessageKey" #action>
            <button class="btn btn--ghost btn--sm" type="button" :disabled="!canRetry" @click="retryTurn">
              <RotateCcw aria-hidden="true" />
              Повторить
            </button>
          </template>
        </UiNotice>

        <div v-else class="msg msg--ai" :data-msg="item.id">
          <div v-if="item.content" class="msg__text md" @click="onTextClick" v-html="renderMarkdown(item.content, { sortable: true })" />
          <ChatBlocks v-if="item.blocks?.length" :blocks="item.blocks" />

          <details v-if="item.reads.length" class="chat-steps">
            <summary>
              Что я посмотрел · {{ item.reads.length }}
              <span v-if="item.reads.some((r) => r.failed)" class="tone-bad">· есть ошибки</span>
            </summary>
            <ul>
              <li v-for="r in item.reads" :key="r.id">
                <details v-if="expert && (hasArgs(r.args) || r.result != null)" class="chat-step">
                  <summary>
                    {{ r.label }}
                    <span v-if="r.result == null" class="hint">…</span>
                    <span v-else-if="r.failed" class="tone-bad">ошибка</span>
                  </summary>
                  <pre v-if="hasArgs(r.args)">{{ pretty(r.args) }}</pre>
                  <pre v-if="r.result != null">{{ r.result }}</pre>
                </details>
                <template v-else>
                  {{ r.label }}
                  <span v-if="r.result == null" class="hint">…</span>
                  <span v-else-if="r.failed" class="tone-bad">— ошибка</span>
                </template>
              </li>
            </ul>
          </details>

          <template v-for="a in item.asks" :key="a.key">
            <div v-if="a.plaque" class="board-plaque" :class="`is-${a.status}`" :data-action="a.id">
              <span>{{ a.plaque }}</span>
              <small v-if="a.result?.comment">Комментарий: {{ a.result.comment }}</small>
              <button class="ai-check__link" type="button" @click="toBoard(a.id)">{{ panel ? 'Открыть доску' : 'К доске' }}</button>
            </div>
            <AiPlanCard
              v-else-if="a.tool === 'propose_plan'"
              :action="a"
              :progress="planProgress(a)"
              :disabled="deciding || status === 'running'"
              @submit="respond(() => aiApi.plan(a.id, $event))"
            />
            <!-- В панели таблица и картинки не помещаются: сводка и «Развернуть» — чат на весь экран. -->
            <div v-else-if="a.tool === 'propose_items' && panel" class="action-card action-card--brief" :class="`is-${a.status}`" :data-action="a.id">
              <div class="action-card__head">
                <strong class="action-card__title">{{ a.args?.title || a.label }}</strong>
                <UiStatus v-if="a.status !== 'pending'" :tone="STATUS_UI[a.status] || 'off'" :label="STATUS_LABELS[a.status] || a.status" />
              </div>
              <p class="action-card__summary">{{ itemsBrief(a) }}</p>
              <button class="btn btn--ghost btn--sm" type="button" @click="expand(a.id)">
                <Maximize2 aria-hidden="true" />
                Развернуть
              </button>
            </div>
            <AiItemsCard
              v-else-if="a.tool === 'propose_items'"
              :data-action="a.id"
              :action="a"
              :site="ws.site || ''"
              :disabled="deciding || status === 'running'"
              @submit="respond(() => aiApi.items(a.id, $event))"
            />
            <AiQuestionsCard
              v-else
              :action="a"
              :caps="caps"
              :disabled="deciding || status === 'running'"
              @submit="respond(() => aiApi.answer(a.id, $event))"
            />
          </template>

          <template v-for="a in item.writes" :key="a.key">
            <div v-if="a.plaque" class="board-plaque" :class="`is-${a.status}`" :data-action="a.id">
              <span>{{ a.plaque }}</span>
              <button class="ai-check__link" type="button" @click="toBoard(a.id)">{{ panel ? 'Открыть доску' : 'К доске' }}</button>
            </div>
            <div v-else-if="isLater(a)" class="action-card action-card--later" :data-action="a.id">
              <div class="action-card__head">
                <strong class="action-card__title">{{ a.label }}</strong>
                <UiStatus tone="off" :icon="Clock" label="отложено" />
              </div>
              <p v-if="projectName(a)" class="action-card__meta"><span>{{ projectName(a) }}</span></p>
              <p class="action-card__summary">{{ a.summary }}</p>
              <p class="hint">
                Ничего не изменилось. Предложение лежит в «Ждёт решения» среди отложенных и в счётчик не входит. Новое
                сообщение в этом чате его отклонит, если не выбрать «Оставить и отправить».
              </p>
              <div class="action-card__buttons">
                <button class="btn btn--ghost btn--sm" type="button" @click="setLater(a, false)">Решить сейчас</button>
              </div>
            </div>
            <!-- Выполнено или выполняется: строка с галочкой, подробности — по кнопке. -->
            <div
              v-else-if="folded(a)"
              class="action-card action-card--done"
              :class="{ 'is-fresh': fresh[a.id] }"
              :data-action="a.id"
            >
              <span class="done-check"><Check aria-hidden="true" /></span>
              <strong class="action-card__title">{{ a.label }}</strong>
              <span class="action-card__state">{{ a.status === 'done' ? 'выполнено' : 'выполняется…' }}</span>
              <button class="action-card__fold" type="button" :aria-expanded="false" @click="unfolded[a.id] = true">
                Подробнее
              </button>
            </div>
            <div v-else class="action-card" :class="`is-${a.status}`" :data-action="a.id">
              <div class="action-card__head">
                <span v-if="a.status === 'pending'" class="action-card__tag">
                  <Sparkles aria-hidden="true" />
                  Предлагаю
                </span>
                <strong class="action-card__title">{{ a.label }}</strong>
                <UiStatus v-if="a.status !== 'pending'" :tone="STATUS_UI[a.status] || 'off'" :label="STATUS_LABELS[a.status] || a.status" />
                <button
                  v-if="FOLDABLE.has(a.status)"
                  class="action-card__fold"
                  type="button"
                  :aria-expanded="true"
                  @click="unfolded[a.id] = false"
                >
                  Свернуть
                </button>
              </div>
              <p v-if="projectName(a) || a.plan_step || a.auto || isDetached(a)" class="action-card__meta">
                <span v-if="projectName(a)">{{ projectName(a) }}</span>
                <span v-if="a.plan_step">шаг {{ a.plan_step }} плана</span>
                <span v-if="a.auto">без согласования</span>
                <span v-if="isDetached(a)">оставлено ждать: разговор продолжился без решения</span>
              </p>
              <p class="action-card__summary">{{ a.summary }}</p>
              <AiChanges v-if="a.changes?.length" :changes="a.changes" />
              <p v-if="a.status === 'pending' && a.picks && panel" class="ai-picks ai-picks--brief">
                <span>{{ picksBrief(a) }}</span>
                <button class="ai-check__link" type="button" @click="expand(a.id)">Развернуть</button>
              </p>
              <div v-else-if="a.status === 'pending' && a.picks" class="ai-picks">
                <p class="hint">Снимите галочки с того, что не нужно, — этого не будет в действии.</p>
                <AiCheckTable
                  v-model:off="excluded[a.id]"
                  :columns="a.picks.columns"
                  :rows="a.picks.rows"
                  :groups="a.picks.groups"
                  :disabled="deciding"
                />
              </div>
              <p v-if="a.removed?.length" class="hint ai-removed">
                Снято перед выполнением ({{ a.removed.length }}):
                <s v-for="(r, i) in a.removed.slice(0, 12)" :key="i">{{ r.section ? `${r.section}: ${r.text}` : r.text }}</s>
                <span v-if="a.removed.length > 12">и ещё {{ a.removed.length - 12 }}</span>
              </p>
              <p v-if="a.reason" class="hint">Зачем: {{ a.reason }}</p>
              <p v-if="expert && a.categories?.length" class="action-card__cats">
                <span v-for="c in a.categories" :key="c.id" class="chip">{{ c.label }}</span>
              </p>
              <details v-if="expert && hasArgs(a.argsShown)" class="action-card__details">
                <summary>Параметры</summary>
                <pre>{{ pretty(a.argsShown) }}</pre>
              </details>
              <p v-if="a.status === 'error' && a.error" class="tone-bad">{{ a.error }}</p>
              <details v-if="expert && a.status === 'done' && a.result != null" class="action-card__details">
                <summary>Результат</summary>
                <pre>{{ pretty(a.result) }}</pre>
              </details>
              <p v-if="a.status === 'rejected' && a.error" class="hint">{{ a.error }}</p>
              <div v-if="a.status === 'pending' && a.id" class="action-card__buttons action-card__buttons--split">
                <!-- На телефоне этот ряд прилипает к низу экрана: «Выполнить» во всю ширину, «Позже» — в «⋯». -->
                <div class="action-card__acts">
                  <p v-if="(phone || panel) && offCount(a)" class="action-card__acts-note">
                    {{ allOff(a) ? 'Сняты все строки — отклоните действие' : `Снято строк: ${offCount(a)} — выполню без них` }}
                  </p>
                  <button
                    v-coach="'proposal'"
                    class="btn btn--accent"
                    type="button"
                    :disabled="deciding || allOff(a)"
                    @click="decide(a, true)"
                  >
                    <Check aria-hidden="true" />
                    {{ offCount(a) && !phone && !panel ? `Выполнить без снятых (${offCount(a)})` : 'Выполнить' }}
                  </button>
                  <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decide(a, false)">Отклонить</button>
                  <UiMenu v-if="phone || panel" :disabled="deciding">
                    <UiMenuItem :icon="Clock" hint="решить можно здесь или в «Ждёт решения»" @click="setLater(a, true)">
                      Позже
                    </UiMenuItem>
                  </UiMenu>
                  <button
                    v-else
                    class="btn btn--ghost"
                    type="button"
                    :disabled="deciding"
                    v-tip="'Свернуть карточку: решить можно позже здесь или в «Ждёт решения»'"
                    @click="setLater(a, true)"
                  >
                    Позже
                  </button>
                </div>
                <span v-if="allOff(a) && !phone && !panel" class="hint">Сняты все строки — отклоните действие</span>
                <UiCheckbox
                  v-if="!general && a.categories?.length && !a.categories.some((c) => c.id === 'destructive')"
                  v-model="remember[a.id]"
                  class="check-row"
                >
                  Больше не спрашивать для «{{ a.categories.map((c) => c.label).join('», «') }}»
                </UiCheckbox>
                <p v-if="!expert && !panel" class="action-card__note">
                  Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».
                </p>
              </div>
            </div>
          </template>

          <div v-if="item.content" class="msg__foot">
            <p v-if="item.created || (expert && item.model)" class="msg__meta">
              <span v-if="expert && item.model">{{ item.model }}</span>
              <time v-if="item.created" :datetime="item.created">{{ formatDateTime(item.created) }}</time>
            </p>
            <ChatRate :message-id="item.id" :feedback="item.feedback" />
          </div>

          <div v-if="item.supportAsk" class="chat-support">
            <span>В справке ответа нет — спросите команду, ответим здесь же.</span>
            <button class="btn btn--ghost btn--sm" type="button" @click="openSupport(item.supportAsk)">
              <LifeBuoy aria-hidden="true" />
              Написать в поддержку
            </button>
          </div>

          <div v-if="item.key === followupKey" class="chat-next" role="group" aria-label="Что дальше">
            <button
              v-for="f in item.followups"
              :key="f"
              class="chat-next__btn"
              type="button"
              :disabled="!canSendStarter"
              @click="send(f)"
            >
              <CornerDownRight aria-hidden="true" />
              {{ f }}
            </button>
          </div>
        </div>
      </template>

      <div v-if="status === 'running'" class="msg msg--ai msg--thinking" role="status">
        <span class="spinner" aria-hidden="true" />
        <span class="msg__thinking">
          {{ thinkingText }}
          <small v-if="longWait">Можно уйти со страницы: ответ придёт в уведомления.</small>
        </span>
      </div>
      <UiNotice v-if="status === 'error' && chat?.error && !errorInFeed" tone="error" class="chat-error">
        {{ chat.error }}
        <template #action>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="!canRetry" @click="retryTurn">
            <RotateCcw aria-hidden="true" />
            Повторить
          </button>
        </template>
      </UiNotice>
    </div>
  </div>
</template>

<script setup>
import { BookmarkPlus, Check, Clock, CornerDownRight, LifeBuoy, Maximize2, PencilLine, RotateCcw, Sparkles } from '@lucide/vue'
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { openSupport } from '../../composables/useSupport'
import { aiApi, errorText } from '../../api/web'
import { askText } from '../../composables/useConfirm'
import { saveOwnPrompt } from '../../composables/usePrompts'
import { pushToast } from '../../composables/useToasts'
import { STATUS_LABELS, mentionParts, useChat } from '../../composables/useChat'
import { useExpert } from '../../composables/useExpert'
import { useViewport } from '../../composables/useViewport'
import { formatDateTime, plural } from '../../utils/format'
import { foldFrom, heightOf } from '../../utils/foldAnim'
import { renderMarkdown, sortMdTable } from '../../utils/markdown'
import ChatBlocks from './blocks/ChatBlocks.vue'
import AiChanges from '../../views/workspace/AiChanges.vue'
import AiCheckTable from '../../views/workspace/AiCheckTable.vue'
import AiItemsCard from '../../views/workspace/AiItemsCard.vue'
import AiPlanCard from '../../views/workspace/AiPlanCard.vue'
import AiQuestionsCard from '../../views/workspace/AiQuestionsCard.vue'
import UiCheckbox from '../ui/UiCheckbox.vue'
import ChatRate from './ChatRate.vue'
import UiMenu from '../ui/UiMenu.vue'
import UiMenuItem from '../ui/UiMenuItem.vue'
import UiNotice from '../ui/UiNotice.vue'
import UiStatus from '../ui/UiStatus.vue'

/** Лента чата: реплики, ответы ИИ с шагами чтения, вопросы, план и карточки действий на согласование. */
const STATUS_UI = { pending: 'wait', approved: 'info', done: 'ok', rejected: 'off', error: 'bad' }

// to-board — показать на доске раздел, где решается действие (id действия).
const emit = defineEmits(['to-board'])
const router = useRouter()

/** Ссылка ИИ на экран кабинета (/cabinet/…, /w/12/settings) — переход роутера, без перезагрузки страницы. */
function onTextClick(event) {
  if (sortMdTable(event)) return
  const link = event.target instanceof Element ? event.target.closest('a[data-route]') : null
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button) return
  event.preventDefault()
  router.push(link.getAttribute('href'))
}

const {
  general,
  panel,
  basePath,
  ws,
  chatId,
  chat,
  status,
  loadError,
  items,
  thinkingText,
  errorInFeed,
  lastMessageKey,
  followupKey,
  canSendStarter,
  send,
  canRetry,
  retryTurn,
  caps,
  remember,
  excluded,
  deciding,
  isLater,
  isDetached,
  setLater,
  projectName,
  offCount,
  allOff,
  planProgress,
  decide,
  respond,
  scroller,
  onScroll,
} = useChat()
// Ответ готовится дольше 10 секунд — подсказка, что ждать на странице не обязательно (useAnswerWatch).
const LONG_WAIT_MS = 10000
const longWait = ref(false)
let longTimer = 0
watch(
  status,
  (s) => {
    clearTimeout(longTimer)
    longWait.value = false
    if (s === 'running') longTimer = setTimeout(() => (longWait.value = true), LONG_WAIT_MS)
  },
  { immediate: true },
)
onBeforeUnmount(() => clearTimeout(longTimer))

const { expert } = useExpert()
const { phone } = useViewport()

/** Чат на весь экран — страницей, к нужной карточке (из панели, где таблицы и картинки не помещаются). */
function expand(actionId) {
  if (!chatId.value) return
  router.push({ path: `${basePath.value}/${chatId.value}`, hash: actionId ? `#a${actionId}` : '' })
}

/** «К доске»: на странице чата — раздел доски рядом; в панели доски нет — открываем чат страницей. */
function toBoard(actionId) {
  if (panel) expand(actionId)
  else emit('to-board', actionId)
}

const rowsText = (n) => `${n} ${plural(n, 'строка', 'строки', 'строк')}`

/** Сводка карточки «выбор строк и картинок» для узкой панели. */
function itemsBrief(a) {
  const rows = a.args?.rows || []
  const pics = rows.filter((r) => r.image).length
  const head = a.args?.intro || a.summary || ''
  if (a.status !== 'pending' || !rows.length) return head
  const what = a.args?.stage === 'images' && pics ? `${pics} ${plural(pics, 'картинка', 'картинки', 'картинок')}` : rowsText(rows.length)
  return [head, `${what} на выбор`].filter(Boolean).join(' · ')
}

/** Сводка таблицы с галочками в карточке действия: сколько строк и сколько снято. */
function picksBrief(a) {
  const total = a.picks?.rows?.length || 0
  const off = offCount(a)
  return off ? `${rowsText(total)}, снято ${off}` : `${rowsText(total)} — все войдут в действие`
}

// После «Выполнить» карточка сворачивается в строку с галочкой — главный момент продукта, он должен ощущаться.
// Раскрыть подробности можно кнопкой; ошибка и отказ не сворачиваются — там есть что прочитать.
const FOLDABLE = new Set(['approved', 'done'])
const unfolded = reactive({})
const fresh = reactive({})
const seenStatus = new Map()

function folded(a) {
  return FOLDABLE.has(a.status) && !!a.id && !unfolded[a.id]
}

// Ловим переход «ждёт решения» → «выполняется/выполнено» в открытом чате: меряем карточку до перерисовки
// (watch срабатывает раньше неё) и сжимаем на месте строки после.
watch(
  items,
  (list) => {
    for (const item of list || []) {
      for (const a of item.writes || []) {
        if (!a.id) continue
        if (seenStatus.get(a.id) === 'pending' && FOLDABLE.has(a.status) && !unfolded[a.id]) {
          const selector = `.action-card[data-action="${a.id}"]`
          const from = heightOf(selector)
          fresh[a.id] = true
          nextTick(() => foldFrom(selector, from))
          setTimeout(() => delete fresh[a.id], 1500)
        }
        seenStatus.set(a.id, a.status)
      }
    }
  },
  { immediate: true },
)

function hasArgs(args) {
  return args && typeof args === 'object' && Object.keys(args).length > 0
}

function pretty(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

/** Своё сообщение — в «Мои задачи»: название по первой строке, его можно поправить. */
async function saveAsTask(item) {
  const first = item.content.trim().split('\n')[0].replace(/\s+/g, ' ')
  const value = first.length > 60 ? `${first.slice(0, 59).trimEnd()}…` : first
  const title = await askText('Название задачи', {
    title: 'Сохранить как задачу',
    value,
    message: 'Текст сообщения станет задачей: её можно вставить в поле ввода кнопкой «Задачи» или «/».',
    required: true,
  })
  if (title == null) return
  try {
    await saveOwnPrompt(title.trim(), item.content)
    pushToast('Задача сохранена — найдёте её через «Задачи» или «/»')
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}
</script>
