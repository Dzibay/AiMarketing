<template>
  <div class="ws-toolbar">
    <div class="seg seg--tabs" role="tablist" aria-label="Срез">
      <button
        v-for="d in DIMENSIONS"
        :key="d.key"
        type="button"
        role="tab"
        :class="{ active: dimension === d.key }"
        :disabled="!!busy"
        @click="setDimension(d.key)"
      >
        {{ d.label }}
      </button>
    </div>
    <div class="seg" role="group" aria-label="Период">
      <button
        v-for="d in [7, 14, 30, 90]"
        :key="d"
        type="button"
        :class="{ active: days === d }"
        :disabled="!!busy"
        @click="days = d"
      >
        {{ d }} дней
      </button>
    </div>
    <div class="row-actions">
      <button class="btn btn--accent" type="button" :disabled="!!busy || !campaignIds.length" @click="loadReport">
        {{ busy === 'report' ? 'Строим…' : report ? 'Обновить отчёт' : 'Построить отчёт' }}
      </button>
    </div>
  </div>

  <section class="panel">
    <div class="filters">
      <details class="slice-camps">
        <summary>
          Кампании: {{ campaignIds.length === campaigns.length ? 'все' : campaignIds.length }} из {{ campaigns.length }}
        </summary>
        <label class="slice-camps__all">
          <input
            type="checkbox"
            :checked="campaignIds.length === campaigns.length"
            @change="pickAll($event.target.checked)"
          />
          Все
        </label>
        <label v-for="c in campaigns" :key="c.campaign_row_id">
          <input
            type="checkbox"
            :checked="picked.has(c.campaign_row_id)"
            @change="pick(c.campaign_row_id, $event.target.checked)"
          />
          {{ c.name }} <small class="cell-note">{{ CAMPAIGN_TYPES[c.type] || c.type }}</small>
        </label>
      </details>
      <p class="sync-status">
        <template v-if="busyText">{{ busyText }}</template>
        <template v-else-if="report">
          {{ formatDate(report.date_from) }} — {{ formatDate(report.date_to) }} ·
          {{ report.goal_name ? `конверсии по цели «${report.goal_name}»` : 'все конверсии' }}
          <template v-if="report.target_cpa_kopecks"> · цель CPA {{ rub(report.target_cpa_kopecks) }}</template>
        </template>
        <template v-else>{{ DIM_HINTS[dimension] }}</template>
      </p>
    </div>

    <p v-if="dimension === 'hour'" class="notice">
      Время суток считается по Метрике: визиты из выбранных кампаний по часу визита, в часовом поясе счётчика.
      Процент ставки по часам меняет расписание показов кампании; автостратегии его могут не учитывать.
    </p>
    <p v-else-if="dimension !== 'placement'" class="notice">
      Подсказка — во сколько раз конверсия среза отличается от средней, сглажено и в пределах ±50 %. При малом числе
      кликов подсказки нет. Корректировка применяется ко всем выбранным кампаниям.
    </p>
    <p v-if="loadError" class="notice notice--warn">{{ loadError }}</p>
    <p v-for="e in report?.errors || []" :key="e.client_login" class="notice notice--warn">
      {{ e.client_login }}: {{ e.message || e.error }}
    </p>
    <div v-if="done" class="notice">
      {{ done }}
      <RouterLink :to="`/w/${ws.id}/actions`">Журнал и откат</RouterLink>
    </div>
    <ul v-if="doneErrors.length" class="notice notice--warn">
      <li v-for="e in doneErrors" :key="e.campaign_row_id">«{{ e.name }}»: {{ e.message || e.error }}</li>
    </ul>

    <div v-if="!report && !busy" class="empty">Выберите срез и период и постройте отчёт.</div>

    <!-- Площадки РСЯ -->
    <template v-else-if="report && report.dimension === 'placement'">
      <div class="stats-grid">
        <div class="stat-card">
          <small>Площадок</small>
          <strong>{{ num(report.items.length) }}</strong>
          <span class="cell-note">расход {{ rub(report.totals.cost_kopecks) }}</span>
        </div>
        <div class="stat-card">
          <small>К запрету по правилам</small>
          <strong :class="{ 'tone-bad': flagged.length }">{{ num(flagged.length) }}</strong>
          <span class="cell-note">слили {{ rub(sum(flagged, 'cost_kopecks')) }}</span>
        </div>
        <div class="stat-card">
          <small>Игры и приложения</small>
          <strong>{{ num(apps.length) }}</strong>
          <span class="cell-note">расход {{ rub(sum(apps, 'cost_kopecks')) }}</span>
        </div>
        <div class="stat-card">
          <small>Порог «расход без конверсий»</small>
          <strong>{{ rub(report.waste_kopecks) }}</strong>
          <span class="cell-note">{{ report.target_cpa_kopecks ? 'цель CPA' : 'средний CPA или 20 кликов' }}</span>
        </div>
      </div>

      <div class="filters">
        <div class="seg seg--sm" role="group" aria-label="Фильтр площадок">
          <button
            v-for="f in PLACEMENT_FILTERS"
            :key="f.key"
            type="button"
            :class="{ active: placementFilter === f.key }"
            @click="placementFilter = f.key"
          >
            {{ f.label }}
          </button>
        </div>
        <input v-model.trim="search" type="search" placeholder="поиск площадки" />
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="!flagged.length" @click="selectFlagged">
            Отметить подсказки
          </button>
          <button
            class="btn btn--ghost"
            type="button"
            :disabled="!!busy || !report.items.length"
            title="ИИ посмотрит площадки с расходом и предложит, что запретить. Ничего не меняет"
            @click="askAi"
          >
            {{ busy === 'ai' ? 'ИИ думает…' : 'Разобрать с ИИ' }}
          </button>
          <button class="btn btn--danger-solid" type="button" :disabled="!!busy || !selected.size" @click="excludeSelected">
            Запретить выбранные ({{ selected.size }})
          </button>
        </div>
      </div>
      <p v-if="aiNote" class="cell-note">{{ aiNote }}</p>

      <div class="table-scroll" :class="{ 'is-loading': busy === 'report' }">
        <table class="table">
          <thead>
            <tr>
              <th class="check-col">
                <input
                  type="checkbox"
                  :checked="placementRows.length > 0 && placementRows.every((r) => selected.has(r.key))"
                  aria-label="Выбрать все"
                  @change="toggleAll(placementRows, $event.target.checked)"
                />
              </th>
              <th>Площадка</th>
              <th class="num">Показы</th>
              <th class="num">Клики</th>
              <th class="num">CTR</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th class="num">CPA</th>
              <th class="num">Отказы</th>
              <th>Почему</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in placementRows" :key="r.key" :class="{ 'is-off': excluded.has(r.key) }">
              <td class="check-col">
                <input
                  type="checkbox"
                  :disabled="excluded.has(r.key) || !r.excludable"
                  :title="r.excludable ? '' : 'Эту площадку Директ запретить не даёт'"
                  :checked="selected.has(r.key)"
                  @change="toggle(r.key, $event.target.checked)"
                />
              </td>
              <td>
                {{ r.label }}
                <span v-if="KINDS[r.kind]" class="chip" :class="KIND_CHIPS[r.kind]">{{ KINDS[r.kind] }}</span>
                <span v-if="excluded.has(r.key)" class="chip">запрещена</span>
                <small v-if="r.campaigns.length > 1" class="cell-note">в {{ r.campaigns.length }} кампаниях</small>
              </td>
              <td class="num">{{ num(r.impressions) }}</td>
              <td class="num">{{ num(r.clicks) }}</td>
              <td class="num">{{ r.ctr != null ? pct(r.ctr, 2) : '—' }}</td>
              <td class="num">{{ rub(r.cost_kopecks) }}</td>
              <td class="num" :class="{ 'tone-bad': !r.conversions && r.cost_kopecks > 0 }">{{ num(r.conversions) }}</td>
              <td class="num">{{ r.cpa_kopecks != null ? rub(r.cpa_kopecks) : '—' }}</td>
              <td class="num">{{ r.bounce_rate != null ? pct(r.bounce_rate) : '—' }}</td>
              <td class="slice-why">
                <span v-if="ai[r.key]" class="chip" :class="VERDICT_CHIPS[ai[r.key].verdict]">
                  ИИ: {{ VERDICTS[ai[r.key].verdict] }}
                </span>
                <small v-if="ai[r.key]?.reason" class="cell-note">{{ ai[r.key].reason }}</small>
                <small v-for="why in r.reasons" :key="why" class="cell-note tone-bad">{{ why }}</small>
              </td>
            </tr>
            <tr v-if="!placementRows.length">
              <td colspan="10" class="empty">Площадок нет — в выбранных кампаниях не было показов в сетях.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Корректировки -->
    <template v-else-if="report">
      <div class="filters">
        <p class="cell-note">
          Средние: CR {{ report.totals.cr != null ? pct(report.totals.cr, 2) : '—' }}
          <template v-if="report.totals.cpa_kopecks"> · CPA {{ rub(report.totals.cpa_kopecks) }}</template>
          <template v-if="report.hidden"> · показаны {{ report.items.length }} самых затратных, ещё {{ num(report.hidden) }} скрыто</template>
          <template v-if="!report.current_known && report.dimension === 'hour'">
            · текущее расписание не показано: кампаний больше 30
          </template>
        </p>
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="!suggestedCount" @click="takeSuggested">
            Взять подсказки ({{ suggestedCount }})
          </button>
          <button class="btn btn--accent" type="button" :disabled="!!busy || !changes.length" @click="applyAdjust">
            {{ busy === 'adjust' ? 'Применяем…' : `Применить корректировки (${changes.length})` }}
          </button>
        </div>
      </div>

      <div class="table-scroll" :class="{ 'is-loading': busy === 'report' }">
        <table class="table">
          <thead>
            <tr>
              <th>{{ DIMENSIONS.find((d) => d.key === report.dimension).label }}</th>
              <th v-if="report.dimension === 'hour'" class="num">Визиты</th>
              <template v-else>
                <th class="num">Клики</th>
                <th class="num">Расход</th>
              </template>
              <th class="num">Конв.</th>
              <th class="num">CR</th>
              <th v-if="report.dimension !== 'hour'" class="num">CPA</th>
              <th class="num">Отказы</th>
              <th class="num">Сейчас</th>
              <th class="num">Подсказка</th>
              <th class="num" title="Изменение ставки в процентах: −20 — на 20 % ниже, −100 — не показывать">Новая, %</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in adjustRows" :key="r.key">
              <td>
                {{ r.label }}
                <small v-if="!r.adjustable" class="cell-note">корректировку сюда не поставить</small>
              </td>
              <td v-if="report.dimension === 'hour'" class="num">{{ num(r.visits) }}</td>
              <template v-else>
                <td class="num">{{ num(r.clicks) }}</td>
                <td class="num">{{ rub(r.cost_kopecks) }}</td>
              </template>
              <td class="num">{{ num(r.conversions) }}</td>
              <td class="num" :class="crTone(r)">{{ r.cr != null ? pct(r.cr, 2) : '—' }}</td>
              <td v-if="report.dimension !== 'hour'" class="num">{{ r.cpa_kopecks != null ? rub(r.cpa_kopecks) : '—' }}</td>
              <td class="num">{{ r.bounce_rate != null ? pct(r.bounce_rate) : '—' }}</td>
              <td class="num">
                <template v-if="r.current_mixed">разные</template>
                <template v-else-if="r.current_pct != null">{{ changeText(r.current_pct) }}</template>
                <template v-else>—</template>
              </td>
              <td class="num">{{ r.suggested_pct != null ? changeText(r.suggested_pct) : '—' }}</td>
              <td class="num">
                <input
                  v-if="r.adjustable"
                  v-model="edits[r.key]"
                  class="slice-pct"
                  type="number"
                  :step="report.dimension === 'hour' ? 10 : 5"
                  :min="-100"
                  :max="report.dimension === 'hour' ? 100 : 1200"
                  placeholder="—"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDate, num, pct, plural, rub, rubAi } from '../../utils/format'
