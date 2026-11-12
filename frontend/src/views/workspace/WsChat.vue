<template>
  <div class="chat" :class="build ? ['chat--board', `is-tab-${tab}`, { 'is-chats-open': chatsOpen }] : null">
    <aside class="chat__side panel" :aria-label="general ? 'Общие чаты' : 'Чаты проекта'">
      <button class="btn btn--accent chat__new" type="button" :disabled="sending" @click="newChat">Новый чат</button>
      <p v-if="chatsError" class="empty">{{ chatsError }}</p>
      <p v-else-if="!chats.length" class="hint">Чатов пока нет.</p>
      <nav v-else class="chat__list" aria-label="Чаты">
        <RouterLink
          v-for="c in chats"
          :key="c.id"
          :to="`${basePath}/${c.id}`"
          class="chat__item"
          :class="{ active: c.id === chatId }"
        >
          <span class="chat__item-title">{{ c.title }}</span>
          <small>
            <span v-if="c.status === 'running'" class="tone-warn">отвечает…</span>
            <span v-else-if="c.pending_actions" class="ws-tabs__badge">{{ c.pending_actions }}</span>
            <span v-else-if="c.status === 'error'" class="tone-bad">ошибка</span>
            {{ formatDateTime(c.updated_at) }}
          </small>
        </RouterLink>
      </nav>
    </aside>

    <section class="chat__main panel">
      <header class="chat__head">
        <div class="chat__title">
          <h2>{{ chat?.title || 'Новый чат' }}</h2>
          <p class="hint">{{ accessText }}</p>
        </div>
        <div class="row-actions">
          <button v-if="build" class="btn btn--ghost chat__chats-toggle" type="button" @click="chatsOpen = true">
            Чаты
          </button>
          <button
            v-if="!isFirst && !general"
            class="btn"
            type="button"
            :disabled="!canReview"
            title="Что работает, что затухает и почему, что стоит повторить"
            @click="startReview()"
          >
            {{ reviewing ? 'Запускаем…' : 'Разбор' }}
          </button>
          <label
            v-if="!general && expert"
            class="switch"
            :title="access.full_access ? 'Выключить полный доступ' : 'Включить полный доступ'"
          >
            <input type="checkbox" :checked="access.full_access" :disabled="accessBusy" @change="toggleFullAccess($event)" />
            <span>Полный доступ</span>
          </label>
          <RouterLink
            v-if="!general && expert"
            class="btn btn--ghost"
            :to="{ path: `/w/${ws.id}/settings`, hash: '#access' }"
          >
            Права ИИ
          </RouterLink>
          <template v-if="chat">
            <button class="btn btn--ghost" type="button" @click="renameChat">Переименовать</button>
            <button class="btn btn--ghost btn--danger" type="button" @click="deleteChat">Удалить</button>
          </template>
        </div>
      </header>

      <div v-if="build" class="seg seg--sm chat__tabs" role="tablist" aria-label="Чат или доска кампании">
        <button type="button" role="tab" :class="{ active: tab === 'chat' }" :aria-selected="tab === 'chat'" @click="tab = 'chat'">
          Чат
        </button>
        <button type="button" role="tab" :class="{ active: tab === 'board' }" :aria-selected="tab === 'board'" @click="tab = 'board'">
          Доска<span v-if="boardWaiting" class="ws-tabs__badge">{{ boardWaiting }}</span>
        </button>
      </div>

      <p v-if="!aiConfigured" class="notice notice--warn">
        ИИ временно недоступен: администратор ещё не подключил модель. Напишите в поддержку — подключим.
      </p>

      <div ref="scroller" class="chat__messages" @scroll="onScroll">
        <p v-if="loadError" class="empty">{{ loadError }}</p>
        <p v-else-if="chatId && !data" class="empty">Загружаем чат…</p>

        <div v-else-if="!items.length" class="chat__intro">
          <template v-if="general">
            <h3>Чем помочь по всем проектам?</h3>
            <p class="hint">
              ИИ читает статистику, кампании, фразы, объявления и запросы любого вашего проекта и сравнивает их между
              собой. Правки он предлагает карточками — каждая выполняется только после вашего подтверждения. Новую
              кампанию удобнее собирать в чате самого проекта: там доска сборки. Напишите @, чтобы указать проект.
            </p>
          </template>
          <h3 v-else>Чем помочь с «{{ ws.name }}»?</h3>
          <p v-if="general" hidden></p>
          <p v-else-if="isFirst" class="hint">
            Кампаний здесь пока нет, поэтому разбирать нечего. ИИ соберёт первую по этапам — цели, анализ спроса,
            группы и фразы, объявления, настройки. Рядом с чатом появится доска кампании: на ней вы согласуете
            этапы, поправите фразы, заголовки и тексты и увидите объявление в выдаче. Любое изменение в Директе ИИ
            сначала покажет вам.
          </p>
          <p v-else class="hint">
            ИИ видит статистику, кампании, ключевые фразы, объявления и поисковые запросы этого проекта. Любое
            изменение в Директе он сначала покажет вам на согласование.
          </p>
          <div v-if="!general" class="chat__review">
            <button
              v-if="isFirst"
              class="btn btn--accent"
              type="button"
              :disabled="!canSendStarter"
              @click="send(NEW_CAMPAIGN_PROMPT)"
            >
              Собрать первую кампанию
            </button>
            <button v-else class="btn btn--accent" type="button" :disabled="!canReview" @click="startReview()">
              {{ reviewing ? 'Запускаем…' : 'Разбор проекта' }}
            </button>
            <span v-if="isFirst" class="hint">
              ИИ задаст вопросы по продукту, гео, бюджету и цели, изучит спрос в Вордстате и предложит направления.
            </span>
            <span v-else class="hint">
              Что работает, что затухает и почему, что стоит повторить. Сигналы за 30 дней считает код — ИИ получает
              короткую выжимку, а не сырые таблицы.
            </span>
          </div>

          <p v-if="signalsError" class="hint">Сигналы не посчитались: {{ signalsError }}</p>
          <template v-else-if="signals?.suggestions.length">
            <h4 class="chat__subhead">Заметил в данных</h4>
            <div class="chat__suggestions">
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

          <h4 class="chat__subhead">Или спросите сами</h4>
          <div class="chat__suggestions">
            <button v-for="s in starters" :key="s" type="button" class="quick-action" :disabled="!aiConfigured" @click="send(s)">
              {{ s }}
            </button>
          </div>
        </div>

        <template v-for="item in items" :key="item.key">
          <div v-if="item.role === 'user'" class="msg msg--user">
            <div class="msg__bubble"><template v-for="(part, i) in mentionParts(item)" :key="i"><RouterLink v-if="part.ref" class="mention" :to="`/w/${part.ref.id}/summary`">{{ part.text }}</RouterLink><template v-else>{{ part.text }}</template></template></div>
          </div>

          <p v-else-if="item.role === 'note'" class="chat-note">
            <span aria-hidden="true">✎</span> Правки на доске: {{ item.content }}
          </p>

          <div v-else-if="item.role === 'error'" class="notice notice--warn chat-error">
            <span>{{ item.content }}</span>
            <button
              v-if="errorInFeed && item.key === lastMessageKey"
              class="btn btn--ghost btn--xs"
              type="button"
              :disabled="!canRetry"
              @click="retryTurn"
            >
              Повторить
            </button>
          </div>

          <div v-else class="msg msg--ai">
            <div v-if="item.content" class="msg__bubble md" v-html="renderMarkdown(item.content)" />
            <small v-if="expert && item.content && item.model" class="msg__model">{{ item.model }}</small>

            <div v-if="item.reads.length" class="chat-steps">
              <details v-for="r in item.reads" :key="r.id" class="chat-step">
                <summary>
                  <span class="chat-step__label">{{ r.label }}</span>
                  <span v-if="r.result == null" class="hint">…</span>
                  <span v-else-if="r.failed" class="tone-bad">ошибка</span>
                </summary>
                <pre v-if="hasArgs(r.args)">{{ pretty(r.args) }}</pre>
                <pre v-if="r.result != null">{{ r.result }}</pre>
              </details>
            </div>

            <template v-for="a in item.asks" :key="a.key">
              <div v-if="a.plaque" class="board-plaque" :class="`is-${a.status}`" :data-action="a.id">
                <span>{{ a.plaque }}</span>
                <small v-if="a.result?.comment">Комментарий: {{ a.result.comment }}</small>
                <button class="ai-check__link" type="button" @click="toBoard(a.id)">К доске</button>
              </div>
              <AiPlanCard
                v-else-if="a.tool === 'propose_plan'"
                :action="a"
                :progress="planProgress(a)"
                :disabled="deciding || status === 'running'"
                @submit="respond(() => aiApi.plan(a.id, $event))"
              />
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
                <button class="ai-check__link" type="button" @click="toBoard(a.id)">К доске</button>
              </div>
              <div v-else-if="isLater(a)" class="action-card action-card--later" :data-action="a.id">
                <div class="action-card__head">
                  <strong>{{ a.label }}</strong>
                  <span class="chip">отложено</span>
                  <span v-if="projectName(a)" class="chip chip--project">{{ projectName(a) }}</span>
                </div>
                <p class="action-card__summary">{{ a.summary }}</p>
                <p class="hint">
                  Ничего не изменилось. Предложение ждёт на главной в «Ждут вашего решения»; новое сообщение в этом чате
                  его отклонит.
                </p>
                <div class="action-card__buttons">
                  <button class="btn btn--ghost" type="button" @click="setLater(a, false)">Решить сейчас</button>
                </div>
              </div>
              <div v-else class="action-card" :class="`is-${a.status}`" :data-action="a.id">
                <div class="action-card__head">
                  <strong>{{ a.label }}</strong>
                  <span class="chip" :class="STATUS_CHIPS[a.status]">{{ STATUS_LABELS[a.status] || a.status }}</span>
                  <span v-if="projectName(a)" class="chip chip--project">{{ projectName(a) }}</span>
                  <span v-if="a.plan_step" class="chip">шаг {{ a.plan_step }} плана</span>
                  <span v-if="a.auto" class="chip">без согласования</span>
                </div>
                <p class="action-card__summary">{{ a.summary }}</p>
                <AiChanges v-if="a.changes?.length" :changes="a.changes" />
                <div v-if="a.status === 'pending' && a.picks" class="ai-picks">
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
                <p v-if="a.categories?.length" class="action-card__cats">
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
                <div v-if="a.status === 'pending' && a.id" class="action-card__buttons">
                  <button class="btn btn--accent" type="button" :disabled="deciding || allOff(a)" @click="decide(a, true)">
                    {{ offCount(a) ? `Выполнить без снятых (${offCount(a)})` : 'Выполнить' }}
                  </button>
                  <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decide(a, false)">Отклонить</button>
                  <button
                    class="btn btn--ghost"
                    type="button"
                    :disabled="deciding"
                    title="Свернуть карточку: решить можно позже здесь или на главной"
                    @click="setLater(a, true)"
                  >
                    Позже
                  </button>
                  <span v-if="allOff(a)" class="hint">Сняты все строки — отклоните действие</span>
                  <label
                    v-if="!general && a.categories?.length && !a.categories.some((c) => c.id === 'destructive')"
                    class="check-row"
                  >
                    <input v-model="remember[a.id]" type="checkbox" />
                    <span>Больше не спрашивать для «{{ a.categories.map((c) => c.label).join('», «') }}»</span>
                  </label>
                  <p v-if="!expert" class="hint action-card__note">
                    Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».
                  </p>
                </div>
              </div>
            </template>
          </div>
        </template>

        <div v-if="status === 'running'" class="msg msg--ai">
          <div class="msg__bubble msg__bubble--thinking"><span class="spinner" /> {{ thinkingText }}</div>
        </div>
        <div v-if="status === 'error' && chat?.error && !errorInFeed" class="notice notice--warn chat-error">
          <span>{{ chat.error }}</span>
          <button class="btn btn--ghost btn--xs" type="button" :disabled="!canRetry" @click="retryTurn">Повторить</button>
        </div>
      </div>

      <div v-if="activeWrites.length > 1" class="chat__pending">
        <span>
          Ждут решения: {{ activeWrites.length }}<template v-if="laterCount">, отложено: {{ laterCount }}</template>
        </span>
        <button class="btn btn--accent" type="button" :disabled="deciding" @click="decideAll(true)">Выполнить все</button>
        <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decideAll(false)">Отклонить все</button>
      </div>

      <form class="chat__composer" @submit.prevent="send()">
        <div class="chat__field">
          <div v-if="picker.open" id="mention-list" class="menu-pop mention-pop" role="listbox" aria-label="Проекты">
            <button
              v-for="(w, i) in picker.items"
              :id="`mention-${w.id}`"
              :key="w.id"
              type="button"
              class="menu-item mention-pop__item"
              :class="{ 'is-active': i === picker.index }"
              role="option"
              :aria-selected="i === picker.index"
              @mousedown.prevent="pickProject(w)"
              @mousemove="picker.index = i"
            >
              <strong>{{ w.name }}</strong>
              <small v-if="w.site">{{ w.site }}</small>
            </button>
          </div>
          <textarea
            ref="input"
            v-model="draft"
            rows="2"
            maxlength="8000"
            aria-label="Сообщение ИИ"
            :aria-controls="picker.open ? 'mention-list' : null"
            :aria-activedescendant="picker.open && picker.items[picker.index] ? `mention-${picker.items[picker.index].id}` : null"
            :disabled="status === 'running' || !aiConfigured"
            :placeholder="placeholder"
            @input="updatePicker"
            @click="updatePicker"
            @keyup.left="updatePicker"
            @keyup.right="updatePicker"
            @blur="closePicker"
            @keydown="onKeydown"
          />
        </div>
        <div class="chat__send">
          <select
            v-if="modelItems.length > 1"
            class="chat__model-pick"
            :value="selectedClass"
            :disabled="modelBusy || status === 'running' || !aiConfigured"
            aria-label="Модель ответа"
            title="Модель ответа: больше точек — подробнее думает и быстрее расходует лимит"
            @change="chooseModel($event.target.value)"
          >
            <option v-for="m in modelItems" :key="m.id" :value="m.id">{{ m.name }} {{ dots(m.dots) }}</option>
          </select>
          <button
            v-if="otherProjects.length"
            class="icon-btn"
            type="button"
            :title="general ? 'Указать проект' : 'Сослаться на другой проект'"
            :aria-label="general ? 'Указать проект' : 'Сослаться на другой проект'"
            :disabled="status === 'running' || !aiConfigured"
            @mousedown.prevent
            @click="insertAt"
          >
            <AtSign />
          </button>
          <button class="btn btn--accent" type="submit" :disabled="!canSend">
            {{ sending ? 'Отправляем…' : 'Отправить' }}
          </button>
        </div>
      </form>
    </section>

    <template v-if="build">
      <CampaignBoard
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
      <div v-if="chatsOpen" class="chat-backdrop" @click="chatsOpen = false" />
    </template>
  </div>
