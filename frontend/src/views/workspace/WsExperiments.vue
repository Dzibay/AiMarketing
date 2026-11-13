<template>
  <PageHeader>
    <template #description>
      <UiMore page="experiments" lead="Гипотезы проверяются на тестовых копиях кампаний, образец не меняется.">
        <p>Прогресс считает сервис по загруженной статистике — ИИ подключается только по вашей кнопке.</p>
        <p>
          Деньги ограничивает только недельный бюджет тестовых кампаний в Директе — сервис сам ничего не останавливает.
          Срок считается с первого дня показов: копии сначала проходят модерацию.
        </p>
      </UiMore>
    </template>
    <template #actions>
      <RouterLink class="btn btn--accent exp-ask" :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'experiment' } }">
        <Sparkles :size="15" aria-hidden="true" />Предложить эксперимент
      </RouterLink>
    </template>
  </PageHeader>
  <FeatureLockNotice feature="experiments">
    Эксперименты не входят в ваш тариф: ИИ может предложить гипотезу, но запустить её не получится.
  </FeatureLockNotice>

  <h2 class="section-h exp-h">
    Эксперименты <span class="exp-h__n">{{ running.length }}</span>
  </h2>
  <p v-if="loading && !items.length" class="ccard ccard--note">Загружаем эксперименты…</p>
  <p v-else-if="loadError" class="ccard ccard--note">{{ loadError }}</p>
  <div v-else-if="!running.length" class="ccard exp-empty">
    <Sparkles :size="22" aria-hidden="true" />
    <p>
      Идущих экспериментов нет. Попросите ИИ предложить гипотезу — он спросит лимит в неделю, срок и критерий успеха и
      покажет план на согласование.
    </p>
  </div>
  <article v-for="e in running" :key="e.id" class="ccard exp" :class="{ 'is-due': isDue(e) }">
    <header class="exp__head">
      <div>
        <small>{{ e.kind_label }} · № {{ e.id }} · запущен {{ formatDate(e.started_on) }}</small>
        <h3>{{ e.title }}</h3>
      </div>
      <span class="st" :class="ST_TONES[e.progress?.status] || 'st--off'">{{ statusText(e) }}</span>
    </header>
    <p v-if="e.hypothesis" class="exp__hyp">{{ e.hypothesis }}</p>

    <dl class="exp__facts">
      <div>
        <dt>Критерий успеха</dt>
        <dd>{{ e.criterion_summary }}</dd>
      </div>
      <div>
        <dt>Лимит</dt>
        <dd>{{ limitText(e) }}</dd>
      </div>
      <div>
        <dt>Срок</dt>
        <dd>{{ termText(e) }}</dd>
      </div>
    </dl>

    <template v-if="e.progress">
      <div class="meter exp__meter" v-tip="`День ${e.progress.day} из ${e.days}`">
        <span :style="{ width: `${dayPct(e)}%` }"></span>
      </div>
      <p class="exp__reason" :class="REASON_TONES[e.progress.status]">{{ capitalize(e.progress.reason) }}</p>

      <div v-scroll-x class="table-scroll">
        <table class="table exp__table">
          <thead>
            <tr>
              <th>Кампания</th>
              <th class="num">Расход</th>
              <th class="num">Конверсии</th>
              <th class="num">Цена конверсии</th>
              <th v-if="e.weekly_limit_rub != null" class="num" v-tip="'С понедельника — неделя Директа'">За неделю</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in e.progress.campaigns" :key="c.id">
              <td>
                <template v-if="c.missing">[{{ c.id }}] — нет в базе</template>
                <template v-else>
                  <RouterLink :to="`/w/${ws.id}/campaigns/${c.id}`">{{ c.name }}</RouterLink>
                  <small>тестовая копия · {{ stateText(c) }}</small>
                </template>
              </td>
              <td class="num">{{ money(c.cost_kopecks) }}</td>
              <td class="num">{{ conversionsText(c) }}</td>
              <td class="num"><b>{{ money(cpaOf(c)) }}</b></td>
              <td v-if="e.weekly_limit_rub != null" class="num" :class="{ 'tone-warn': c.limit_hit }">
                {{ weekText(c, e) }}
              </td>
            </tr>
            <tr v-if="e.progress.source && !e.progress.source.missing" class="exp__base">
              <td>
                <RouterLink :to="`/w/${ws.id}/campaigns/${e.progress.source.id}`">{{ e.progress.source.name }}</RouterLink>
                <small>образец за время теста, без изменений</small>
              </td>
              <td class="num">{{ money(e.progress.source.during?.cost_kopecks) }}</td>
              <td class="num">{{ conversionsText(e.progress.source.during) }}</td>
              <td class="num"><b>{{ money(cpaOf(e.progress.source.during)) }}</b></td>
              <td v-if="e.weekly_limit_rub != null" class="num">—</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="e.progress.limit_hit" class="hint tone-warn">
        Недельный лимит почти израсходован — Директ сам притормозит показы до понедельника.
      </p>

      <details class="exp__more">
        <summary>Подробные цифры</summary>
        <div v-scroll-x class="table-scroll">
          <table class="table">
            <thead>
              <tr>
                <th>Что</th>
                <th v-for="col in COLS" :key="col.label" class="num">{{ col.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><b>Тест, всего</b></td>
                <td v-for="col in COLS" :key="col.label" class="num">
                  <b>{{ col.value(e.progress.totals) }}</b>
                </td>
              </tr>
              <template v-if="e.progress.source && !e.progress.source.missing">
                <tr>
                  <td>Образец за время теста</td>
                  <td v-for="col in COLS" :key="col.label" class="num">{{ col.value(e.progress.source.during) }}</td>
                </tr>
                <tr>
                  <td>
                    Образец до теста,
                    {{ formatDate(e.progress.source.before_from) }} — {{ formatDate(e.progress.source.before_to) }}
                  </td>
                  <td v-for="col in COLS" :key="col.label" class="num">{{ col.value(e.progress.source.before) }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p v-if="e.progress.source?.missing" class="hint">Образец [{{ e.progress.source.id }}] — нет в базе.</p>
        <p v-if="e.progress.loaded_to" class="hint">
          Статистика загружена по {{ formatDate(e.progress.loaded_to) }} включительно.
        </p>
      </details>
    </template>

    <form v-if="finishId === e.id" class="exp-finish" @submit.prevent="finish(e)">
      <div>
        <h4 class="exp-sub">Гипотеза</h4>
        <div class="seg" role="group" aria-label="Гипотеза">
          <button
            v-for="(label, id) in VERDICT_LABELS"
            :key="id"
            type="button"
            :class="{ active: verdict === id }"
            @click="verdict = id"
          >
            {{ label }}
          </button>
        </div>
      </div>
      <div class="field">
        <label :for="`exp-conclusion-${e.id}`">Вывод</label>
        <textarea
          :id="`exp-conclusion-${e.id}`"
          v-model="conclusion"
          rows="3"
          maxlength="2000"
          placeholder="Что узнали и что делать дальше"
        ></textarea>
      </div>
      <p class="hint">
        Кампании эксперимента останутся как есть: остановить или масштабировать их — отдельными действиями.
      </p>
      <div class="row-actions">
        <button class="btn btn--accent" type="submit" :disabled="!verdict || busyId === e.id">
          {{ busyId === e.id ? 'Записываем…' : 'Записать итог' }}
        </button>
        <button class="btn btn--ghost" type="button" @click="finishId = null">Не сейчас</button>
      </div>
    </form>
    <div v-else class="exp__acts">
      <button
        class="btn"
        :class="{ 'btn--accent': isDue(e) }"
        type="button"
        :disabled="reviewingId !== null"
        v-tip="'ИИ разберёт итоги и предложит, что дальше: масштабировать или остановить'"
        @click="review(e)"
      >
        <Sparkles :size="14" aria-hidden="true" />{{ reviewingId === e.id ? 'Запускаем…' : 'Разобрать итоги с ИИ' }}
      </button>
      <button class="btn btn--ghost" type="button" :disabled="busyId === e.id" @click="openFinish(e)">
        Завершить
      </button>
      <RouterLink v-if="e.chat_id" class="btn btn--ghost" :to="`/w/${ws.id}/chat/${e.chat_id}`">Чат эксперимента</RouterLink>
      <UiMenu size="sm" :label="`Действия с экспериментом «${e.title}»`" :disabled="busyId === e.id">
        <UiMenuItem :icon="CircleX" danger @click="cancel(e)">Отменить эксперимент</UiMenuItem>
      </UiMenu>
    </div>
  </article>

  <template v-if="past.length">
    <h2 class="section-h exp-h">
      Завершённые <span class="exp-h__n">{{ past.length }}</span>
      <small>ИИ видит выводы, когда предлагает новые тесты</small>
    </h2>
    <article v-for="e in past" :key="e.id" class="ccard exp exp--past">
      <header class="exp__head">
        <div>
          <small>{{ e.kind_label }} · {{ formatDate(e.started_on) }} — {{ formatDate(e.finished_at) }}</small>
          <h3>{{ e.title }}</h3>
        </div>
        <span v-if="e.status === 'cancelled'" class="st st--off">Отменён</span>
        <span v-else class="st" :class="VERDICT_TONES[e.verdict] || 'st--off'">
          Гипотеза {{ e.verdict_label || '—' }}
        </span>
      </header>
      <p v-if="e.hypothesis" class="exp__hyp">{{ e.hypothesis }}</p>
      <dl class="exp__facts">
        <div>
          <dt>Критерий успеха</dt>
          <dd>{{ e.criterion_summary }}</dd>
        </div>
        <div>
          <dt>За срок теста</dt>
          <dd>{{ totalsLine(e.progress?.totals) }}</dd>
        </div>
      </dl>
      <p v-if="e.conclusion" class="exp__verdict">
        <Check :size="16" aria-hidden="true" /><span><b>Вывод.</b> {{ e.conclusion }}</span>
      </p>
      <p class="exp__links">
        <template v-for="(c, i) in e.progress?.campaigns || []" :key="c.id">
          <template v-if="i">, </template>
          <template v-if="c.missing">[{{ c.id }}]</template>
          <RouterLink v-else :to="`/w/${ws.id}/campaigns/${c.id}`">{{ c.name }}</RouterLink>
        </template>
        <template v-if="e.chat_id">
          · <RouterLink :to="`/w/${ws.id}/chat/${e.chat_id}`">чат эксперимента</RouterLink>
        </template>
      </p>
    </article>
  </template>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Check, CircleX, Sparkles } from '@lucide/vue'
import { aiApi, errorText } from '../../api/web'
import FeatureLockNotice from '../../components/FeatureLockNotice.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiMore from '../../components/ui/UiMore.vue'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { CAMPAIGN_STATES, CAMPAIGN_STATUSES, formatDate, num, pct, plural, rub, rubExact, rubles } from '../../utils/format'
import { useWs } from './context'

// Исходы, по которым пора разбирать итоги, — как REVIEW_STATUSES на сервере.
const DUE = new Set(['success', 'fail', 'stalled', 'ready'])
const ST_TONES = { success: 'st--ok', fail: 'st--bad', stalled: 'st--bad', ready: 'st--warn' }
const REASON_TONES = { success: 'tone-ok', fail: 'tone-bad', stalled: 'tone-bad', ready: 'tone-warn' }
const VERDICT_LABELS = { success: 'Подтвердилась', fail: 'Не подтвердилась', unclear: 'Непонятно' }
const VERDICT_TONES = { success: 'st--ok', fail: 'st--bad' }
const COLS = [
  { label: 'Показы', value: (m) => num(m?.impressions) },
  { label: 'Клики', value: (m) => num(m?.clicks) },
  { label: 'Расход', value: (m) => money(m?.cost_kopecks) },
  { label: 'Конверсии', value: (m) => conversionsText(m) },
  { label: 'CR', value: (m) => (m?.cr_pct == null ? '—' : pct(m.cr_pct, 2)) },
  { label: 'CPA', value: (m) => money(m?.cpa_kopecks) },
]

const router = useRouter()
const { ws } = useWs()
const items = ref([])
const loading = ref(false)
const loadError = ref('')
const reviewingId = ref(null)
const busyId = ref(null)
const finishId = ref(null)
const verdict = ref('')
const conclusion = ref('')

const running = computed(() => items.value.filter((e) => e.status === 'running'))
const past = computed(() => items.value.filter((e) => e.status !== 'running'))

function isDue(e) {
  return DUE.has(e.progress?.status)
}

/** «Идёт · день 5 из 14» — статус прогресса и день теста. */
function statusText(e) {
  const p = e.progress
  const label = p?.label || e.status_label || 'Идёт'
  return p?.showing_since && e.days ? `${capitalize(label)} · день ${Math.min(p.day, e.days)} из ${e.days}` : capitalize(label)
}

// Цена конверсии: с сервера для итогов, для строки кампании — расход ÷ конверсии.
function cpaOf(m) {
  if (!m) return null
  if (m.cpa_kopecks != null) return m.cpa_kopecks
  return m.conversions ? Math.round(m.cost_kopecks / m.conversions) : null
}

function capitalize(text) {
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : ''
}

function days(n) {
  return `${n} ${plural(n, 'день', 'дня', 'дней')}`
}

// Схема «конверсии за ~0 ₽» живёт в копейках: такие суммы не округляем до нуля.
function money(kopecks) {
  return kopecks > 0 && kopecks < 1000 ? rubExact(kopecks) : rub(kopecks)
}

function limitText(e) {
  return e.weekly_limit_rub == null ? 'не задан' : `${rubles(e.weekly_limit_rub)} в неделю на кампанию`
}

function termText(e) {
  const p = e.progress
  if (!p?.showing_since) return `${days(e.days)} с первого дня показов`
  // days_left считает и сегодняшний день: «день 8 из 14» — это ещё 6 дней после сегодня.
  let left = ', срок вышел'
  if (p.days_left > 1) left = `, осталось ${days(p.days_left - 1)}`
  else if (p.days_left === 1) left = ', сегодня последний день'
  return `${days(e.days)}: ${formatDate(p.window_start)} — ${formatDate(p.ends_on)}${left}`
}

function dayPct(e) {
  const p = e.progress
  if (!p?.showing_since || !e.days) return 0
  return Math.min(100, Math.round((100 * p.day) / e.days))
}

function stateText(c) {
  if (['DRAFT', 'MODERATION', 'REJECTED'].includes(c.status)) return CAMPAIGN_STATUSES[c.status]
  return CAMPAIGN_STATES[c.state] || c.state || '—'
}

// Платные — по целям, за которые кампания платит; null — эти цели не загружены.
function conversionsText(m) {
  if (!m) return '—'
  const paid = m.paid_conversions
  if (paid == null || paid === m.conversions) return num(m.conversions)
  return `${num(m.conversions)} (платных ${num(paid)})`
}

function weekText(c, e) {
  if (c.week_cost_kopecks == null) return '—'
  return `${money(c.week_cost_kopecks)} из ${rubles(e.weekly_limit_rub)}`
}

function totalsLine(t) {
  if (!t) return '—'
  const parts = [`показы ${num(t.impressions)}`, `клики ${num(t.clicks)}`, `расход ${money(t.cost_kopecks)}`]
  parts.push(`конверсии ${conversionsText(t)}`)
  if (t.cr_pct != null) parts.push(`CR ${pct(t.cr_pct, 2)}`)
  if (t.cpa_kopecks != null) parts.push(`CPA ${money(t.cpa_kopecks)}`)
  return parts.join(', ')
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await aiApi.experiments(ws.value.id)
    items.value = data.items || []
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

/** Разбор итогов — ИИ запускается только здесь, по кнопке; дальше разговор в чате. */
async function review(e) {
  reviewingId.value = e.id
  try {
    const res = await aiApi.reviewExperiment(ws.value.id, e.id)
    await router.push(`/w/${ws.value.id}/chat/${res.chat.id}`)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    reviewingId.value = null
  }
}

function openFinish(e) {
  finishId.value = e.id
  const status = e.progress?.status
  verdict.value = status === 'success' || status === 'fail' ? status : ''
  conclusion.value = ''
}

async function submit(e, payload, done) {
  busyId.value = e.id
  try {
    const data = await aiApi.finishExperiment(ws.value.id, e.id, payload)
    items.value = data.items || []
    finishId.value = null
    pushToast(done, 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

function finish(e) {
  if (!verdict.value) return
  submit(e, { verdict: verdict.value, conclusion: conclusion.value }, 'Итог записан')
}

async function cancel(e) {
  const ok = await askConfirm(
    `Отменить эксперимент «${e.title}»? Тестовые кампании останутся как есть — остановить их можно в списке кампаний.`,
    { confirmText: 'Отменить эксперимент', cancelText: 'Не отменять', danger: true },
  )
  if (ok) submit(e, { cancel: true }, 'Эксперимент отменён')
}

onMounted(load)
</script>

<style scoped>
.exp-ask {
  gap: 6px;
}
.exp-h {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.exp-h__n {
  color: var(--faint);
  font-weight: 600;
}
.exp-h small {
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}
.exp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 24px 18px;
  color: var(--muted);
  text-align: center;
}
.exp-empty p {
  max-width: 520px;
  margin: 0;
}
.exp {
  display: grid;
  gap: 12px;
  margin-bottom: 12px;
  padding: 18px;
}
.exp.is-due {
  border-color: var(--ink);
}
.exp__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.exp__head small {
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
}
.exp__head h3 {
  margin: 2px 0 0;
  font-size: 17px;
  font-weight: 800;
}
.exp__head .st {
  flex-shrink: 0;
}
.exp__hyp {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.exp__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px 16px;
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--surface-2);
}
.exp__facts dt {
  margin: 0 0 2px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.exp__facts dd {
  margin: 0;
  font-size: 14px;
  line-height: 1.45;
}
.exp__meter {
  margin: 0;
}
.exp__reason {
  margin: -4px 0 0;
  font-size: 14px;
}
.exp__table td small {
  display: block;
  color: var(--faint);
  font-size: 12px;
}
.exp__base td {
  background: var(--surface-2);
}
.exp__more summary {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.exp__more[open] summary {
  margin-bottom: 8px;
}
.exp__more .hint {
  margin: 8px 0 0;
}
.exp__acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.exp__acts .btn {
  gap: 6px;
}
.exp__verdict {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--lime-soft);
  box-shadow: inset 0 0 0 1px var(--lime-line);
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.exp__verdict svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.exp__links {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 640px) {
  .exp__head {
    flex-direction: column;
  }
  .exp-h small {
    margin-left: 0;
    flex-basis: 100%;
  }
}
</style>
