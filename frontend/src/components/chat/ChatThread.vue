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
        <div v-if="item.role === 'user'" class="msg msg--user">
          <div class="msg__bubble"><template v-for="(part, i) in mentionParts(item)" :key="i"><RouterLink v-if="part.ref" class="mention" :to="`/w/${part.ref.id}/summary`">{{ part.text }}</RouterLink><template v-else>{{ part.text }}</template></template></div>
          <time v-if="item.created" class="msg__time" :datetime="item.created">{{ formatDateTime(item.created) }}</time>
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

        <div v-else class="msg msg--ai">
          <div v-if="item.content" class="msg__text md" v-html="renderMarkdown(item.content)" />

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
              <button class="ai-check__link" type="button" @click="emit('to-board', a.id)">К доске</button>
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
              <button class="ai-check__link" type="button" @click="emit('to-board', a.id)">К доске</button>
            </div>
            <div v-else-if="isLater(a)" class="action-card action-card--later" :data-action="a.id">
              <div class="action-card__head">
                <strong class="action-card__title">{{ a.label }}</strong>
                <UiStatus tone="off" :icon="Clock" label="отложено" />
              </div>
              <p v-if="projectName(a)" class="action-card__meta"><span>{{ projectName(a) }}</span></p>
              <p class="action-card__summary">{{ a.summary }}</p>
              <p class="hint">
                Ничего не изменилось. Предложение ждёт на главной в «Ждут вашего решения»; новое сообщение в этом чате
                его отклонит.
              </p>
              <div class="action-card__buttons">
                <button class="btn btn--ghost btn--sm" type="button" @click="setLater(a, false)">Решить сейчас</button>
              </div>
            </div>
            <div v-else class="action-card" :class="`is-${a.status}`" :data-action="a.id">
              <div class="action-card__head">
                <span v-if="a.status === 'pending'" class="action-card__tag">
                  <Sparkles aria-hidden="true" />
                  Предлагаю
                </span>
                <strong class="action-card__title">{{ a.label }}</strong>
                <UiStatus v-if="a.status !== 'pending'" :tone="STATUS_UI[a.status] || 'off'" :label="STATUS_LABELS[a.status] || a.status" />
              </div>
              <p v-if="projectName(a) || a.plan_step || a.auto" class="action-card__meta">
                <span v-if="projectName(a)">{{ projectName(a) }}</span>
                <span v-if="a.plan_step">шаг {{ a.plan_step }} плана</span>
                <span v-if="a.auto">без согласования</span>
              </p>
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
              <div v-if="a.status === 'pending' && a.id" class="action-card__buttons">
                <button class="btn btn--accent" type="button" :disabled="deciding || allOff(a)" @click="decide(a, true)">
                  <Check aria-hidden="true" />
                  {{ offCount(a) ? `Выполнить без снятых (${offCount(a)})` : 'Выполнить' }}
                </button>
                <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decide(a, false)">Отклонить</button>
                <button
                  class="btn btn--ghost"
                  type="button"
                  :disabled="deciding"
                  v-tip="'Свернуть карточку: решить можно позже здесь или на главной'"
                  @click="setLater(a, true)"
                >
                  Позже
                </button>
                <span v-if="allOff(a)" class="hint">Сняты все строки — отклоните действие</span>
                <UiCheckbox
                  v-if="!general && a.categories?.length && !a.categories.some((c) => c.id === 'destructive')"
                  v-model="remember[a.id]"
                  class="check-row"
                >
                  Больше не спрашивать для «{{ a.categories.map((c) => c.label).join('», «') }}»
                </UiCheckbox>
                <p v-if="!expert" class="action-card__note">
                  Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».
                </p>
              </div>
            </div>
          </template>

          <p v-if="item.content && (item.created || (expert && item.model))" class="msg__meta">
            <span v-if="expert && item.model">{{ item.model }}</span>
            <time v-if="item.created" :datetime="item.created">{{ formatDateTime(item.created) }}</time>
          </p>
        </div>
      </template>

      <div v-if="status === 'running'" class="msg msg--ai msg--thinking" role="status">
        <span class="spinner" aria-hidden="true" />
        {{ thinkingText }}
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
import { Check, Clock, PencilLine, RotateCcw, Sparkles } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import { aiApi } from '../../api/web'
import { STATUS_LABELS, mentionParts, useChat } from '../../composables/useChat'
import { useExpert } from '../../composables/useExpert'
import { formatDateTime } from '../../utils/format'
import { renderMarkdown } from '../../utils/markdown'
import AiChanges from '../../views/workspace/AiChanges.vue'
import AiCheckTable from '../../views/workspace/AiCheckTable.vue'
import AiItemsCard from '../../views/workspace/AiItemsCard.vue'
import AiPlanCard from '../../views/workspace/AiPlanCard.vue'
import AiQuestionsCard from '../../views/workspace/AiQuestionsCard.vue'
import UiCheckbox from '../ui/UiCheckbox.vue'
import UiNotice from '../ui/UiNotice.vue'
import UiStatus from '../ui/UiStatus.vue'

/** Лента чата: реплики, ответы ИИ с шагами чтения, вопросы, план и карточки действий на согласование. */
const STATUS_UI = { pending: 'wait', approved: 'info', done: 'ok', rejected: 'off', error: 'bad' }

// to-board — показать на доске раздел, где решается действие (id действия).
const emit = defineEmits(['to-board'])

const {
  general,
  ws,
  chatId,
  chat,
  status,
  loadError,
  items,
  thinkingText,
  errorInFeed,
  lastMessageKey,
  canRetry,
  retryTurn,
  caps,
  remember,
  excluded,
  deciding,
  isLater,
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
const { expert } = useExpert()

function hasArgs(args) {
  return args && typeof args === 'object' && Object.keys(args).length > 0
}

function pretty(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
</script>
