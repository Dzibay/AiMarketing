<template>
  <div class="ws-toolbar">
    <div class="seg" role="group" aria-label="Период загрузки из Директа">
      <button
        v-for="d in PERIODS"
        :key="d"
        type="button"
        :class="{ active: days === d }"
        :disabled="!!busy"
        @click="pickDays(d)"
      >
        {{ d }} дней
      </button>
    </div>
    <p class="sync-status">
      <template v-if="busyText">{{ busyText }}</template>
      <template v-else-if="data?.synced_at">
        Запросы за {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} · загружены
        {{ formatDateTime(data.synced_at) }}
      </template>
      <template v-else-if="data">Запросы ещё не загружали</template>
    </p>
    <div class="row-actions">
      <button
        class="btn btn--ghost"
        type="button"
        :disabled="!!busy"
        :title="`Загрузить поисковые запросы за последние ${days} дней`"
        @click="sync"
      >
        {{ busy === 'sync' ? 'Загружаем…' : 'Обновить из Директа' }}
      </button>
      <button
        class="btn btn--accent"
        type="button"
        :disabled="!!busy || !canClassify"
        :title="counts.new ? 'ИИ разберёт новые запросы и предложит минус-фразы' : 'Разметить заново неподтверждённые'"
        @click="classify"
      >
        {{ busy === 'classify' ? 'Размечаем…' : counts.new || !canClassify ? 'Разметить с ИИ' : 'Переразметить' }}
      </button>
    </div>
  </div>

  <p v-if="!briefFilled" class="notice notice--warn">
    Бриф клиента не заполнен — ИИ хуже понимает, что вы продаёте, и чаще ошибается.
    <RouterLink :to="`/w/${ws.id}/settings#brief`">Заполнить бриф</RouterLink> — черновик можно собрать по сайту.
  </p>

  <div v-if="data" class="stats-grid">
    <div class="stat-card">
      <small>Новые запросы</small>
      <strong>{{ num(counts.new) }}</strong>
      <span class="cell-note">расход {{ rubles(data.new_cost_rub) }}</span>
    </div>
    <div class="stat-card">
      <small>Предложено в минус</small>
      <strong :class="{ 'tone-bad': counts.negative }">{{ num(counts.negative) }}</strong>
      <span class="cell-note">слили {{ rubles(data.negative_cost_rub) }} за период</span>
    </div>
    <div class="stat-card">
      <small>Предложено целевыми</small>
      <strong>{{ num(counts.target) }}</strong>
      <span class="cell-note">ждут подтверждения</span>
    </div>
    <div class="stat-card">
      <small>Решено</small>
      <strong>{{ num(counts.done) }}</strong>
      <span class="cell-note">в памяти — ИИ их не спрашивает</span>
    </div>
  </div>

  <div class="queries-tabs">
    <nav class="seg seg--tabs" aria-label="Очередь запросов">
      <button v-for="t in MAIN_TABS" :key="t.key" type="button" :class="{ active: tab === t.key }" @click="setTab(t.key)">
        {{ t.label }}<template v-if="data"> · {{ num(counts[t.key]) }}</template>
      </button>
    </nav>
    <nav class="queries-tabs__extra" aria-label="Разобранные запросы">
      <button
        v-for="t in EXTRA_TABS"
        :key="t.key"
        type="button"
        :class="{ active: tab === t.key }"
        :aria-current="tab === t.key ? 'page' : undefined"
        @click="setTab(t.key)"
      >
        {{ t.label }}
      </button>
    </nav>
  </div>

  <div v-if="result" class="notice queries-result" :class="{ 'notice--warn': result.status !== 'ok' }">
    <div class="queries-result__head">
      <strong>{{ resultTitle }}</strong>
      <div class="row-actions">
        <button v-if="result.log_id" class="btn btn--ghost" type="button" @click="undoId = result.log_id">
          Откатить
        </button>
        <button class="btn btn--ghost" type="button" @click="result = null">Скрыть</button>
      </div>
    </div>
    <ul v-if="result.problems.length">
      <li v-for="p in result.problems.slice(0, 20)" :key="p.query_norm">
        «{{ p.query }}»: {{ p.message || STATUS_TEXT[p.status] }}
        <template v-if="p.conflicts?.length">
          — {{ p.conflicts.map((k) => `«${k.keyword}»`).join(', ') }}
        </template>
      </li>
    </ul>
    <button v-if="forceable.length" class="btn btn--ghost" type="button" :disabled="!!busy" @click="applyForce">
      Всё равно добавить {{ forceable.length }}
      {{ plural(forceable.length, 'минус-фразу', 'минус-фразы', 'минус-фраз') }}
    </button>
  </div>

  <section v-if="tab !== 'memory'" class="panel">
    <div class="filters">
      <div v-field class="field field--wide">
        <label>Поиск</label>
        <input v-model.trim="filters.search" type="search" placeholder="слово из запроса" />
      </div>
      <div v-if="campaigns.length > 1" v-field class="field">
        <label>Кампания</label>
        <select v-model="filters.campaign">
          <option value="">Все кампании</option>
          <option v-for="c in campaigns" :key="c.id" :value="String(c.id)">{{ campaignLabel(c, repeatedCampaigns) }}</option>
        </select>
      </div>
      <div v-field class="field">
        <label>Расход от, ₽</label>
        <input v-model="filters.minCost" type="number" min="0" step="10" placeholder="0" />
      </div>
      <label class="check-row">
        <input v-model="filters.noConv" type="checkbox" />
        Без конверсий
      </label>
    </div>

    <p v-if="loading && !data" class="empty">Загружаем запросы…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить запросы.' }}
      <button class="btn btn--ghost" type="button" @click="load()">Повторить</button>
    </div>
    <div v-else-if="!campaigns.length" class="empty">
      В пространстве нет поисковых кампаний — чистить нечего. Добавьте кампании в
      <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink>.
    </div>
    <div v-else-if="!counts.all && !hasFilters" class="empty">
      Запросов пока нет. Нажмите «Обновить из Директа» — загрузим отчёт по поисковым запросам за {{ days }} дней.
    </div>
    <p v-else-if="!items.length" class="empty">{{ emptyText }}</p>
    <template v-else>
      <div class="table-scroll" :class="{ 'is-loading': loading }">
        <table class="table queries-table">
          <thead>
            <tr>
              <th class="check-col">
                <input
                  type="checkbox"
                  :checked="items.every((q) => selected.has(q.query_norm))"
                  aria-label="Выбрать все"
                  @change="toggleAll($event.target.checked)"
                />
              </th>
              <th>Запрос</th>
              <th class="num">Показы</th>
              <th class="num">Клики</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th>Решение</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="q in items"
              :key="q.query_norm"
              :class="{ 'is-selected': selected.has(q.query_norm) }"
            >
              <td class="check-col">
                <input
                  type="checkbox"
                  :checked="selected.has(q.query_norm)"
                  :aria-label="`Выбрать «${q.query}»`"
                  @change="toggle(q.query_norm, $event.target.checked)"
                />
              </td>
              <td class="query-cell">
                <strong>{{ q.query }}</strong>
                <small class="cell-note">{{ placeText(q) }}</small>
                <small v-if="q.matched_keywords.length" class="cell-note">
                  по фразе {{ q.matched_keywords.map((k) => `«${k}»`).join(', ') }}
                </small>
                <small v-if="q.blocked_by" class="cell-note tone-ok">Уже отсекается: {{ q.blocked_by }}</small>
              </td>
              <td class="num">{{ num(q.impressions) }}</td>
              <td class="num">{{ num(q.clicks) }}</td>
              <td class="num">{{ rubles(q.cost_rub) }}</td>
              <td class="num" :class="{ 'tone-bad': !q.conversions && q.cost_rub > 0 }">{{ num(q.conversions) }}</td>
              <td class="decision">
                <div class="decision__top">
                  <div class="seg seg--sm" role="group" aria-label="Решение">
                    <button
                      type="button"
                      :class="{ active: decisions[q.query_norm].verdict === 'negative' }"
                      @click="setVerdict(q, 'negative')"
                    >
                      Минус
                    </button>
                    <button
                      type="button"
                      :class="{ active: decisions[q.query_norm].verdict === 'target' }"
                      @click="setVerdict(q, 'target')"
                    >
                      Целевой
                    </button>
                  </div>
                  <span v-if="q.label" class="chip" :class="SOURCE_CHIPS[q.label.source] || ''">
                    {{ SOURCES[q.label.source] || q.label.source }}
                  </span>
                  <span v-if="q.label?.confirmed" class="chip chip--ok">
                    {{ q.label.applied_at ? 'применено' : 'решено' }}
                  </span>
                </div>
                <p v-if="q.label?.reason" class="decision__reason">{{ q.label.reason }}</p>
                <template v-if="decisions[q.query_norm].verdict === 'negative'">
                  <div class="decision__neg">
                    <input
                      v-model="decisions[q.query_norm].negative_phrase"
                      type="text"
                      maxlength="4096"
                      aria-label="Минус-фраза"
                      @input="touch(q.query_norm)"
                      @change="scheduleCheck([q.query_norm])"
                    />
                    <select
                      v-model="decisions[q.query_norm].level"
                      aria-label="Куда добавить"
                      @change="touch(q.query_norm); scheduleCheck([q.query_norm])"
                    >
                      <option value="adgroup" :disabled="!q.adgroups.length">на группу</option>
                      <option value="campaign">на кампанию</option>
                    </select>
                  </div>
                  <p v-if="checkOf(q)?.problems.length" class="decision__warn tone-bad">
                    {{ checkOf(q).problems.join('; ') }}
                  </p>
                  <p v-if="checkOf(q)?.total" class="decision__warn tone-warn">
                    Заденет ваши ключи: {{ conflictText(checkOf(q)) }}
                  </p>
                </template>
                <label v-else-if="decisions[q.query_norm].verdict === 'target'" class="check-row decision__kw">
                  <input
                    v-model="decisions[q.query_norm].add_keyword"
                    type="checkbox"
                    :disabled="!q.adgroups.length"
                    @change="touch(q.query_norm)"
                  />
                  <span>{{ q.adgroups.length ? 'Добавить в ключевые фразы' : 'Нет группы — в ключи не добавить' }}</span>
                  <select
                    v-if="decisions[q.query_norm].add_keyword && q.adgroups.length > 1"
                    v-model="decisions[q.query_norm].adgroup_row_id"
                    aria-label="Группа"
                    @change="touch(q.query_norm)"
                  >
                    <option v-for="g in q.adgroups" :key="g.id" :value="g.id">{{ groupLabel(q, g) }}</option>
                  </select>
                </label>
                <p
                  v-if="rowResults[q.query_norm] && rowResults[q.query_norm].status !== 'ok'"
                  class="decision__warn"
                  :class="rowResults[q.query_norm].status === 'error' ? 'tone-bad' : 'tone-warn'"
                >
                  {{ STATUS_TEXT[rowResults[q.query_norm].status] }}:
                  {{ rowResults[q.query_norm].message || 'без подробностей' }}
                </p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="items.length < (data.total || 0)" class="pager">
        <span class="sync-status">Показано {{ items.length }} из {{ data.total }}</span>
        <button class="btn btn--ghost" type="button" :disabled="loading" @click="load({ more: true })">
          {{ loading ? 'Загружаем…' : 'Показать ещё' }}
        </button>
      </div>
    </template>

    <div v-if="selected.size" class="bulk-bar queries-applybar">
      <span>Выбрано: {{ selected.size }}</span>
      <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="markSelected('negative')">В минус</button>
      <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="markSelected('target')">Целевые</button>
      <select aria-label="Уровень минус-фраз" :disabled="!!busy" @change="setLevelSelected($event)">
        <option value="">Уровень минус-фраз…</option>
        <option value="adgroup">Все на группу</option>
        <option value="campaign">Все на кампанию</option>
      </select>
      <button class="btn btn--accent" type="button" :disabled="!!busy" @click="openApply">
        {{ busy === 'apply' ? 'Применяем…' : `Применить (${readyCount})` }}
      </button>
      <button class="btn btn--ghost" type="button" @click="selected = new Set()">Снять выбор</button>
    </div>
  </section>

  <section v-else class="panel">
    <div class="panel__head">
      <div>
        <h2>Память решений</h2>
        <p class="hint">
          Запросы, по которым вы уже решили. Повторно их не размечаем, а ИИ берёт их как примеры. «Забыть» вернёт
          запрос в разбор; минус-фразы в Директе останутся — их можно откатить в журнале.
        </p>
      </div>
    </div>
    <div class="filters">
      <div v-field class="field field--wide">
        <label>Поиск</label>
        <input v-model.trim="mem.search" type="search" placeholder="запрос или минус-фраза" />
      </div>
      <div v-field class="field">
        <label>Решение</label>
        <select v-model="mem.verdict">
          <option value="">Все</option>
          <option value="negative">В минус</option>
          <option value="target">Целевые</option>
        </select>
      </div>
    </div>
    <div v-if="mem.selected.size" class="bulk-bar">
      <span>Выбрано: {{ mem.selected.size }}</span>
      <button class="btn btn--ghost btn--danger" type="button" :disabled="mem.busy" @click="forget">Забыть</button>
      <button class="btn btn--ghost" type="button" @click="mem.selected = new Set()">Снять выбор</button>
    </div>
    <p v-if="mem.loading && !mem.items.length" class="empty">Загружаем…</p>
    <p v-else-if="mem.error" class="form-error">{{ mem.error }}</p>
    <p v-else-if="!mem.items.length" class="empty">
      {{ mem.search || mem.verdict ? 'Ничего не нашлось.' : 'Пока пусто: решения появятся после «Применить».' }}
    </p>
    <div v-else class="table-scroll" :class="{ 'is-loading': mem.loading }">
      <table class="table">
        <thead>
          <tr>
            <th class="check-col">
              <input
                type="checkbox"
                :checked="mem.items.every((m) => mem.selected.has(m.query_norm))"
                aria-label="Выбрать все"
                @change="memToggleAll($event.target.checked)"
              />
            </th>
            <th>Запрос</th>
            <th>Решение</th>
            <th>Минус-фраза</th>
            <th>Когда</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in mem.items" :key="m.query_norm">
            <td class="check-col">
              <input
                type="checkbox"
                :aria-label="`Выбрать «${m.query}»`"
                :checked="mem.selected.has(m.query_norm)"
                @change="memToggle(m.query_norm, $event.target.checked)"
              />
            </td>
            <td>
              {{ m.query }}
              <small v-if="m.reason" class="cell-note">{{ m.reason }}</small>
            </td>
            <td>
              <span class="chip" :class="m.verdict === 'negative' ? 'chip--bad' : 'chip--ok'">
                {{ m.verdict === 'negative' ? 'минус' : 'целевой' }}
              </span>
            </td>
            <td>{{ m.verdict === 'negative' ? `${m.negative_phrase} · ${LEVELS[m.level] || m.level}` : '—' }}</td>
            <td>
              {{ formatDateTime(m.updated_at) }}
              <small v-if="m.applied_at" class="cell-note">
                {{ m.verdict === 'negative' ? 'есть в Директе' : 'добавлено в ключи' }}
              </small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="mem.items.length < mem.total" class="pager">
      <span class="sync-status">Показано {{ mem.items.length }} из {{ mem.total }}</span>
      <button class="btn btn--ghost" type="button" :disabled="mem.loading" @click="loadMemory(true)">Показать ещё</button>
    </div>
  </section>

  <Modal :open="applyModal.open" title="Применить решения" :locked="busy === 'apply'" @close="applyModal.open = false">
    <ul class="apply-summary">
      <li v-if="plan.negative">
        Запросов в минус: <strong>{{ plan.negative }}</strong>
        <span class="cell-note">
          <template v-if="plan.negative > plan.known">на группы — {{ plan.groups }}, на кампании — {{ plan.campaigns }}</template
          ><template v-if="plan.known"
            >{{ plan.negative > plan.known ? '; ещё ' : '' }}{{ plan.known }} уже отсекаются в Директе — их только
            запомним</template
          >
        </span>
      </li>
      <li v-if="plan.target">
        Отметить целевыми: <strong>{{ plan.target }}</strong>
        <span v-if="plan.keywords" class="cell-note">из них в ключевые фразы — {{ plan.keywords }}</span>
      </li>
    </ul>
    <p v-if="applyModal.skipped" class="field-hint">
      Без решения {{ applyModal.skipped }} {{ plural(applyModal.skipped, 'запрос', 'запроса', 'запросов') }} — их
      пропустим.
    </p>
    <p v-if="plan.conflicts" class="notice notice--warn">
      {{ plan.conflicts }} {{ plural(plan.conflicts, 'минус-фраза пересекается', 'минус-фразы пересекаются', 'минус-фраз пересекаются') }}
      с вашими ключевыми фразами — такие пропустим, добавить их можно будет отдельно.
    </p>
    <div v-if="plan.keywords" v-field class="field">
      <label>Ставка для новых ключевых фраз, ₽</label>
      <input v-model="applyModal.bid" type="number" min="0.3" step="0.1" placeholder="по умолчанию Директа" />
      <small class="field-hint">Пусто — подойдёт для автостратегий: ставку назначит Директ.</small>
    </div>
    <p class="field-hint">
      Решения запомним: эти запросы больше не попадут в разбор, а ИИ будет на них учиться. Действие появится в журнале
      — его можно откатить.
    </p>
    <p v-if="busy === 'apply'" class="sync-status">{{ busyText }}</p>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy === 'apply'" @click="applyModal.open = false">
        Отмена
      </button>
      <button class="btn btn--accent" type="button" :disabled="busy === 'apply'" @click="confirmApply">
        {{ busy === 'apply' ? 'Применяем…' : 'Применить' }}
      </button>
    </template>
  </Modal>

  <Modal :open="estimate.open" title="Разметка с ИИ" @close="estimate.open = false">
    <template v-if="estimate.est">
      <ul class="apply-summary">
        <li>
          Запросов к разметке: <strong>{{ num(estimate.est.queries) }}</strong>
          <span v-if="estimate.est.rules" class="cell-note">
            {{ num(estimate.est.rules) }} закроют память решений, бриф и Директ — без ИИ
          </span>
        </li>
        <li>
          Уйдёт в ИИ: <strong>{{ num(estimate.est.ai) }}</strong>
          <span class="cell-note">
            {{ estimate.est.batches }} {{ plural(estimate.est.batches, 'вызов', 'вызова', 'вызовов') }} модели
            {{ estimate.est.model }}
          </span>
        </li>
        <li>
          Токенов ≈ <strong>{{ num(estimate.est.tokens_in + estimate.est.tokens_out) }}</strong>
          <span class="cell-note">вход ≈ {{ num(estimate.est.tokens_in) }}, выход ≈ {{ num(estimate.est.tokens_out) }}</span>
        </li>
        <li>
          Стоимость ≈ <strong>{{ estimate.est.priced ? rubAi(estimate.est.cost_rub) : '—' }}</strong>
          <span v-if="!estimate.est.priced" class="cell-note">
            цены модели не заданы — <RouterLink to="/cabinet/settings#ai">укажите в настройках</RouterLink>, чтобы видеть рубли
          </span>
          <span v-else-if="estimate.est.remaining_rub !== null" class="cell-note">
            в месячном лимите осталось {{ rubAi(estimate.est.remaining_rub) }} из {{ rubAi(estimate.est.limit_rub) }}
          </span>
        </li>
      </ul>
      <p v-if="estimateShort" class="notice notice--warn">
        <template v-if="estimate.est.fits">
          В остаток лимита влезет ≈ {{ num(estimate.est.fits) }} из {{ num(estimate.est.ai) }} — остальные подождут
          следующего месяца или повышения лимита.
        </template>
        <template v-else>
          Остатка лимита не хватит даже на один вызов — ИИ не запустится.
          <template v-if="estimate.est.rules">Разметим только то, что закрывают правила.</template>
        </template>
      </p>
      <p class="field-hint">
        {{
          estimate.est.calibrated
            ? 'Оценка — по прошлым вызовам этой модели.'
            : 'Оценка приблизительная: после первых разметок станет точнее.'
        }}
        Фактический расход — в настройках, «Расход ИИ».
      </p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" @click="estimate.open = false">Отмена</button>
      <button
        class="btn btn--accent"
        type="button"
        :disabled="estimateShort && !estimate.est.fits && !estimate.est.rules"
        @click="confirmClassify"
      >
        {{ estimateShort && !estimate.est.fits ? 'Разметить правилами' : 'Разметить' }}
      </button>
    </template>
  </Modal>

  <UndoDialog :log-id="undoId" @close="undoId = null" @done="onUndone" />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import Modal from '../../components/ui/Modal.vue'
