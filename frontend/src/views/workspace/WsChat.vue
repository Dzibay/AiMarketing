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
    <aside class="chat__side" :class="{ 'is-open': chatsOpen }" :aria-label="general ? 'Общие чаты' : 'Чаты проекта'">
      <div class="chat__side-head">
        <strong>{{ general ? 'Общие чаты' : 'Чаты проекта' }}</strong>
        <button class="chat__side-x" type="button" aria-label="Закрыть список чатов" @click="chatsOpen = false">
          <X aria-hidden="true" />
        </button>
      </div>
      <button class="btn btn--accent chat__new" type="button" :disabled="sending" @click="newChat">
        <Plus aria-hidden="true" />
        Новый чат
      </button>
      <p v-if="chatsError" class="hint">{{ chatsError }}</p>
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
            <span v-else-if="c.pending_actions" class="chat__item-wait">ждёт решения: {{ c.pending_actions }}</span>
            <span v-else-if="c.status === 'error'" class="tone-bad">ошибка</span>
            {{ formatDateTime(c.updated_at) }}
          </small>
        </RouterLink>
      </nav>
    </aside>
    <div v-if="chatsOpen" class="chat-backdrop" @click="chatsOpen = false" />

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

      <div v-else ref="scroller" class="chat__messages" @scroll="onScroll">
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

      <div class="chat__foot">
        <div v-if="activeWrites.length > 1" class="chat__pending">
          <span>
            Ждут решения: {{ activeWrites.length }}<template v-if="laterCount">, отложено: {{ laterCount }}</template>
          </span>
          <button class="btn btn--accent btn--sm" type="button" :disabled="deciding" @click="decideAll(true)">Выполнить все</button>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="deciding" @click="decideAll(false)">Отклонить все</button>
        </div>

        <form class="composer" :class="{ 'composer--big': showIntro, 'is-off': inputOff }" @submit.prevent="send()">
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
              <strong>{{ niceQuotes(w.name) }}</strong>
              <small v-if="w.site">{{ w.site }}</small>
            </button>
          </div>
          <textarea
            ref="input"
            v-model="draft"
            class="composer__input"
            rows="1"
            maxlength="8000"
            aria-label="Сообщение ИИ"
            :aria-controls="picker.open ? 'mention-list' : null"
            :aria-activedescendant="picker.open && picker.items[picker.index] ? `mention-${picker.items[picker.index].id}` : null"
            :disabled="inputOff"
            :placeholder="placeholder"
            @input="updatePicker"
            @click="updatePicker"
            @keyup.left="updatePicker"
            @keyup.right="updatePicker"
            @blur="closePicker"
            @keydown="onKeydown"
          />
          <div class="composer__row">
            <button
              v-if="otherProjects.length"
              class="composer__icon"
              type="button"
              v-tip="general ? 'Указать проект' : 'Сослаться на другой проект'"
              :aria-label="general ? 'Указать проект' : 'Сослаться на другой проект'"
              :disabled="inputOff"
              @mousedown.prevent
              @click="insertAt"
            >
              <AtSign aria-hidden="true" />
            </button>
            <UiSelect
              v-if="modelItems.length > 1"
              class="composer__model"
              size="sm"
              :model-value="selectedClass"
              :options="modelOptions"
              :disabled="modelBusy || inputOff"
              aria-label="Модель ответа"
              v-tip="'Модель ответа'"
              @change="chooseModel"
            />
            <span class="composer__tip">{{ composerTip }}</span>
            <button
              class="composer__send"
              type="submit"
              :disabled="!canSend"
              :aria-label="busy ? 'ИИ отвечает' : 'Отправить'"
              v-tip="busy ? 'ИИ отвечает' : 'Отправить'"
            >
              <span v-if="busy" class="spinner" aria-hidden="true" />
              <ArrowUp v-else aria-hidden="true" />
            </button>
          </div>
        </form>
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
import {
  ArrowUp,
  AtSign,
  Check,
  Clock,
  History,
  Pencil,
  PencilLine,
  Plus,
  RotateCcw,
  ScanSearch,
  Sparkles,
  Trash2,
  X,
} from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { aiApi, errorText, wsApi } from '../../api/web'
import { takeHomeQuestion } from '../../composables/useHomeAsk'
import { refreshPending } from '../../composables/usePending'
import { pushToast } from '../../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'
import { formatDateTime, niceQuotes, pct } from '../../utils/format'
import { renderMarkdown } from '../../utils/markdown'
import AiChanges from './AiChanges.vue'
import AiCheckTable from './AiCheckTable.vue'
import AiItemsCard from './AiItemsCard.vue'
import AiPlanCard from './AiPlanCard.vue'
import AiQuestionsCard from './AiQuestionsCard.vue'
import CampaignBoard from './CampaignBoard.vue'
import { useWs } from './context'
import { useExpert } from '../../composables/useExpert'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiNotice from '../../components/ui/UiNotice.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiStatus from '../../components/ui/UiStatus.vue'