import { useWs } from './context'

const DIMENSIONS = [
  { key: 'placement', label: 'Площадки РСЯ' },
  { key: 'device', label: 'Устройства' },
  { key: 'demography', label: 'Пол и возраст' },
  { key: 'region', label: 'Регионы' },
  { key: 'hour', label: 'Время суток' },
]
const DIM_HINTS = {
  placement: 'Где показывались объявления в сетях и что из этого стоит запретить.',
  device: 'Конверсия по устройствам и корректировки ставок.',
  demography: 'Конверсия по полу и возрасту и корректировки ставок.',
  region: 'Конверсия по регионам и корректировки ставок.',
  hour: 'Конверсия по часам из Метрики и процент ставки в расписании.',
}
const CAMPAIGN_TYPES = { TEXT_CAMPAIGN: 'ТГО', UNIFIED_CAMPAIGN: 'ЕПК', DYNAMIC_TEXT_CAMPAIGN: 'ДО', SMART_CAMPAIGN: 'Смарт' }
const KINDS = { game: 'игра', app: 'приложение', yandex: 'Яндекс' }
const KIND_CHIPS = { game: 'chip--warning', app: 'chip--warning', yandex: 'chip--info' }
const VERDICTS = { exclude: 'запретить', watch: 'наблюдать', keep: 'оставить' }
const VERDICT_CHIPS = { exclude: 'chip--bad', watch: 'chip--warning', keep: 'chip--ok' }
const PLACEMENT_FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'flagged', label: 'К запрету' },
  { key: 'apps', label: 'Игры и приложения' },
  { key: 'ai', label: 'ИИ: запретить' },
]