import UndoDialog from '../../components/UndoDialog.vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { campaignLabel, repeatedNames } from '../../utils/campaignNames'
import { formatDate, formatDateTime, num, plural, rubAi, rubles } from '../../utils/format'
import { useWs } from './context'

const TABS = [
  { key: 'new', label: 'Новые' },
  { key: 'negative', label: 'К минусу' },
  { key: 'target', label: 'Целевые' },
  { key: 'done', label: 'Решено' },
  { key: 'all', label: 'Все' },
  { key: 'memory', label: 'Память' },
]
// Каждый день нужны первые три; разобранное и память — второстепенные ссылки справа.
const MAIN_TABS = TABS.slice(0, 3)
const EXTRA_TABS = TABS.slice(3)
const SOURCES = { ai: 'ИИ', memory: 'по памяти', brief: 'по брифу', direct: 'уже в Директе', user: 'вы' }
const SOURCE_CHIPS = { ai: 'chip--src-ai', memory: 'chip--src-rule', brief: 'chip--src-rule', direct: 'chip--src-rule' }
const LEVELS = { adgroup: 'на группу', campaign: 'на кампанию' }
const STATUS_TEXT = { ok: 'Готово', partial: 'Частично', skipped: 'Пропущено', error: 'Ошибка' }
const PAGE = 100
const EMPTY_COUNTS = { new: 0, negative: 0, target: 0, done: 0, all: 0 }

