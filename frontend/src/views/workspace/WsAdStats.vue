<template>
  <div class="ws-toolbar">
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
          {{ c.name }}
        </label>
      </details>
      <p class="sync-status">
        <template v-if="busyText">{{ busyText }}</template>
        <template v-else-if="report">
          {{ formatDate(report.period.from) }} — {{ formatDate(report.period.to) }}
          <template v-if="report.missing"> · {{ report.missing }} объявл. нет в кабинете (удалены)</template>
        </template>
        <template v-else>Статистика объявлений по поиску и сетям, слабые по CTR — на переписывание.</template>
      </p>
    </div>

    <p class="notice">
      Слабое объявление — CTR ниже 70 % от соседей по группе в той же сети (нет соседей — от кампании) или на поиске
      ниже 1 % на 500+ показах. Судим от 200 показов на поиске и 1000 в сетях. ИИ пишет новый вариант, он добавляется в
      ту же группу отдельным объявлением, старое не меняется — через пару недель сравните CTR и остановите слабое.
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
      <li v-for="e in doneErrors" :key="e.ad_row_id">Объявление {{ e.ad_row_id }}: {{ e.message }}</li>
    </ul>

    <div v-if="!report && !busy" class="empty">Выберите кампании и период и постройте отчёт.</div>

    <template v-else-if="report">
      <div class="stats-grid">
        <div class="stat-card">
          <small>Объявлений со статистикой</small>
          <strong>{{ num(rows.length) }}</strong>
          <span class="cell-note">расход {{ rub(sumTotal(rows, 'cost_kopecks')) }}</span>
        </div>
        <div class="stat-card">
          <small>Слабые по CTR</small>
          <strong :class="{ 'tone-bad': weak.length }">{{ num(weak.length) }}</strong>
          <span class="cell-note">расход {{ rub(sumTotal(weak, 'cost_kopecks')) }}</span>
        </div>
        <div class="stat-card">
          <small>CTR поиска</small>
          <strong>{{ netCtr('search') }}</strong>
          <span class="cell-note">по всем объявлениям</span>
        </div>
        <div class="stat-card">
          <small>CTR сетей</small>
          <strong>{{ netCtr('network') }}</strong>
          <span class="cell-note">по всем объявлениям</span>
        </div>
      </div>

      <div class="filters">
        <div class="seg seg--sm" role="group" aria-label="Фильтр объявлений">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            type="button"
            :class="{ active: filter === f.key }"
            @click="filter = f.key"
          >
            {{ f.label }}
          </button>
        </div>
        <input v-model.trim="search" type="search" placeholder="поиск по тексту или группе" />
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="!weak.length" @click="selectWeak">Отметить слабые</button>
          <button
            class="btn btn--ghost"
            type="button"
            :disabled="!!busy || !selected.size"
            :title="`ИИ предложит новые тексты для выбранных (до ${AI_MAX}). Ничего не меняет`"
            @click="askAi"
          >
            {{ busy === 'ai' ? 'ИИ пишет…' : `Переписать с ИИ (${Math.min(selected.size, AI_MAX)})` }}
          </button>
        </div>
      </div>
      <p v-if="aiNote" class="cell-note">{{ aiNote }}</p>

      <div v-if="draftIds.length" class="ad-drafts">
        <div class="filters">
          <strong>Новые варианты: {{ usedDrafts.length }} из {{ draftIds.length }}</strong>
          <label class="ad-moderate">
            <input v-model="moderate" type="checkbox" />
            Сразу на модерацию
          </label>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="clearDrafts">Сбросить</button>
            <button
              class="btn btn--accent"
              type="button"
              :disabled="!!busy || !usedDrafts.length || usedDrafts.some((d) => draftProblem(d))"
              @click="createAb"
            >
              {{ busy === 'ab' ? 'Добавляем…' : `Добавить как новые объявления (${usedDrafts.length})` }}
            </button>
          </div>
        </div>
        <article v-for="id in draftIds" :key="id" class="ad-draft" :class="{ 'is-off': !drafts[id].use }">
          <header>
            <label>
              <input v-model="drafts[id].use" type="checkbox" />
              <strong>{{ byId[id]?.adgroup_name }}</strong>
            </label>
            <small class="cell-note">{{ byId[id]?.campaign_name }}</small>
          </header>
          <div class="ad-draft__cols">
            <div>
              <small class="cell-note">Сейчас</small>
              <p class="ad-text">
                <b>{{ (byId[id]?.titles || []).join(' | ') }}</b>
                <template v-if="byId[id]?.title2"> — {{ byId[id].title2 }}</template>
              </p>
              <p class="ad-text">{{ (byId[id]?.texts || []).join(' | ') }}</p>
              <small v-if="drafts[id].why" class="cell-note">ИИ: {{ drafts[id].why }}</small>
            </div>
            <div class="ad-draft__edit">
              <label>
                <small class="cell-note">
                  Заголовки — по одному в строке, до {{ LIMITS.title }} символов{{ isResponsive(id) ? ', до 7' : '' }}
                </small>
                <textarea v-model="drafts[id].titles" :rows="isResponsive(id) ? 4 : 1" />
              </label>
              <label v-if="!isResponsive(id)">
                <small class="cell-note">Второй заголовок, до {{ LIMITS.title2 }}</small>
                <input v-model="drafts[id].title2" type="text" />
              </label>
              <label>
                <small class="cell-note">
                  Тексты — по одному в строке, до {{ LIMITS.text }} символов{{ isResponsive(id) ? ', до 3' : '' }}
                </small>
                <textarea v-model="drafts[id].texts" :rows="isResponsive(id) ? 3 : 2" />
              </label>
              <small v-if="draftProblem(drafts[id])" class="tone-bad">{{ draftProblem(drafts[id]) }}</small>
            </div>
          </div>
        </article>
      </div>

      <div class="table-scroll" :class="{ 'is-loading': busy === 'report' }">
        <table class="table">
          <thead>
            <tr>
              <th class="check-col">
                <input
                  type="checkbox"
                  :checked="shown.length > 0 && shown.every((r) => !r.rewritable || selected.has(r.ad_row_id))"
                  aria-label="Выбрать все"
                  @change="toggleAll($event.target.checked)"
                />
              </th>
              <th>Объявление</th>
              <th class="num">Поиск: показы</th>
              <th class="num">клики</th>
              <th class="num">CTR</th>
              <th class="num">Сети: показы</th>
              <th class="num">клики</th>
              <th class="num">CTR</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th class="num">CPA</th>
              <th>Почему</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shown" :key="r.ad_row_id">
              <td class="check-col">
                <input
                  type="checkbox"
                  :disabled="!r.rewritable"
                  :title="r.rewritable ? '' : 'Переписать можно только текстовые и комбинаторные объявления'"
                  :checked="selected.has(r.ad_row_id)"
                  @change="toggle(r.ad_row_id, $event.target.checked)"
                />
              </td>
              <td class="ad-cell">
                <b>{{ r.title || '—' }}</b>
                <span v-if="r.titles.length > 1" class="chip">+{{ r.titles.length - 1 }} загол.</span>
                <span v-if="STATES[r.state]" class="chip">{{ STATES[r.state] }}</span>
                <span v-if="created.has(r.ad_row_id)" class="chip chip--ok">есть новый вариант</span>
                <small class="cell-note">{{ r.text }}</small>
                <small class="cell-note">{{ r.campaign_name }} · {{ r.adgroup_name }}</small>
              </td>
              <template v-for="net in ['search', 'network']" :key="net">
                <td class="num">{{ r.nets[net] ? num(r.nets[net].impressions) : '—' }}</td>
                <td class="num">{{ r.nets[net] ? num(r.nets[net].clicks) : '—' }}</td>
                <td class="num" :class="{ 'tone-bad': r.nets[net]?.low }">
                  {{ r.nets[net]?.ctr != null ? pct(r.nets[net].ctr, 2) : '—' }}
                  <small v-if="r.nets[net]?.ref_ctr != null" class="cell-note">соседи {{ pct(r.nets[net].ref_ctr, 2) }}</small>
                </td>
              </template>
              <td class="num">{{ rub(r.total.cost_kopecks) }}</td>
              <td class="num">{{ num(r.total.conversions) }}</td>
              <td class="num">{{ r.total.cpa_kopecks != null ? rub(r.total.cpa_kopecks) : '—' }}</td>
              <td class="slice-why">
                <small v-for="(why, i) in reasons(r)" :key="i" class="cell-note">{{ why }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="!shown.length" class="empty">Нет объявлений под фильтр.</div>
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

const AI_MAX = 20
const LIMITS = { title: 56, title2: 30, text: 81, word: 22 }
const NET_NAMES = { search: 'поиск', network: 'сети' }
const STATES = { SUSPENDED: 'остановлено', OFF: 'выключено', ARCHIVED: 'в архиве' }
const FILTERS = [
  { key: 'weak', label: 'Слабые' },
  { key: 'all', label: 'Все' },
]

const { ws } = useWs()

const campaigns = ref([])
const picked = ref(new Set())
const days = ref(30)
const report = ref(null)
const busy = ref('')
const busyText = ref('')
const loadError = ref('')
const done = ref('')
const doneErrors = ref([])
const selected = ref(new Set())
const created = ref(new Set())
const filter = ref('weak')
const search = ref('')
const aiNote = ref('')
const moderate = ref(false)
const drafts = reactive({})

const campaignIds = computed(() => campaigns.value.map((c) => c.campaign_row_id).filter((id) => picked.value.has(id)))
const rows = computed(() => report.value?.ads || [])
const byId = computed(() => Object.fromEntries(rows.value.map((r) => [r.ad_row_id, r])))
const weak = computed(() => rows.value.filter((r) => r.low))
const shown = computed(() => {
  const q = search.value.toLowerCase()
  return (filter.value === 'weak' ? weak.value : rows.value).filter(
    (r) =>
      !q ||
      [r.adgroup_name, r.campaign_name, ...r.titles, ...r.texts].some((s) => (s || '').toLowerCase().includes(q)),
  )
})
const draftIds = computed(() => Object.keys(drafts).map(Number))
const usedDrafts = computed(() => draftIds.value.filter((id) => drafts[id].use).map((id) => drafts[id]))

function sumTotal(list, key) {
  return list.reduce((s, r) => s + (r.total[key] || 0), 0)
}

function netCtr(net) {
  let imp = 0
  let clicks = 0
  for (const r of rows.value) {
    imp += r.nets[net]?.impressions || 0
    clicks += r.nets[net]?.clicks || 0
  }
  return imp ? pct((clicks / imp) * 100, 2) : '—'
}

function reasons(r) {
  return Object.entries(r.nets).flatMap(([net, n]) => (n.reasons || []).map((why) => `${NET_NAMES[net]}: ${why}`))
}

function isResponsive(id) {
  return byId.value[id]?.type === 'RESPONSIVE_AD'
}

function lines(text) {
  return String(text || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}

function draftProblem(d) {
  const titles = lines(d.titles)
  const texts = lines(d.texts)
  const responsive = isResponsive(d.ad_row_id)
  if (!titles.length || !texts.length) return 'Нужен хотя бы один заголовок и один текст'
  if (titles.length > (responsive ? 7 : 1)) return responsive ? 'Заголовков — не больше 7' : 'Заголовок — одной строкой'
  if (texts.length > (responsive ? 3 : 1)) return responsive ? 'Текстов — не больше 3' : 'Текст — одной строкой'
  if (titles.some((t) => t.length > LIMITS.title)) return `Заголовок длиннее ${LIMITS.title} символов`
  if (texts.some((t) => t.length > LIMITS.text)) return `Текст длиннее ${LIMITS.text} символов`
  if ((d.title2 || '').trim().length > LIMITS.title2) return `Второй заголовок длиннее ${LIMITS.title2} символов`
  if ([...titles, ...texts, d.title2 || ''].some((s) => s.split(/\s+/).some((w) => w.length > LIMITS.word)))
    return `Слово длиннее ${LIMITS.word} символов — Директ не примет`
  return ''
}

function pick(id, on) {
  const next = new Set(picked.value)
  on ? next.add(id) : next.delete(id)
  picked.value = next
}

function pickAll(on) {
  picked.value = new Set(on ? campaigns.value.map((c) => c.campaign_row_id) : [])
}

function toggle(id, on) {
  const next = new Set(selected.value)
  on ? next.add(id) : next.delete(id)
  selected.value = next
}

function toggleAll(on) {
  const next = new Set(selected.value)
  for (const r of shown.value) {
    if (!r.rewritable) continue
    on ? next.add(r.ad_row_id) : next.delete(r.ad_row_id)
  }
  selected.value = next
}

function selectWeak() {
  selected.value = new Set(weak.value.filter((r) => r.rewritable && !created.value.has(r.ad_row_id)).map((r) => r.ad_row_id))
}

function clearDrafts() {
  for (const k of Object.keys(drafts)) delete drafts[k]
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
    report.value = await wsApi.adReport(ws.value.id, { days: days.value, campaign_ids: campaignIds.value }, (job) => {
      busyText.value = `Отчёт: ${jobProgressText(job)}`
    })
    selected.value = new Set()
    created.value = new Set()
    aiNote.value = ''
    clearDrafts()
    if (!weak.value.length) filter.value = 'all'
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

function aiItem(r) {
  // Сеть для промпта: где объявление слабое, иначе — где больше показов.
  const nets = Object.entries(r.nets)
  const [net, n] = nets.find(([, x]) => x.low) || nets.sort((a, b) => b[1].impressions - a[1].impressions)[0] || []
  return {
    ad_row_id: r.ad_row_id,
    net: net || '',
    ctr: n?.ctr ?? null,
    ref_ctr: n?.ref_ctr ?? null,
    best_ad_row_id: n?.best_ad_row_id ?? null,
  }
}

async function askAi() {
  const list = rows.value.filter((r) => selected.value.has(r.ad_row_id) && r.rewritable).slice(0, AI_MAX)
  if (!list.length) return
  busy.value = 'ai'
  busyText.value = 'ИИ пишет тексты…'
  try {
    const res = await wsApi.adsAi(ws.value.id, { items: list.map(aiItem) }, (job) => {
      busyText.value = jobProgressText(job)
    })
    const got = res.suggestions || {}
    for (const [id, s] of Object.entries(got)) {
      drafts[id] = {
        ad_row_id: Number(id),
        titles: s.titles.join('\n'),
        texts: s.texts.join('\n'),
        title2: s.title2 || '',
        why: s.why || '',
        use: true,
      }
    }
    const n = Object.keys(got).length
    aiNote.value = [
      `ИИ предложил новые тексты: ${n} из ${list.length}`,
      res.usage ? usageText(res.usage) : '',
      res.error ? `часть не разобрана: ${res.error}` : '',
    ]
      .filter(Boolean)
      .join(' · ')
    if (!n) pushToast('ИИ не предложил годных текстов — попробуйте ещё раз', 'warn')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function createAb() {
  const list = usedDrafts.value
  const ok = await askConfirm(
    `Добавить ${list.length} ${plural(list.length, 'новое объявление', 'новых объявления', 'новых объявлений')} ` +
      'в те же группы с той же ссылкой? Старые объявления не меняются. ' +
      (moderate.value ? 'Новые сразу уйдут на модерацию.' : 'Новые останутся черновиками.') +
      ' Удалить их можно откатом в журнале.',
    { confirmText: 'Добавить' },
  )
  if (!ok) return
  busy.value = 'ab'
  busyText.value = 'Добавляем объявления…'
  doneErrors.value = []
  try {
    const res = await wsApi.adsAb(
      ws.value.id,
      {
        moderate: moderate.value,
        items: list.map((d) => ({
          ad_row_id: d.ad_row_id,
          titles: lines(d.titles),
          texts: lines(d.texts),
          title2: (d.title2 || '').trim(),
        })),
      },
      (job) => {
        busyText.value = `Объявления: ${jobProgressText(job)}`
      },
    )
    const n = res.created.length
    done.value =
      `Добавлено ${n} ${plural(n, 'объявление', 'объявления', 'объявлений')}` +
      (moderate.value ? `, на модерацию — ${res.moderated}.` : ' черновиками.')
    doneErrors.value = res.errors || []
    pushToast(done.value, res.errors?.length || res.moderate_errors?.length ? 'warn' : 'ok')
    for (const e of res.moderate_errors || []) pushToast(`Модерация: ${e}`, 'warn')
    const next = new Set(created.value)
    for (const a of res.created) {
      next.add(a.source_ad_row_id)
      delete drafts[a.source_ad_row_id]
    }
    created.value = next
    selected.value = new Set()
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
  max-width: 280px;
}
.slice-why .cell-note,
.ad-cell .cell-note {
  display: block;
}
.ad-cell {
  min-width: 260px;
  max-width: 420px;
}
.ad-moderate {
  display: flex;
  gap: 6px;
  align-items: center;
}
.ad-drafts {
  display: grid;
  gap: 12px;
  margin: 12px 0;
}
.ad-draft {
  border: 1px solid var(--line, #ddd);
  border-radius: 12px;
  padding: 12px 14px;
}
.ad-draft.is-off {
  opacity: 0.55;
}
.ad-draft header {
  display: flex;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}
.ad-draft header label {
  display: flex;
  gap: 8px;
  align-items: center;
}
.ad-draft__cols {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 16px;
}
.ad-draft__edit {
  display: grid;
  gap: 8px;
}
.ad-draft__edit label {
  display: grid;
  gap: 4px;
}
.ad-draft__edit textarea,
.ad-draft__edit input {
  width: 100%;
}
.ad-text {
  margin: 4px 0;
}
@media (max-width: 760px) {
  .ad-draft__cols {
    grid-template-columns: 1fr;
  }
}
</style>