</template>

<script setup>
import { askConfirm, askText } from '../../composables/useConfirm'
import { AtSign } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { aiApi, errorText, wsApi } from '../../api/web'
import { takeHomeQuestion } from '../../composables/useHomeAsk'
import { refreshPending } from '../../composables/usePending'
import { pushToast } from '../../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'
import { formatDateTime, pct } from '../../utils/format'
import { renderMarkdown } from '../../utils/markdown'
import AiChanges from './AiChanges.vue'
import AiCheckTable from './AiCheckTable.vue'
import AiItemsCard from './AiItemsCard.vue'
import AiPlanCard from './AiPlanCard.vue'
import AiQuestionsCard from './AiQuestionsCard.vue'
import CampaignBoard from './CampaignBoard.vue'
import { useWs } from './context'
import { useExpert } from '../../composables/useExpert'

const POLL_MS = 1500
const NEW_CAMPAIGN_PROMPT = 'Хочу собрать новую кампанию. Задай вопросы, которых не хватает, и веди меня по этапам'
const SUGGESTIONS = [
  NEW_CAMPAIGN_PROMPT,
  'Проанализируй кампании за последние 7 дней и дай рекомендации',
  'Где сливается бюджет? Найди кампании и фразы с расходом без конверсий',
  'Почисти поисковые запросы: предложи минус-фразы',
  'Составь план оптимизации на неделю',
]
// В проекте без кампаний нет ни статистики, ни запросов: спрашивать про них нечего.
const FIRST_SUGGESTIONS = [
  'Помоги заполнить бриф: продукт, оффер, гео, бюджет и цель',
  'Какую стратегию и какой бюджет выбрать для первой кампании?',
]
// Общий чат: ИИ сравнивает проекты и предлагает правки в любом из них.
const GENERAL_SUGGESTIONS = [
  'Сравни проекты за последние 7 дней: расход, конверсии, CPA',
  'Где во всех проектах сливается бюджет без конверсий?',
  'Какие проекты выбиваются из KPI и почему?',
  'Найди кампании, которые стоит остановить, и те, что стоит масштабировать',
  'Составь план работ на неделю по всем проектам',
]
// Общий чат живёт вне проекта: заглушка вместо проекта, чтобы шаблон не ветвился на каждом шаге.
const GENERAL_WS = {
  id: null,
  name: 'Все проекты',
  site: '',
  ai_access: { full_access: false, categories: {}, max_change_pct: 30 },
  ai_categories: [],
  campaigns_count: 1,
}
const STATUS_LABELS = {
  pending: 'ждёт решения',
  approved: 'выполняется',
  done: 'выполнено',
  rejected: 'отклонено',
  error: 'ошибка',
}
const STATUS_CHIPS = { pending: 'chip--warning', done: 'chip--ok', error: 'chip--bad' }
const STATUS_TONES = { pending: 'tone-warn', done: 'tone-ok', error: 'tone-bad' }

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
const { workspaces } = useWorkspaces()