const route = useRoute()
const router = useRouter()
const { ws } = useWs()

const data = ref(null)
const items = ref([])
const loading = ref(false)
const loadError = ref('')
const tab = ref(TABS.some((t) => t.key === route.query.tab) ? route.query.tab : '')
const PERIODS = [7, 14, 30]
const days = ref(14)
// Пока период не выбирали руками, он повторяет уже загруженный — чип не спорит с подписью «Запросы за …».
const daysPicked = ref(false)
const busy = ref('') // sync | classify | apply
const busyText = ref('')
const filters = reactive({ search: '', campaign: '', minCost: '', noConv: false })
const selected = ref(new Set())
// Решения по строкам живут на клиенте до «Применить»: query_norm → { verdict, negative_phrase, level, … }.
const decisions = reactive({})
const checks = reactive({})
const rowResults = reactive({})
const result = ref(null)
const undoId = ref(null)
const applyModal = reactive({ open: false, items: [], skipped: 0, bid: '' })
// Оценка перед массовой разметкой: объём, токены, ₽ и остаток месячного лимита.
const estimate = reactive({ open: false, est: null, payload: null })
const estimateShort = computed(() => {
  const est = estimate.est
  return !!est && est.fits !== null && est.fits < est.ai
})
const mem = reactive({ items: [], total: 0, loading: false, busy: false, error: '', search: '', verdict: '', selected: new Set() })