const POLL_MS = 1500
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
// Подсказка к классу модели в списке «Модель ответа».
const MODEL_HINTS = {
  1: 'Быстрые ответы, расходует меньше лимита',
  2: 'Для большинства задач',
  3: 'Думает дольше, расходует лимит быстрее',
}
const FULL_ACCESS_CONFIRM =
  'Включить полный доступ? ИИ сможет менять бюджеты и ставки, создавать, копировать и удалять кампании без подтверждения.'
// Поле ввода растёт с текстом до ~8 строк, дальше — своя прокрутка.
const INPUT_MAX = 200
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
const STATUS_UI = { pending: 'wait', approved: 'info', done: 'ok', rejected: 'off', error: 'bad' }
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
const root = ref(null)
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
// Список чатов выезжает слева по кнопке «Все чаты»; с доской на узком экране чат и доска — вкладками.
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

const modelOptions = computed(() =>
  modelItems.value.map((m) => ({ value: m.id, label: m.name, hint: MODEL_HINTS[m.dots] || '' })),
)

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
      ? 'Ответьте в карточке; сообщение отклонит предложения'
      : 'Ответьте в карточке или напишите своими словами'
  }
  if (pendingWrites.value.length) return 'Новое сообщение отклонит ждущие предложения'
  return showIntro.value ? 'Спросите или поручите задачу…' : 'Уточните или попросите что-то ещё…'
})
const composerTip = computed(
  () => `Enter — отправить · Shift+Enter — перенос${otherProjects.value.length ? ' · @ — проект' : ''}`,
)
// Пока ИИ отвечает, писать нельзя: остановить ход нечем, новое сообщение ушло бы в середину ответа.
const inputOff = computed(() => status.value === 'running' || !aiConfigured.value)
const busy = computed(() => sending.value || status.value === 'running')
const canSend = computed(
  () => !!draft.value.trim() && !sending.value && status.value !== 'running' && aiConfigured.value,
)
const canReview = computed(
  () => !reviewing.value && !sending.value && status.value !== 'running' && aiConfigured.value,
)
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
    if (m.role === 'user') {
      out.push({ key: `m${m.id}`, role: 'user', content: m.content, refs: m.refs || [], created: m.created_at || '' })
    }
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
  return {
    key: `m${m.id}`,
    role: 'assistant',
    content: m.content,
    model: m.model || '',
    created: m.created_at || '',
    reads,
    asks,
    writes,
  }
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

/** Что прокручивается: лента сама — рядом с доской, иначе вся страница (поле ввода прилипает к низу окна). */
function scrollBox() {
  const el = scroller.value
  if (el && getComputedStyle(el).overflowY !== 'visible') return el
  return document.scrollingElement
}

function onScroll() {
  const box = scrollBox()
  if (box) stickToBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120
}

async function scrollDown(force = false) {
  await nextTick()
  const box = scrollBox()
  if (box && (force || stickToBottom)) box.scrollTop = box.scrollHeight
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

function autosize() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  // Пустое поле меряем по подсказке: длинная подсказка на узком экране иначе обрезается на первой строке.
  const empty = !el.value
  if (empty) el.value = el.placeholder
  const height = el.scrollHeight
  if (empty) el.value = ''
  el.style.height = `${Math.min(height, INPUT_MAX)}px`
  el.style.overflowY = height > INPUT_MAX ? 'auto' : 'hidden'
}

function onResize() {
  measureTop()
  autosize()
}

function onWindowKey(event) {
  if (event.key === 'Escape' && chatsOpen.value) chatsOpen.value = false
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

function newChat() {
  stopPolling()
  chatsOpen.value = false
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

watch(chatsOpen, (open) => {
  if (open) nextTick(() => root.value?.querySelector('.chat__side-x')?.focus())
})

watch([draft, showIntro, placeholder], () => nextTick(autosize))

onMounted(async () => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onWindowKey)
  const page = root.value?.closest('.dpage')
  if (page && typeof ResizeObserver !== 'undefined') {
    topObserver = new ResizeObserver(measureTopLater)
    topObserver.observe(page)
  }
  measureTop()
  autosize()
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
  stopPolling()
  topObserver?.disconnect()
  cancelAnimationFrame(topFrame)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onWindowKey)
})
</script>