const chats = ref([])
const chatsError = ref('')
const data = ref(null)
const loadError = ref('')
const aiConfigured = ref(true)
const aiModels = ref(null)
const draft = ref('')
const sending = ref(false)
const reviewing = ref(false)
const signals = ref(null)
const signalsError = ref('')
const deciding = ref(false)
const accessBusy = ref(false)
const remember = reactive({})
// {id действия: [id снятых строк]} — что маркетолог убрал из карточки перед «Выполнить».
const excluded = reactive({})
const scroller = ref(null)
const input = ref(null)
let pollTimer = null
let stickToBottom = true

const chatId = computed(() => Number(route.params.chatId) || null)
const chat = computed(() => (data.value && data.value.chat.id === chatId.value ? data.value.chat : null))
const status = computed(() => chat.value?.status || 'idle')
// Ошибка хода уже стоит в ленте последней строкой (с размером запроса) — плашку с тем же текстом не дублируем.
const errorInFeed = computed(() => {
  const last = chat.value ? data.value.messages[data.value.messages.length - 1] : null
  return last?.role === 'error' && !!chat.value.error && last.content.startsWith(chat.value.error)
})
const lastMessageKey = computed(() => {
  const messages = chat.value ? data.value.messages : []
  return messages.length ? `m${messages[messages.length - 1].id}` : ''
})
const retrying = ref(false)
// «Повторить» — тот же ход заново: ИИ продолжит с места сбоя, без нового сообщения.
const canRetry = computed(
  () => status.value === 'error' && !retrying.value && !sending.value && !deciding.value && aiConfigured.value,
)
const access = computed(() => ws.value.ai_access || { full_access: false, categories: {}, max_change_pct: 30 })
const actions = computed(() => (chat.value ? data.value.actions : []))
const pendingActions = computed(() => actions.value.filter((a) => a.status === 'pending'))
// Вопросы, план и таблицы закрываются ответом в своей карточке, а не кнопками «Выполнить / Отклонить все».
const pendingWrites = computed(() => pendingActions.value.filter((a) => a.kind !== 'ask'))