const { ws } = useWs()

const campaigns = ref([])
const picked = ref(new Set())
const dimension = ref('placement')
const days = ref(30)
const report = ref(null)
const busy = ref('')
const busyText = ref('')
const loadError = ref('')
const done = ref('')
const doneErrors = ref([])
const selected = ref(new Set())
const excluded = ref(new Set())
const ai = ref({})
const aiNote = ref('')
const placementFilter = ref('all')
const search = ref('')
const edits = reactive({})

const campaignIds = computed(() => campaigns.value.map((c) => c.campaign_row_id).filter((id) => picked.value.has(id)))
const items = computed(() => report.value?.items || [])
const flagged = computed(() => items.value.filter((r) => r.reasons?.length))
const apps = computed(() => items.value.filter((r) => r.kind === 'game' || r.kind === 'app'))
const placementRows = computed(() => {
  const q = search.value.toLowerCase()
  return items.value.filter((r) => {
    if (q && !r.key.includes(q)) return false
    if (placementFilter.value === 'flagged') return r.reasons?.length
    if (placementFilter.value === 'apps') return r.kind === 'game' || r.kind === 'app'
    if (placementFilter.value === 'ai') return ai.value[r.key]?.verdict === 'exclude'
    return true
  })
})
const adjustRows = computed(() => items.value)
const suggestedCount = computed(() => items.value.filter((r) => r.adjustable && r.suggested_pct != null).length)
const changes = computed(() =>
  items.value
    .filter((r) => r.adjustable && edits[r.key] !== '' && edits[r.key] != null && !Number.isNaN(Number(edits[r.key])))
    .map((r) => ({ key: r.key, label: r.label, pct: 100 + Number(edits[r.key]) }))
    .filter((c) => c.pct !== currentOf(c.key)),
)