const counts = computed(() => data.value?.counts || EMPTY_COUNTS)
const campaigns = computed(() => data.value?.campaigns || [])
const repeatedCampaigns = computed(() => repeatedNames(campaigns.value.map((c) => c.name)))

// Группы с одинаковым названием (обычно из копии кампании) различаем кампанией.
function groupLabel(q, g) {
  const name = g.name || `группа ${g.id}`
  if (!repeatedNames(q.adgroups.map((x) => x.name || `группа ${x.id}`)).has(name)) return name
  const camp = campaigns.value.find((c) => c.id === g.campaign_row_id)
  return camp ? `${name} · ${campaignLabel(camp, repeatedCampaigns.value)}` : `${name} · №${g.id}`
}

const hasFilters = computed(() => !!(filters.search || filters.campaign || filters.minCost || filters.noConv))
const canClassify = computed(() => counts.value.new > 0 || counts.value.negative + counts.value.target > 0)
const briefFilled = computed(() => {
  const b = ws.value.brief || {}
  return !!(b.product || b.geo || b.notes || (b.not_ours || []).length)
})
const byNorm = computed(() => Object.fromEntries(items.value.map((q) => [q.query_norm, q])))
const readyCount = computed(() => [...selected.value].filter((n) => decisions[n]?.verdict).length)
const emptyText = computed(() => {
  if (hasFilters.value) return 'По фильтрам ничего не нашлось.'
  if (tab.value === 'new') return 'Новых запросов нет — всё разобрано.'
  if (tab.value === 'negative')
    return counts.value.new ? 'Предложений пока нет — нажмите «Разметить с ИИ».' : 'Предложений в минус нет.'
  if (tab.value === 'target') return 'Целевых предложений нет.'
  if (tab.value === 'done') return 'Решений пока нет.'
  return 'Запросов нет.'
})