const { expert } = useExpert()

// «Позже»: карточка сворачивается только у вас в браузере, на сервере действие так и ждёт решения.
const LATER_KEY = 'aimarketing-later'
const later = ref(new Set(readLater()))
function readLater() {
  try {
    const ids = JSON.parse(localStorage.getItem(LATER_KEY) || '[]')
    return Array.isArray(ids) ? ids : []
  } catch {
    return []
  }
}
const activeWrites = computed(() => pendingWrites.value.filter((a) => !later.value.has(a.id)))
const laterCount = computed(() => pendingWrites.value.length - activeWrites.value.length)
const isLater = (a) => a.status === 'pending' && !!a.id && later.value.has(a.id)
function setLater(a, on) {
  const next = new Set(later.value)
  if (on) next.add(a.id)
  else next.delete(a.id)
  later.value = next
  try {
    localStorage.setItem(LATER_KEY, JSON.stringify([...next].slice(-200)))
  } catch {
    // хранилище недоступно — свёрнуто до перезагрузки
  }
}
const pendingAsks = computed(() => pendingActions.value.filter((a) => a.kind === 'ask'))
const plans = computed(() => actions.value.filter((a) => a.tool === 'propose_plan'))
// Сборка новой кампании — доской на пол-экрана рядом с чатом; таблицы этапов решаются на ней.
const build = computed(() => (chat.value ? data.value.build || null : null))
// Что подключено у маркетолога: варианты ответа, которым это нужно, без него не выбрать.
const caps = computed(() => ({ image_gen: !!data.value?.image_gen?.configured }))
const onBoard = computed(() => new Set(build.value?.action_ids || []))
const boardWaiting = computed(() => pendingActions.value.filter((a) => onBoard.value.has(a.id)).length)
const board = ref(null)
// С доской список чатов выезжает по кнопке, а на узком экране чат и доска — вкладками.
const chatsOpen = ref(false)
const tab = ref('chat')

// @-упоминания других проектов: ИИ получает их на чтение. Подставленные из списка помним по id,
// набранные руками находим по названию в тексте при отправке.
const mentions = ref([])
const picker = reactive({ open: false, index: 0, items: [], start: 0, end: 0 })
const otherProjects = computed(() => workspaces.value.filter((w) => w.id !== ws.value.id && !w.archived && !w.locked))
loadWorkspaces().catch(() => null)

const accessText = computed(() => {
  const a = access.value
  if (a.full_access) return 'Полный доступ: ИИ выполняет действия без согласования.'
  const allowed = (ws.value.ai_categories || []).filter((c) => a.categories?.[c.key]).map((c) => c.label.toLowerCase())
  if (!allowed.length) return 'Каждое изменение в Директе — только после вашего подтверждения.'
  return `Без согласования (до ${pct(a.max_change_pct, 0)}): ${allowed.join('; ')}. Остальное — после подтверждения.`
})