function currentOf(key) {
  const r = items.value.find((i) => i.key === key)
  return r && !r.current_mixed && r.current_pct != null ? r.current_pct : 100
}

function changeText(value) {
  if (value === 0) return 'не показывать'
  const d = value - 100
  return d === 0 ? '0' : `${d > 0 ? '+' : '−'}${Math.abs(d)} %`
}

function crTone(r) {
  const avg = report.value?.totals?.cr
  if (!avg || r.cr == null || (r.clicks || r.visits || 0) < 30) return ''
  return r.cr >= avg * 1.2 ? 'tone-ok' : r.cr <= avg * 0.8 ? 'tone-bad' : ''
}

function sum(rows, key) {
  return rows.reduce((s, r) => s + (r[key] || 0), 0)
}

function pick(id, on) {
  const next = new Set(picked.value)
  on ? next.add(id) : next.delete(id)
  picked.value = next
}

function pickAll(on) {
  picked.value = new Set(on ? campaigns.value.map((c) => c.campaign_row_id) : [])
}

function toggle(key, on) {
  const next = new Set(selected.value)
  on ? next.add(key) : next.delete(key)
  selected.value = next
}

function toggleAll(rows, on) {
  const next = new Set(selected.value)
  for (const r of rows) {
    if (excluded.value.has(r.key) || !r.excludable) continue
    on ? next.add(r.key) : next.delete(r.key)
  }
  selected.value = next
}