const plan = computed(() => {
  const list = applyModal.items
  const neg = list.filter((i) => i.verdict === 'negative')
  // Та же фраза уже отсекает запрос везде — в Директ ничего не уйдёт, решение просто запомним.
  const known = neg.filter((i) => {
    const blocked = byNorm.value[i.query_norm]?.blocked_by
    return blocked && blocked.toLowerCase() === (i.negative_phrase || '').trim().toLowerCase()
  })
  const fresh = neg.filter((i) => !known.includes(i))
  return {
    negative: neg.length,
    known: known.length,
    groups: fresh.filter((i) => i.level === 'adgroup').length,
    campaigns: fresh.filter((i) => i.level === 'campaign').length,
    target: list.length - neg.length,
    keywords: list.filter((i) => i.add_keyword).length,
    conflicts: neg.filter((i) => byNorm.value[i.query_norm] && checkOf(byNorm.value[i.query_norm])?.total).length,
  }
})

const resultTitle = computed(() => {
  const r = result.value
  if (!r) return ''
  const parts = []
  if (r.negative) parts.push(`в минус ${r.negative}`)
  if (r.target) parts.push(`целевых ${r.target}`)
  if (r.keywords) parts.push(`новых ключевых фраз ${r.keywords}`)
  if (r.skipped) parts.push(`пропущено ${r.skipped}`)
  if (r.errors) parts.push(`ошибок ${r.errors}`)
  return parts.length ? `Применено: ${parts.join(', ')}` : 'Ничего не изменилось'
})
const forceable = computed(() => (result.value?.problems || []).filter((p) => p.status === 'skipped' && p.conflicts))