// Класс модели: у открытого чата — сохранённый, у нового — выбранный здесь до первого сообщения.
const pickedClass = ref('')
const modelBusy = ref(false)
const modelItems = computed(() => aiModels.value?.items || [])
const selectedClass = computed(() => {
  const m = aiModels.value
  if (!m) return ''
  if (chat.value) return m.selected || m.default
  const has = (id) => !!id && modelItems.value.some((c) => c.id === id)
  if (has(pickedClass.value)) return pickedClass.value
  // Новый чат проекта сервер создаст на модели из настроек «Помощника» — показываем её же.
  const projectClass = props.general ? '' : ws.value?.ai_style?.model_class
  return has(projectClass) ? projectClass : m.default
})

/** Относительный расход класса: ●○○, ●●○, ●●●. */
function dots(n) {
  const k = Math.max(1, Math.min(3, Number(n) || 1))
  return '●'.repeat(k) + '○'.repeat(3 - k)
}

function fillModels(res) {
  aiModels.value = res || null
  aiConfigured.value = !!res?.configured
}

async function chooseModel(id) {
  pickedClass.value = id
  if (!chat.value) return
  modelBusy.value = true
  try {
    const res = await aiApi.setChatModel(chat.value.id, id)
    if (data.value?.chat?.id === res.chat.id) data.value = { ...data.value, chat: { ...data.value.chat, ...res.chat }, ai: res.ai }
    fillModels(res.ai)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    modelBusy.value = false
  }
}

const placeholder = computed(() => {
  if (!aiConfigured.value) return 'ИИ временно недоступен'
  if (status.value === 'running') return 'ИИ отвечает…'
  if (pendingActions.value.length && pendingActions.value.every((a) => onBoard.value.has(a.id))) {
    return 'Решите этап на доске или напишите своими словами'
  }
  if (pendingAsks.value.length) {
    return pendingWrites.value.length
      ? 'Ответьте в карточке или напишите своими словами; действия, которые ждут решения, будут отклонены'
      : 'Ответьте в карточке или напишите своими словами'
  }
  if (pendingWrites.value.length) return 'Новое сообщение отклонит действия, которые ждут решения'
  const tip = otherProjects.value.length ? (props.general ? ', @ — указать проект' : ', @ — сослаться на другой проект') : ''
  return `Задача для ИИ: проанализировать, найти проблемы, создать, поправить… (Enter — отправить, Shift+Enter — новая строка${tip})`
})
const canSend = computed(
  () => !!draft.value.trim() && !sending.value && status.value !== 'running' && aiConfigured.value,
)
const canReview = computed(
  () => !reviewing.value && !sending.value && status.value !== 'running' && aiConfigured.value,
)
// Первая кампания: «Разбор» и сигналы без данных пусты, вместо них — сборка по этапам.
const isFirst = computed(() => !ws.value.campaigns_count)
const starters = computed(() => {
  if (props.general) return GENERAL_SUGGESTIONS
  return isFirst.value ? [NEW_CAMPAIGN_PROMPT, ...FIRST_SUGGESTIONS] : SUGGESTIONS
})

/** Проект карточки действия в общем чате — чтобы не перепутать, где ИИ что-то меняет. */
function projectName(action) {
  if (!props.general) return ''
  const id = action.args?.project_id
  return id == null ? '' : data.value?.projects?.[String(id)] || `проект ${id}`
}
const canSendStarter = computed(() => !sending.value && status.value !== 'running' && aiConfigured.value)

const thinkingText = computed(() => {
  const messages = chat.value ? data.value.messages : []
  const last = messages[messages.length - 1]
  if (last?.role === 'assistant' && last.tool_calls?.length) return `${last.tool_calls[last.tool_calls.length - 1].label}…`
  if (actions.value.some((a) => a.status === 'approved')) return 'Выполняю действия…'
  return 'ИИ думает…'
})

function parseToolResult(content) {
  try {
    const value = JSON.parse(content)
    // Выжимка сигналов — готовый текст: показываем ровно то, что прочитал ИИ.
    if (typeof value?.digest === 'string') return { text: value.digest, failed: false }
    return { text: JSON.stringify(value, null, 2), failed: value && value.ok === false }
  } catch {
    return { text: content, failed: false }
  }
}

function toolError(content) {
  try {
    const value = JSON.parse(content)
    return String(value?.error || content)
  } catch {
    return content
  }
}

/** Ход за ходом: реплики пользователя, ответы ИИ с шагами чтения, вопросами, планом и карточками действий. */
const items = computed(() => {
  if (!chat.value) return []
  const results = new Map()
  for (const m of data.value.messages) if (m.role === 'tool') results.set(m.tool_call_id, m.content)
  const byCall = new Map(data.value.actions.map((a) => [a.tool_call_id, a]))
  // Правки на доске — строкой после сообщения, на котором их сделали.
  const edits = new Map()
  for (const a of data.value.actions) {
    if (a.tool === 'board_edit') edits.set(a.message_id, [...(edits.get(a.message_id) || []), a])
  }
  const out = []
  for (const m of data.value.messages) {
    if (m.role === 'user') out.push({ key: `m${m.id}`, role: 'user', content: m.content, refs: m.refs || [] })
    if (m.role === 'error') out.push({ key: `m${m.id}`, role: 'error', content: m.content })
    if (m.role === 'assistant') {
      const item = assistantItem(m, results, byCall)
      if (item) out.push(item)
    }
    for (const e of edits.get(m.id) || []) out.push({ key: `e${e.id}`, role: 'note', content: e.summary })
  }
  return out
})