function selectFlagged() {
  selected.value = new Set(flagged.value.filter((r) => r.excludable && !excluded.value.has(r.key)).map((r) => r.key))
}

function setDimension(key) {
  if (dimension.value === key) return
  dimension.value = key
  report.value = null
  loadError.value = ''
  done.value = ''
  doneErrors.value = []
}

function resetEdits() {
  for (const k of Object.keys(edits)) delete edits[k]
}

function takeSuggested() {
  for (const r of items.value) {
    if (r.adjustable && r.suggested_pct != null) edits[r.key] = r.suggested_pct - 100
  }
}

async function loadCampaigns() {
  try {
    const data = await wsApi.campaigns(ws.value.id)
    campaigns.value = data.items.filter((c) => c.state !== 'ARCHIVED')
    picked.value = new Set(campaigns.value.filter((c) => c.state === 'ON').map((c) => c.campaign_row_id))
    if (!picked.value.size) pickAll(true)
  } catch (err) {
    loadError.value = errorText(err)
  }
}

async function loadReport() {
  busy.value = 'report'
  busyText.value = 'Запрашиваем отчёт…'
  loadError.value = ''
  done.value = ''
  doneErrors.value = []
  try {
    report.value = await wsApi.slice(
      ws.value.id,
      { dimension: dimension.value, days: days.value, campaign_ids: campaignIds.value },
      (job) => {
        busyText.value = `Отчёт: ${jobProgressText(job)}`
      },
    )
    selected.value = new Set()
    excluded.value = new Set()
    ai.value = {}
    aiNote.value = ''
    resetEdits()
    for (const r of report.value.items) {
      if (r.adjustable && !r.current_mixed && r.current_pct != null && r.current_pct !== 100) edits[r.key] = r.current_pct - 100
    }
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

function usageText(usage) {
  const tokens = usage.tokens_in + usage.tokens_out
  const cost = usage.cost_rub === null ? '' : `, ${usage.estimated ? '≈ ' : ''}${rubAi(usage.cost_rub)}`
  return `ИИ: ${usage.calls} ${plural(usage.calls, 'вызов', 'вызова', 'вызовов')}, ${num(tokens)} ${plural(tokens, 'токен', 'токена', 'токенов')}${cost}`
}

async function askAi() {
  // ИИ смотрит площадки с расходом: остальные ничего не стоили.
  const rows = items.value.filter((r) => r.cost_kopecks > 0 && r.excludable && !excluded.value.has(r.key)).slice(0, 300)
  if (!rows.length) return pushToast('Нет площадок с расходом', 'warn')
  busy.value = 'ai'
  busyText.value = 'ИИ разбирает площадки…'
  try {
    const payload = rows.map((r) => ({
      key: r.key,
      label: r.label,
      impressions: r.impressions,
      clicks: r.clicks,
      cost_kopecks: r.cost_kopecks,
      conversions: r.conversions,
      ctr: r.ctr,
      bounce_rate: r.bounce_rate,
    }))
    const res = await wsApi.placementsAi(ws.value.id, { items: payload }, (job) => {
      busyText.value = jobProgressText(job)
    })
    ai.value = res.verdicts || {}
    const toExclude = Object.values(ai.value).filter((v) => v.verdict === 'exclude').length
    aiNote.value = [
      `ИИ предлагает запретить: ${toExclude} из ${rows.length}`,
      res.usage ? usageText(res.usage) : '',
      res.error ? `часть не разобрана: ${res.error}` : '',
    ]
      .filter(Boolean)
      .join(' · ')
    if (toExclude) {
      placementFilter.value = 'ai'
      selected.value = new Set(Object.keys(ai.value).filter((k) => ai.value[k].verdict === 'exclude'))
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function excludeSelected() {
  const rows = items.value.filter((r) => selected.value.has(r.key))
  const camps = new Set(rows.flatMap((r) => r.campaigns.map((c) => c.campaign_row_id)))
  const ok = await askConfirm(
    `Запретить ${rows.length} ${plural(rows.length, 'площадку', 'площадки', 'площадок')} в ${camps.size} ` +
      `${plural(camps.size, 'кампании', 'кампаниях', 'кампаниях')}, где они показывались? ` +
      `Расход на них за период — ${rub(sum(rows, 'cost_kopecks'))}. Откатить можно в журнале.`,
    { confirmText: 'Запретить', danger: true },
  )
  if (!ok) return
  busy.value = 'exclude'
  busyText.value = 'Запрещаем площадки…'
  doneErrors.value = []
  try {
    const res = await wsApi.placementsExclude(
      ws.value.id,
      { items: rows.map((r) => ({ placement: r.label, campaign_ids: r.campaigns.map((c) => c.campaign_row_id) })) },
      (job) => {
        busyText.value = `Запрет: ${jobProgressText(job)}`
      },
    )
    done.value = `Запрещено площадок: ${res.sites} в ${res.applied} ${plural(res.applied, 'кампании', 'кампаниях', 'кампаниях')}.`
    doneErrors.value = res.errors || []
    pushToast(done.value, res.errors?.length ? 'warn' : 'ok')
    const failed = new Set((res.errors || []).map((e) => e.campaign_row_id))
    const next = new Set(excluded.value)
    for (const r of rows) if (!r.campaigns.some((c) => failed.has(c.campaign_row_id))) next.add(r.key)
    excluded.value = next
    selected.value = new Set()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function applyAdjust() {
  const list = changes.value
  const shown = list
    .slice(0, 6)
    .map((c) => `${c.label}: ${changeText(c.pct)}`)
    .join('; ')
  const ok = await askConfirm(
    `Поставить корректировки в ${campaignIds.value.length} ` +
      `${plural(campaignIds.value.length, 'кампании', 'кампаниях', 'кампаниях')}: ${shown}` +
      (list.length > 6 ? ` и ещё ${list.length - 6}` : '') +
      '. Откатить можно в журнале.',
    { confirmText: 'Применить' },
  )
  if (!ok) return
  busy.value = 'adjust'
  busyText.value = 'Применяем корректировки…'
  doneErrors.value = []
  try {
    const res = await wsApi.sliceAdjust(
      ws.value.id,
      { dimension: report.value.dimension, items: list, campaign_ids: campaignIds.value },
      (job) => {
        busyText.value = `Корректировки: ${jobProgressText(job)}`
      },
    )
    done.value = `Корректировки обновлены в ${res.applied} ${plural(res.applied, 'кампании', 'кампаниях', 'кампаниях')}.`
    doneErrors.value = res.errors || []
    pushToast(done.value, res.errors?.length ? 'warn' : 'ok')
    for (const c of list) {
      const r = items.value.find((i) => i.key === c.key)
      if (r) {
        r.current_pct = c.pct
        r.current_mixed = false
      }
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

onMounted(loadCampaigns)
</script>

<style scoped>
.slice-camps {
  position: relative;
  min-width: 260px;
}
.slice-camps summary {
  cursor: pointer;
  padding: 8px 12px;
  border: 1px solid var(--line, #ddd);
  border-radius: 10px;
}
.slice-camps[open] {
  z-index: 5;
}
.slice-camps label {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 0;
}
.slice-camps[open] > :not(summary) {
  background: var(--surface, #fff);
}
.slice-camps__all {
  border-bottom: 1px solid var(--line, #ddd);
}
.slice-why {
  max-width: 320px;
}
.slice-why .cell-note {
  display: block;
}
.slice-pct {
  width: 80px;
  text-align: right;
}
</style>