function placeText(q) {
  const camps = q.campaigns.map((c) => c.name || `кампания ${c.id}`)
  let text = camps.slice(0, 2).join(', ') + (camps.length > 2 ? ` и ещё ${camps.length - 2}` : '')
  const groups = q.adgroups.map((g) => g.name).filter(Boolean)
  if (groups.length === 1) text += ` → ${groups[0]}`
  else if (groups.length > 1) text += ` · ${groups.length} ${plural(groups.length, 'группа', 'группы', 'групп')}`
  return text
}

function initDecision(q) {
  if (decisions[q.query_norm]?.touched) return
  const l = q.label
  decisions[q.query_norm] = {
    verdict: l?.verdict || null,
    negative_phrase: l?.negative_phrase || q.query,
    level: levelFor(q, l?.level || 'adgroup'),
    add_keyword: false,
    adgroup_row_id: q.adgroups[0]?.id || null,
    touched: false,
  }
}

/** Запрос без группы (например, из автотаргетинга) можно минусовать только в кампании. */
function levelFor(q, level) {
  return q?.adgroups?.length ? level : 'campaign'
}

function touch(norm) {
  if (!decisions[norm]) return
  decisions[norm].touched = true
  delete rowResults[norm]
}

function conflictText(c) {
  const list = c.conflicts.map((k) => `«${k.keyword}»`).join(', ')
  return c.total > c.conflicts.length ? `${list} и ещё ${c.total - c.conflicts.length}` : list
}

function checkKey(d) {
  return `${d.level}|${(d.negative_phrase || '').trim()}`
}

/** Конфликты и ошибки формата для текущей минус-фразы строки: свежая проверка или то, что пришло с меткой. */
function checkOf(q) {
  const d = decisions[q.query_norm]
  if (!d || d.verdict !== 'negative') return null
  const c = checks[q.query_norm]
  if (c && c.key === checkKey(d)) return c
  const l = q.label
  if (l?.verdict === 'negative' && l.negative_phrase === d.negative_phrase.trim() && l.level === d.level)
    return { problems: [], conflicts: q.conflicts, total: q.conflicts_total }
  return null
}

const checkQueue = new Set()
let checkTimer = null

function scheduleCheck(norms) {
  for (const n of norms) checkQueue.add(n)
  clearTimeout(checkTimer)
  checkTimer = setTimeout(runChecks, 250)
}

async function runChecks() {
  const list = [...checkQueue].filter((n) => decisions[n]?.verdict === 'negative').slice(0, 500)
  checkQueue.clear()
  if (!list.length) return
  const keys = Object.fromEntries(list.map((n) => [n, checkKey(decisions[n])]))
  try {
    const res = await wsApi.queriesCheck(
      ws.value.id,
      list.map((n) => ({ query_norm: n, negative_phrase: decisions[n].negative_phrase, level: decisions[n].level })),
    )
    for (const it of res.items || []) {
      checks[it.query_norm] = {
        key: keys[it.query_norm],
        problems: it.problems || [],
        conflicts: it.conflicts || [],
        total: it.conflicts_total || 0,
      }
    }
  } catch {
    // Проверка — подсказка; при применении фразы всё равно проверяются на сервере.
  }
}

function setVerdict(q, verdict) {
  const d = decisions[q.query_norm]
  d.verdict = d.verdict === verdict ? null : verdict
  touch(q.query_norm)
  if (d.verdict && !selected.value.has(q.query_norm)) toggle(q.query_norm, true)
  if (d.verdict === 'negative') scheduleCheck([q.query_norm])
}

function markSelected(verdict) {
  const norms = [...selected.value].filter((n) => decisions[n])
  for (const n of norms) {
    decisions[n].verdict = verdict
    touch(n)
  }
  if (verdict === 'negative') scheduleCheck(norms)
}

function setLevelSelected(event) {
  const level = event.target.value
  event.target.value = ''
  if (!level) return
  const norms = [...selected.value].filter((n) => decisions[n]?.verdict === 'negative')
  for (const n of norms) {
    decisions[n].level = levelFor(byNorm.value[n], level)
    touch(n)
  }
  scheduleCheck(norms)
}

function toggle(norm, on) {
  const next = new Set(selected.value)
  if (on) next.add(norm)
  else next.delete(norm)
  selected.value = next
}

function toggleAll(on) {
  selected.value = on ? new Set(items.value.map((q) => q.query_norm)) : new Set()
}

function queueParams(offset = 0) {
  return {
    tab: tab.value || 'negative',
    search: filters.search,
    campaign_row_id: filters.campaign,
    min_cost_rub: filters.minCost,
    no_conversions: filters.noConv,
    limit: PAGE,
    offset,
  }
}

let loadSeq = 0