/** Ответ ИИ: текст, шаги чтения, вопросы, план, таблицы и карточки действий (решаемое на доске — плашкой). */
function assistantItem(m, results, byCall) {
  const reads = []
  const asks = []
  const writes = []
  for (const call of m.tool_calls || []) {
    const raw = results.get(call.id)
    // Вопросы без карточки (на всё уже ответили или ошибка в аргументах) — обычный шаг с ответом.
    if (call.kind === 'ask' && byCall.has(call.id)) {
      const action = byCall.get(call.id)
      asks.push({ ...action, key: `a${action.id}`, plaque: plaqueText(action) })
    } else if (call.write) {
      const action = byCall.get(call.id)
      if (action && onBoard.value.has(action.id)) {
        writes.push({ ...action, key: `a${action.id}`, plaque: plaqueText(action) })
      } else if (action) {
        const { reason: _reason, plan_step: _step, ...argsShown } = action.args || {}
        writes.push({ ...action, key: `a${action.id}`, argsShown, picks: pickRows(action.items) })
      } else {
        // Подготовка не прошла (ошибка в аргументах) — ответ инструмента вместо карточки.
        writes.push({
          key: `c${call.id}`,
          label: call.label,
          status: raw != null ? 'error' : 'approved',
          summary: 'Действие не создано',
          error: raw != null ? toolError(raw) : '',
          argsShown: call.args,
        })
      }
    } else {
      const parsed = raw != null ? parseToolResult(raw) : null
      reads.push({ id: call.id, label: call.label, args: call.args, result: parsed?.text ?? null, failed: parsed?.failed })
    }
  }
  if (!m.content && !reads.length && !asks.length && !writes.length) return null
  return { key: `m${m.id}`, role: 'assistant', content: m.content, model: m.model || '', reads, asks, writes }
}

/** Плашка вместо таблицы этапа или карточки создания, которые решаются на доске; '' — карточка в чате. */
function plaqueText(action) {
  if (!onBoard.value.has(action.id)) return ''
  if (action.tool === 'create_campaign') {
    const name = action.args?.spec?.name ? ` «${action.args.spec.name}»` : ''
    const texts = {
      pending: `Черновик${name} на доске ждёт решения`,
      approved: `Черновик${name} создаётся…`,
      done: `Черновик${name} создан в Директе`,
      error: `Черновик${name} не создан: ${action.error || 'ошибка'}`,
      rejected: `Создание черновика${name} отклонено`,
    }
    return texts[action.status] || action.status
  }
  const stage = action.args?.stage
  const label = build.value.stages.find((s) => s.key === stage)?.label || stage
  if (action.status === 'pending') return `Этап «${label}» на доске ждёт решения`
  if (action.status === 'done') {
    const chosen = (action.result?.approved_rows || []).length
    return `Этап «${label}» согласован на доске: ${chosen} из ${(action.args?.rows || []).length}`
  }
  return `Этап «${label}» отклонён`
}

/** Строки с галочками карточки действия: «вся группа» — галочка раздела, остальное — строки. */
function pickRows(items) {
  if (!items?.length) return null
  const groups = {}
  const rows = []
  for (const p of items) {
    if (p.kind === 'group') groups[p.section] = p.id
    else rows.push({ id: p.id, section: p.section, kind: p.kind, cells: [p.text, p.note].slice(0, items.some((i) => i.note) ? 2 : 1) })
  }
  return { groups, rows, columns: items.some((i) => i.note) ? ['Строка', 'Что меняется'] : ['Строка'] }
}

function offRows(action) {
  const off = new Set(excluded[action.id] || [])
  if (!action.picks || !off.size) return []
  return action.picks.rows.filter((r) => off.has(r.id) || (action.picks.groups[r.section] != null && off.has(action.picks.groups[r.section])))
}

function offCount(action) {
  return offRows(action).length
}

function allOff(action) {
  return !!action.picks && offCount(action) === action.picks.rows.length
}

/** id снятых строк ждущих действий — для «Выполнить» и «Выполнить все». */
function excludeFor(action) {
  return action.picks ? (excluded[action.id] || []) : []
}

/** Действия ИИ по шагам плана: с номером шага, после этого плана и до следующего. */
function planProgress(plan) {
  const next = plans.value.find((p) => p.id > plan.id)
  const out = {}
  for (const a of actions.value) {
    if (a.kind !== 'write' || a.plan_step == null || a.id < plan.id || (next && a.id > next.id)) continue
    ;(out[a.plan_step] ||= []).push({
      id: a.id,
      label: a.label,
      status: STATUS_LABELS[a.status] || a.status,
      tone: STATUS_TONES[a.status] || '',
    })
  }
  return out
}

/** Из плашки в чате — к разделу доски, где это решается (на узком экране — вкладка «Доска»). */
async function toBoard(actionId) {
  tab.value = 'board'
  await nextTick()
  board.value?.showAction(actionId)
}

/** Правки на доске: сервер возвращает чат целиком; false — правка не прошла (тост с причиной). */
async function editBoard(ops) {
  const id = chatId.value
  try {
    const res = await aiApi.board(id, ops)
    if (chatId.value === id) data.value = res
    return true
  } catch (err) {
    pushToast(errorText(err), 'error')
    if (chatId.value === id) loadChat(id, true)
    return false
  }
}

function hasArgs(args) {
  return args && typeof args === 'object' && Object.keys(args).length > 0
}

