<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Эксперименты</h2>
        <p class="hint">
          Гипотезы, которые ИИ проверяет на тестовых кампаниях: перезапуск копией, новое гео, другие ключи или тип
          кампании. Прогресс считает код по загруженной статистике — ИИ подключается только по вашей кнопке.
        </p>
      </div>
      <RouterLink class="btn btn--accent" :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'experiment' } }">
        Предложить эксперимент
      </RouterLink>
    </div>
    <p class="notice">
      Деньги ограничивает только недельный бюджет тестовых кампаний в Директе — сервис сам ничего не останавливает. Срок
      считается с первого дня показов: копии сначала проходят модерацию.
    </p>

    <p v-if="loading && !items.length" class="empty">Загружаем эксперименты…</p>
    <p v-else-if="loadError" class="empty">{{ loadError }}</p>
    <p v-else-if="!running.length" class="empty">
      Идущих экспериментов нет. Попросите ИИ предложить гипотезу — он спросит лимит в неделю, срок и критерий успеха и
      покажет план на согласование.
    </p>
    <div v-else class="exp-list">
      <article v-for="e in running" :key="e.id" class="exp-card" :class="{ 'is-due': isDue(e) }">
        <header class="exp-card__head">
          <div>
            <strong>{{ e.title }}</strong>
            <small>
              <span class="chip chip--info">{{ e.kind_label }}</span>
              <span class="chip" :class="PROGRESS_CHIPS[e.progress?.status]">{{ e.progress?.label || e.status_label }}</span>
              № {{ e.id }} · запущен {{ formatDate(e.started_on) }}
            </small>
          </div>
          <div class="row-actions">
            <button
              class="btn"
              :class="isDue(e) ? 'btn--accent' : 'btn--ghost'"
              type="button"
              :disabled="reviewingId !== null"
              title="ИИ разберёт итоги и предложит, что дальше: масштабировать или остановить"
              @click="review(e)"
            >
              {{ reviewingId === e.id ? 'Запускаем…' : 'Разобрать итоги с ИИ' }}
            </button>
            <button class="btn btn--ghost" type="button" :disabled="busyId === e.id" @click="openFinish(e)">
              Завершить
            </button>
            <button class="btn btn--ghost btn--danger" type="button" :disabled="busyId === e.id" @click="cancel(e)">
              Отменить
            </button>
            <RouterLink v-if="e.chat_id" class="btn btn--ghost" :to="`/w/${ws.id}/chat/${e.chat_id}`">Чат</RouterLink>
          </div>
        </header>

        <p v-if="e.hypothesis" class="exp-text">{{ e.hypothesis }}</p>

        <dl class="exp-facts">
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
          <div class="exp-term">
            <div class="meter" :title="`День ${e.progress.day} из ${e.days}`">
              <span :style="{ width: `${dayPct(e)}%` }"></span>
            </div>
            <p :class="REASON_TONES[e.progress.status]">{{ capitalize(e.progress.reason) }}</p>
            <small v-if="e.progress.loaded_to" class="hint">
              Статистика загружена по {{ formatDate(e.progress.loaded_to) }} включительно.
            </small>
          </div>

          <div class="metric-grid">
            <div v-for="col in COLS" :key="col.label" class="metric-grid__item is-static">
              <small>{{ col.label }}</small>
              <strong>{{ col.value(e.progress.totals) }}</strong>
            </div>
          </div>

          <div class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th>Тестовая кампания</th>
                  <th>Статус</th>
                  <th class="num">Показы</th>
                  <th class="num">Клики</th>
                  <th class="num">Расход</th>
                  <th class="num">Конверсии</th>
                  <th v-if="e.weekly_limit_rub != null" class="num" title="С понедельника — неделя Директа">
                    За неделю
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in e.progress.campaigns" :key="c.id">
                  <td>
                    <template v-if="c.missing">[{{ c.id }}] — нет в базе</template>
                    <RouterLink v-else :to="`/w/${ws.id}/campaigns/${c.id}`">{{ c.name }}</RouterLink>
                  </td>
                  <td>{{ c.missing ? '—' : stateText(c) }}</td>
                  <td class="num">{{ num(c.impressions) }}</td>
                  <td class="num">{{ num(c.clicks) }}</td>
                  <td class="num">{{ money(c.cost_kopecks) }}</td>
                  <td class="num">{{ conversionsText(c) }}</td>
                  <td v-if="e.weekly_limit_rub != null" class="num" :class="{ 'tone-warn': c.limit_hit }">
                    {{ weekText(c, e) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="e.progress.limit_hit" class="hint tone-warn">
            Недельный лимит почти израсходован — Директ сам притормозит показы до понедельника.
          </p>

          <div v-if="e.progress.source" class="exp-source">
            <h4 class="exp-sub">
              Образец:
              <template v-if="e.progress.source.missing">[{{ e.progress.source.id }}] — нет в базе</template>
              <RouterLink v-else :to="`/w/${ws.id}/campaigns/${e.progress.source.id}`">
                «{{ e.progress.source.name }}»
              </RouterLink>
            </h4>
            <div v-if="!e.progress.source.missing" class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>Что</th>
                    <th v-for="col in COLS" :key="col.label" class="num">{{ col.label }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      Образец до теста,
                      {{ formatDate(e.progress.source.before_from) }} — {{ formatDate(e.progress.source.before_to) }}
                    </td>
                    <td v-for="col in COLS" :key="col.label" class="num">{{ col.value(e.progress.source.before) }}</td>
                  </tr>
                  <tr>
                    <td>Образец за время теста</td>
                    <td v-for="col in COLS" :key="col.label" class="num">{{ col.value(e.progress.source.during) }}</td>
                  </tr>
                  <tr>
                    <td><strong>Тест</strong></td>
                    <td v-for="col in COLS" :key="col.label" class="num">
                      <strong>{{ col.value(e.progress.totals) }}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
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
      </article>
    </div>
  </section>

  <section v-if="past.length" class="panel">
    <div class="panel__head">
      <div>
        <h2>Завершённые</h2>
        <p class="hint">Итоги и выводы. ИИ видит их, когда разбирает пространство и предлагает новые тесты.</p>
      </div>
    </div>
    <div class="list">
      <details v-for="e in past" :key="e.id" class="log-row">
        <summary class="list-row">
          <div>
            <strong>{{ e.title }}</strong>
            <small>
              <span class="chip chip--info">{{ e.kind_label }}</span>
              {{ formatDate(e.started_on) }} — {{ formatDate(e.finished_at) }}
            </small>
          </div>
          <div class="log-row__side">
            <span v-if="e.status === 'cancelled'" class="chip chip--info">отменён</span>
            <span v-else class="chip" :class="VERDICT_CHIPS[e.verdict]">гипотеза {{ e.verdict_label || '—' }}</span>
          </div>
        </summary>
        <div class="log-row__body">
          <div>
            <h4>Гипотеза</h4>
            <p class="exp-text">{{ e.hypothesis || '—' }}</p>
            <h4>Критерий успеха</h4>
            <p class="exp-text">{{ e.criterion_summary }}</p>
          </div>
          <div>
            <h4>Вывод</h4>
            <p class="exp-text">{{ e.conclusion || '—' }}</p>
            <h4>За срок теста</h4>
            <p class="exp-text">{{ totalsLine(e.progress?.totals) }}</p>
            <p class="exp-text">
              <template v-for="(c, i) in e.progress?.campaigns || []" :key="c.id">
                <template v-if="i">, </template>
                <template v-if="c.missing">[{{ c.id }}]</template>
                <RouterLink v-else :to="`/w/${ws.id}/campaigns/${c.id}`">{{ c.name }}</RouterLink>
              </template>
              <template v-if="e.chat_id">
                · <RouterLink :to="`/w/${ws.id}/chat/${e.chat_id}`">чат эксперимента</RouterLink>
              </template>
            </p>
          </div>
        </div>
      </details>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { aiApi, errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { CAMPAIGN_STATES, CAMPAIGN_STATUSES, formatDate, num, pct, plural, rub, rubExact, rubles } from '../../utils/format'
import { useWs } from './context'

// Исходы, по которым пора разбирать итоги, — как REVIEW_STATUSES на сервере.
const DUE = new Set(['success', 'fail', 'stalled', 'ready'])
const PROGRESS_CHIPS = { success: 'chip--ok', fail: 'chip--bad', stalled: 'chip--bad', ready: 'chip--warning', waiting: 'chip--info', no_data: 'chip--info' }
const REASON_TONES = { success: 'tone-ok', fail: 'tone-bad', stalled: 'tone-bad', ready: 'tone-warn' }
const VERDICT_LABELS = { success: 'Подтвердилась', fail: 'Не подтвердилась', unclear: 'Непонятно' }
const VERDICT_CHIPS = { success: 'chip--ok', fail: 'chip--bad', unclear: 'chip--info' }
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