async function load({ more = false } = {}) {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await wsApi.queries(ws.value.id, queueParams(more ? items.value.length : 0))
    if (seq !== loadSeq) return
    // Первый заход: если предложений в минус нет, а новые есть — открываем «Новые».
    if (!tab.value) {
      if (!res.counts.negative && res.counts.new) {
        tab.value = 'new'
        return load()
      }
      tab.value = 'negative'
    }
    for (const q of res.items) initDecision(q)
    data.value = res
    if (!daysPicked.value) days.value = loadedDays(res) || days.value
    items.value = more ? [...items.value, ...res.items] : res.items
    if (!more) {
      const present = new Set(res.items.map((q) => q.query_norm))
      selected.value = new Set([...selected.value].filter((n) => present.has(n)))
    }
  } catch (err) {
    if (seq === loadSeq) loadError.value = errorText(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function pickDays(d) {
  days.value = d
  daysPicked.value = true
}

// Длина загруженного периода → ближайший из чипов.
function loadedDays(res) {
  if (!res?.date_from || !res?.date_to) return 0
  const span = Math.round((Date.parse(res.date_to) - Date.parse(res.date_from)) / 86_400_000) + 1
  if (!(span > 0)) return 0
  return PERIODS.reduce((best, d) => (Math.abs(d - span) < Math.abs(best - span) ? d : best), PERIODS[0])
}

async function refreshCounts() {
  try {
    const res = await wsApi.queries(ws.value.id, { ...queueParams(), tab: 'all', limit: 1 })
    data.value = { ...(data.value || {}), ...res, items: data.value?.items || [], tab: data.value?.tab }
  } catch {
    // Счётчики обновятся при следующем заходе во вкладку.
  }
}

function setTab(key) {
  if (tab.value === key) return
  tab.value = key
  selected.value = new Set()
  router.replace({ query: { ...route.query, tab: key } })
  if (key === 'memory') loadMemory()
  else load()
}

async function sync() {
  busy.value = 'sync'
  busyText.value = 'Запрашиваем отчёт у Директа…'
  try {
    const res = await wsApi.queriesSync(ws.value.id, days.value, (job) => {
      busyText.value = `Загрузка: ${jobProgressText(job)}`
    })
    const errors = res.errors || []
    pushToast(
      `Загружено запросов: ${num(res.queries)} из ${res.campaigns} ${plural(res.campaigns, 'кампании', 'кампаний', 'кампаний')}` +
        (errors.length ? `. Не вышло: ${errors.map((e) => e.name).join(', ')}` : ''),
      errors.length ? 'warn' : 'ok',
    )
    if (tab.value === 'memory') await refreshCounts()
    else await load()
    // Предложений в минус ещё нет, а новые появились — показываем их, а не пустую вкладку.
    if (tab.value === 'negative' && !counts.value.negative && counts.value.new) setTab('new')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function classify() {
  const relabel = !counts.value.new
  if (
    relabel &&
    !(await askConfirm(
      'Новых запросов нет. Разметить заново неподтверждённые предложения? Учтём свежий бриф и память решений.',
      { title: 'Переразметка', confirmText: 'Разметить заново' },
    ))
  )
    return
  const payload = { limit: 300, relabel, campaign_row_id: filters.campaign ? Number(filters.campaign) : null }
  busy.value = 'classify'
  busyText.value = 'Считаем объём и стоимость…'
  let est
  try {
    est = await wsApi.queriesClassifyEstimate(ws.value.id, payload)
  } catch (err) {
    pushToast(errorText(err), 'error')
    busy.value = ''
    busyText.value = ''
    return
  }
  // Один вызов ИИ, который влезает в лимит, — без лишнего вопроса; массовая разметка — после оценки.
  if (!est.ai || est.ai_error || (est.batches <= 1 && !(est.fits !== null && est.fits < est.ai))) {
    return runClassify(payload)
  }
  busy.value = ''
  busyText.value = ''
  Object.assign(estimate, { open: true, est, payload })
}

function confirmClassify() {
  estimate.open = false
  return runClassify(estimate.payload)
}

function usageText(usage) {
  const tokens = usage.tokens_in + usage.tokens_out
  const cost = usage.cost_rub === null ? '' : `, ${usage.estimated ? '≈ ' : ''}${rubAi(usage.cost_rub)}`
  return `ИИ: ${usage.calls} ${plural(usage.calls, 'вызов', 'вызова', 'вызовов')}, ${num(tokens)} ${plural(tokens, 'токен', 'токена', 'токенов')}${cost}`
}

async function runClassify(payload) {
  busy.value = 'classify'
  busyText.value = 'Размечаем запросы…'
  try {
    const res = await wsApi.queriesClassify(ws.value.id, payload, (job) => {
      busyText.value = `Разметка: ${jobProgressText(job)}`
    })
    if (!res.labeled) {
      pushToast(res.ai_error ? `ИИ не разметил запросы: ${res.ai_error}` : 'Размечать нечего', res.ai_error ? 'error' : 'info')
    } else {
      const parts = [`Размечено ${res.labeled}: в минус ${res.negative}, целевых ${res.target}`]
      if (res.rules) parts.push(`по памяти, брифу и Директу — ${res.rules}`)
      if (res.left) parts.push(`ещё без разметки ${res.left}`)
      if (res.usage) parts.push(usageText(res.usage))
      pushToast(parts.join('; '), 'ok')
      if (res.ai_error) pushToast(`ИИ разобрал не всё: ${res.ai_error}`, 'warn')
    }
    if (res.negative && tab.value !== 'negative') setTab('negative')
    else if (tab.value === 'memory') await refreshCounts()
    else await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

function toItem(q) {
  const d = decisions[q.query_norm]
  const keyword = d.verdict === 'target' && d.add_keyword && q.adgroups.length > 0
  return {
    query_norm: q.query_norm,
    verdict: d.verdict,
    negative_phrase: d.verdict === 'negative' ? d.negative_phrase.trim() : '',
    level: d.level,
    add_keyword: keyword,
    adgroup_row_id: keyword ? d.adgroup_row_id : null,
  }
}

function openApply() {
  const chosen = items.value.filter((q) => selected.value.has(q.query_norm))
  const ready = chosen.filter((q) => decisions[q.query_norm]?.verdict)
  if (!ready.length) {
    pushToast('Отметьте выбранные запросы: «Минус» или «Целевой»', 'error')
    return
  }
  applyModal.items = ready.map(toItem)
  applyModal.skipped = chosen.length - ready.length
  applyModal.open = true
}

function confirmApply() {
  const bid = applyModal.bid === '' ? null : Number(applyModal.bid)
  return apply(applyModal.items, { bid: Number.isFinite(bid) && bid > 0 ? bid : null })
}

async function apply(list, { force = false, bid = null } = {}) {
  busy.value = 'apply'
  busyText.value = 'Применяем решения…'
  // Тексты запросов запоминаем до перезагрузки: применённые уйдут из вкладки.
  const names = Object.fromEntries(items.value.map((q) => [q.query_norm, q.query]))
  try {
    const res = await wsApi.queriesApply(ws.value.id, { items: list, force, bid_rub: bid }, (job) => {
      busyText.value = `Применяем: ${jobProgressText(job)}`
    })
    applyModal.open = false
    const done = new Set()
    for (const it of res.items || []) {
      rowResults[it.query_norm] = it
      if (it.status === 'ok' || it.status === 'partial') {
        done.add(it.query_norm)
        if (decisions[it.query_norm]) decisions[it.query_norm].touched = false
      }
    }
    selected.value = new Set([...selected.value].filter((n) => !done.has(n)))
    result.value = {
      ...res,
      sent: list,
      problems: (res.items || [])
        .filter((i) => i.status !== 'ok' && (i.status !== 'partial' || i.message))
        .map((i) => ({ ...i, query: names[i.query_norm] || i.query_norm })),
    }
    pushToast(resultTitle.value, res.status === 'ok' ? 'ok' : res.status === 'partial' ? 'warn' : 'error')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function applyForce() {
  const norms = new Set(forceable.value.map((p) => p.query_norm))
  const list = (result.value?.sent || []).filter((i) => norms.has(i.query_norm))
  if (!list.length) return
  const ok = await askConfirm(
    'Эти минус-фразы пересекаются с вашими ключевыми фразами и могут отрезать целевой трафик. Добавить всё равно?',
    { title: 'Добавить с конфликтами', confirmText: 'Добавить', danger: true },
  )
  if (ok) await apply(list, { force: true })
}

function onUndone() {
  result.value = null
  for (const key of Object.keys(rowResults)) delete rowResults[key]
  if (tab.value === 'memory') loadMemory()
  else load()
}

// --- Память решений ---

let memSeq = 0

async function loadMemory(more = false) {
  const seq = ++memSeq
  mem.loading = true
  mem.error = ''
  try {
    const res = await wsApi.queriesMemory(ws.value.id, {
      verdict: mem.verdict,
      search: mem.search,
      limit: PAGE,
      offset: more ? mem.items.length : 0,
    })
    if (seq !== memSeq) return
    mem.items = more ? [...mem.items, ...res.items] : res.items
    mem.total = res.total
    if (!more) {
      const present = new Set(res.items.map((m) => m.query_norm))
      mem.selected = new Set([...mem.selected].filter((n) => present.has(n)))
    }
  } catch (err) {
    if (seq === memSeq) mem.error = errorText(err)
  } finally {
    if (seq === memSeq) mem.loading = false
  }
}

function memToggle(norm, on) {
  const next = new Set(mem.selected)
  if (on) next.add(norm)
  else next.delete(norm)
  mem.selected = next
}

function memToggleAll(on) {
  mem.selected = on ? new Set(mem.items.map((m) => m.query_norm)) : new Set()
}

async function forget() {
  const norms = [...mem.selected]
  if (!norms.length) return
  const ok = await askConfirm(
    `Забыть ${norms.length} ${plural(norms.length, 'решение', 'решения', 'решений')}? Запросы вернутся в разбор, ИИ перестанет брать их как примеры. Минус-фразы в Директе останутся.`,
    { title: 'Забыть решения', confirmText: 'Забыть' },
  )
  if (!ok) return
  mem.busy = true
  try {
    const res = await wsApi.queriesForget(ws.value.id, norms)
    pushToast(`Забыто: ${res.forgotten}`, 'ok')
    mem.selected = new Set()
    await Promise.all([loadMemory(), refreshCounts()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    mem.busy = false
  }
}

let filterTimer = null
watch(
  () => [filters.search, filters.campaign, filters.minCost, filters.noConv],
  () => {
    clearTimeout(filterTimer)
    filterTimer = setTimeout(() => {
      if (tab.value !== 'memory') load()
    }, 300)
  },
)

let memTimer = null
watch(
  () => [mem.search, mem.verdict],
  () => {
    clearTimeout(memTimer)
    memTimer = setTimeout(() => loadMemory(), 300)
  },
)

onMounted(() => {
  if (tab.value === 'memory') {
    loadMemory()
    refreshCounts()
  } else {
    load()
  }
})

onBeforeUnmount(() => {
  clearTimeout(filterTimer)
  clearTimeout(memTimer)
  clearTimeout(checkTimer)
})
</script>