function pretty(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function campaignsText(card) {
  const names = card.campaigns.map((c) => `«${c.name}»`).join(', ')
  return card.campaigns_total > card.campaigns.length ? `${names} и др.` : names
}

// --- Загрузка и опрос -------------------------------------------------------------------------

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

function schedulePoll() {
  stopPolling()
  const needs = status.value === 'running' || actions.value.some((a) => a.status === 'approved')
  if (needs && chatId.value) pollTimer = setTimeout(() => loadChat(chatId.value, true), POLL_MS)
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

async function loadChats() {
  chatsError.value = ''
  try {
    const res = props.general ? await aiApi.generalChats() : await aiApi.chats(ws.value.id)
    chats.value = res.items || []
  } catch (err) {
    chatsError.value = errorText(err)
  }
}

function onScroll() {
  const el = scroller.value
  if (el) stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function scrollDown(force = false) {
  await nextTick()
  const el = scroller.value
  if (el && (force || stickToBottom)) el.scrollTop = el.scrollHeight
}

async function loadChat(id, quiet = false) {
  if (!quiet) loadError.value = ''
  const prevStatus = chat.value?.status
  const prevCount = chat.value ? data.value.messages.length + data.value.actions.length : 0
  try {
    const res = await aiApi.chat(id)
    if (chatId.value !== id) return
    data.value = res
    fillModels(res.ai)
    const count = res.messages.length + res.actions.length
    if (!quiet || count !== prevCount) scrollDown(!quiet)
    if (prevStatus && prevStatus !== res.chat.status) loadChats()
    else if (!quiet) {
      const inList = chats.value.find((c) => c.id === id)
      if (inList) Object.assign(inList, res.chat)
    }
  } catch (err) {
    if (chatId.value !== id) return
    if (!quiet) {
      data.value = null
      loadError.value = errorText(err)
    }
  } finally {
    if (chatId.value === id) schedulePoll()
  }
}

// --- @-упоминания проектов --------------------------------------------------------------------

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// @Название в начале строки или после пробела: «me@site.ru» не упоминание.
const hasMention = (message, name) => new RegExp(`(^|\\s)@${escapeRe(name)}`).test(message)

/** Проекты, на которые ссылается текст сообщения. */
function usedMentions(message) {
  const found = new Map()
  for (const m of mentions.value) if (hasMention(message, m.name)) found.set(m.id, m)
  const covered = new Set([...found.values()].map((m) => m.name))
  for (const w of otherProjects.value) {
    if (!covered.has(w.name) && hasMention(message, w.name)) found.set(w.id, { id: w.id, name: w.name })
  }
  return [...found.values()]
}

/** Текст сообщения кусками: @Проект — ссылкой-чипом на проект, остальное — как есть. */
function mentionParts(item) {
  const refs = item.refs || []
  if (!refs.length) return [{ text: item.content }]
  const names = [...new Set(refs.map((r) => r.name))].sort((a, b) => b.length - a.length)
  const re = new RegExp(`(@(?:${names.map(escapeRe).join('|')}))`)
  return item.content
    .split(re)
    .filter(Boolean)
    .map((text) => {
      const ref = text.startsWith('@') ? refs.find((r) => `@${r.name}` === text) : null
      return ref ? { text, ref } : { text }
    })
}

function closePicker() {
  picker.open = false
}

/** Список проектов, пока перед курсором стоит «@запрос» (в начале строки или после пробела). */
function updatePicker() {
  const el = input.value
  if (!el || !otherProjects.value.length) return closePicker()
  const caret = el.selectionStart ?? 0
  const before = el.value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1 || (at > 0 && !/\s/.test(before[at - 1]))) return closePicker()
  const query = before.slice(at + 1)
  if (query.length > 60 || /^\s|\n/.test(query)) return closePicker()
  const q = query.toLowerCase()
  const items = otherProjects.value.filter((w) => `${w.name} ${w.site || ''}`.toLowerCase().includes(q))
  if (!items.length) return closePicker()
  Object.assign(picker, { open: true, items, start: at, end: caret, index: Math.min(picker.index, items.length - 1) })
}

function pickProject(w) {
  const el = input.value
  const insert = `@${w.name} `
  draft.value = draft.value.slice(0, picker.start) + insert + draft.value.slice(picker.end)
  if (!mentions.value.some((m) => m.id === w.id)) mentions.value.push({ id: w.id, name: w.name })
  const caret = picker.start + insert.length
  picker.index = 0
  closePicker()
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(caret, caret)
  })
}

/** Кнопка «@»: ставит @ в курсор и открывает список — для тех, кто не знает про клавишу, и для телефона. */
function insertAt() {
  const el = input.value
  if (!el) return
  const pos = el.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, pos)
  const lead = before && !/\s$/.test(before) ? ' ' : ''
  draft.value = `${before}${lead}@${draft.value.slice(el.selectionEnd ?? pos)}`
  const caret = pos + lead.length + 1
  nextTick(() => {
    el.focus()
    el.setSelectionRange(caret, caret)
    updatePicker()
  })
}

function onKeydown(e) {
  if (picker.open) {
    const last = picker.items.length - 1
    if (e.key === 'ArrowDown') picker.index = picker.index >= last ? 0 : picker.index + 1
    else if (e.key === 'ArrowUp') picker.index = picker.index <= 0 ? last : picker.index - 1
    else if (e.key === 'Enter' || e.key === 'Tab') pickProject(picker.items[picker.index])
    else if (e.key === 'Escape') closePicker()
    else return
    e.preventDefault()
    return
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault()
    send()
  }
}

watch(
  () => picker.index,
  () => nextTick(() => document.querySelector('.mention-pop__item.is-active')?.scrollIntoView({ block: 'nearest' })),
)

// --- Действия пользователя --------------------------------------------------------------------

async function send(text) {
  const message = (text ?? draft.value).trim()
  if (!message || sending.value || status.value === 'running') return
  sending.value = true
  try {
    let id = chatId.value
    if (!id) {
      const created = props.general
        ? await aiApi.createGeneralChat('', pickedClass.value)
        : await aiApi.createChat(ws.value.id, '', pickedClass.value)
      id = created.id
      chats.value = [created, ...chats.value]
      await router.replace({ path: `${basePath.value}/${id}` })
    }
    // Подсказки-стартеры — готовый текст без упоминаний.
    await aiApi.send(id, message, text == null ? usedMentions(message).map((m) => m.id) : [])
    if (text == null || text === draft.value) {
      draft.value = ''
      mentions.value = []
    }
    stickToBottom = true
    await loadChat(id)
    loadChats()
  } catch (err) {
    if (err?.code === 'ai-not-configured') aiConfigured.value = false
    pushToast(errorText(err), 'error')
  } finally {
    sending.value = false
    nextTick(() => input.value?.focus())
  }
}

async function retryTurn() {
  const id = chatId.value
  if (!id || !canRetry.value) return
  retrying.value = true
  try {
    await aiApi.retry(id)
    stickToBottom = true
    await loadChat(id)
    loadChats()
  } catch (err) {
    if (err?.code === 'ai-not-configured') aiConfigured.value = false
    pushToast(errorText(err), 'error')
    loadChat(id, true)
  } finally {
    retrying.value = false
  }
}

/**
 * «Разбор»: в пустом чате — здесь же, иначе в новом. card — карточка сигнала (её запрос и название)
 * или эксперимента, по которому пора подвести итоги.
 */
async function startReview(card = null) {
  if (!canReview.value) return
  reviewing.value = true
  try {
    const payload = card && !card.experiment_id ? { prompt: card.prompt, title: card.title } : {}
    if (chat.value && !items.value.length) payload.chat_id = chat.value.id
    const res = card?.experiment_id
      ? await aiApi.reviewExperiment(ws.value.id, card.experiment_id, payload)
      : await aiApi.review(ws.value.id, payload)
    const id = res.chat.id
    chats.value = [res.chat, ...chats.value.filter((c) => c.id !== id)]
    stickToBottom = true
    if (id !== chatId.value) {
      const go = chatId.value ? router.push : router.replace
      await go({ path: `${basePath.value}/${id}` })
    }
    await loadChat(id)
  } catch (err) {
    if (err?.code === 'ai-not-configured') aiConfigured.value = false
    pushToast(errorText(err), 'error')
  } finally {
    reviewing.value = false
  }
}

async function decide(action, approve) {
  deciding.value = true
  const rememberIt = approve && !!remember[action.id]
  try {
    await aiApi.decide(action.id, approve, rememberIt, approve ? excludeFor(action) : [])
    if (rememberIt) reloadWs()
    await loadChat(chatId.value)
  } catch (err) {
    pushToast(errorText(err), 'error')
    loadChat(chatId.value, true)
  } finally {
    deciding.value = false
  }
}

/** Ответ в карточке вопросов или плана; когда решать больше нечего, ИИ продолжает. */
async function respond(request) {
  deciding.value = true
  try {
    await request()
    stickToBottom = true
    await loadChat(chatId.value)
  } catch (err) {
    pushToast(errorText(err), 'error')
    loadChat(chatId.value, true)
  } finally {
    deciding.value = false
  }
}

async function decideAll(approve) {
  if (!chatId.value) return
  deciding.value = true
  try {
    const exclude = {}
    if (approve) {
      for (const item of items.value) {
        for (const a of item.writes || []) if (a.status === 'pending' && excludeFor(a).length) exclude[a.id] = excludeFor(a)
      }
    }
    // Отложенные «Позже» не трогаем: решаем по одному только те, что на виду.
    if (laterCount.value) {
      for (const a of activeWrites.value) await aiApi.decide(a.id, approve, false, approve ? exclude[a.id] || [] : [])
    } else {
      await aiApi.decideAll(chatId.value, approve, exclude)
    }
    await loadChat(chatId.value)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    deciding.value = false
  }
}

async function toggleFullAccess(event) {
  const on = event.target.checked
  if (
    on &&
    !await askConfirm(
      'Включить полный доступ? ИИ сможет менять бюджеты и ставки, создавать, копировать и удалять кампании без подтверждения.',
    )
  ) {
    event.target.checked = false
    return
  }
  accessBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { ai_access: { full_access: on } })
    patch(updated)
    pushToast(on ? 'Полный доступ включён' : 'Полный доступ выключен', on ? 'warn' : 'ok')
  } catch (err) {
    event.target.checked = !on
    pushToast(errorText(err), 'error')
  } finally {
    accessBusy.value = false
  }
}

function newChat() {
  stopPolling()
  data.value = null
  draft.value = ''
  router.push(basePath.value)
  nextTick(() => input.value?.focus())
}

async function renameChat() {
  const title = await askText('Название чата', { title: 'Переименовать чат', value: chat.value.title, required: true })
  if (title == null || !title.trim() || title.trim() === chat.value.title) return
  try {
    const updated = await aiApi.renameChat(chat.value.id, title.trim())
    data.value = { ...data.value, chat: updated }
    chats.value = chats.value.map((c) => (c.id === updated.id ? updated : c))
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function deleteChat() {
  if (!await askConfirm(`Удалить чат «${chat.value.title}»? Действия, уже выполненные в Директе, останутся в журнале.`)) return
  const id = chat.value.id
  try {
    await aiApi.deleteChat(id)
    chats.value = chats.value.filter((c) => c.id !== id)
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

// Счётчик «Ждёт решения» в шапке: в чате появились карточки без решения или решение принято.
watch(
  () => (data.value?.actions || []).filter((a) => a.status === 'pending').length,
  () => refreshPending(),
)

// Пустой открытый чат или новый: тогда и считаем сигналы — один раз за визит.
const showIntro = computed(() => !loadError.value && !items.value.length && (!chatId.value || !!chat.value))
watch(
  showIntro,
  (show) => {
    if (show && !props.general && !isFirst.value && !signals.value && !signalsError.value) loadSignals()
  },
  { immediate: true },
)

watch(chatId, (id) => {
  stopPolling()
  chatsOpen.value = false
  tab.value = 'chat'
  loadError.value = ''
  if (id) {
    stickToBottom = true
    loadChat(id)
  }
})

onMounted(async () => {
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

onBeforeUnmount(stopPolling)
</script>
