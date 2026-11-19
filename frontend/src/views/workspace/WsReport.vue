<template>
  <PageHeader>
    <template #actions>
      <button class="btn btn--ghost" type="button" :disabled="!report" @click="toggleDone">
        <Check aria-hidden="true" />
        {{ report?.done_at ? 'Готово ✓' : 'Готово' }}
      </button>
      <button class="btn btn--accent" type="button" :disabled="!report" @click="printReport">
        <Printer aria-hidden="true" />
        Сохранить PDF
      </button>
    </template>
  </PageHeader>

  <div class="rp-tools no-print">
    <div class="rp-tools__row">
      <UiSelect
        v-model="month"
        :options="monthOptions"
        :disabled="aiBusy"
        size="sm"
        aria-label="Месяц отчёта"
        @update:model-value="load"
      />
      <button class="btn btn--ghost btn--sm" type="button" @click="brandOpen = !brandOpen">
        <Palette aria-hidden="true" />
        Оформление
      </button>
      <span v-if="saving" class="hint">Сохраняем…</span>
    </div>
    <div v-if="report" class="rp-sections" role="group" aria-label="Разделы отчёта">
      <UiSwitch
        v-for="s in report.section_list"
        :key="s.key"
        :model-value="sections[s.key]"
        @update:model-value="setSection(s.key, $event)"
      >
        {{ s.label }}
      </UiSwitch>
    </div>

    <section v-if="brandOpen" class="panel rp-brand">
      <h2>Оформление отчётов</h2>
      <p class="hint">Одно на все проекты: логотип, название и контакты в шапке каждого отчёта.</p>
      <div class="field">
        <label for="rp-brand-name">Название</label>
        <input id="rp-brand-name" v-model="brand.name" maxlength="120" placeholder="Агентство «Пример»" />
      </div>
      <div class="field">
        <label for="rp-brand-contacts">Контакты</label>
        <textarea id="rp-brand-contacts" v-model="brand.contacts" rows="2" maxlength="300" placeholder="+7 900 000-00-00 · hello@example.ru" />
      </div>
      <div class="field rp-brand__accent">
        <label for="rp-brand-accent">Цвет акцента</label>
        <input id="rp-brand-accent" v-model="brand.accent" type="color" />
        <button v-if="brand.accent" class="btn btn--ghost btn--sm" type="button" @click="brand.accent = ''">Без цвета</button>
      </div>
      <div class="rp-brand__logo">
        <img v-if="brand.logo" :src="brand.logo" alt="Логотип" />
        <label class="btn btn--ghost btn--sm">
          <Upload aria-hidden="true" />
          {{ brand.logo ? 'Заменить логотип' : 'Загрузить логотип' }}
          <input type="file" accept="image/png,image/jpeg,image/svg+xml" hidden @change="pickLogo" />
        </label>
        <button v-if="brand.logo" class="btn btn--ghost btn--sm" type="button" @click="dropLogo">Убрать</button>
        <span class="hint">PNG, JPEG или SVG до 1 МБ</span>
      </div>
      <button class="btn btn--accent btn--sm" type="button" :disabled="brandBusy" @click="saveBrand">Сохранить оформление</button>
    </section>
  </div>

  <div v-if="loading && !report" class="panel"><p class="empty">Собираем отчёт…</p></div>
  <div v-else-if="loadError" class="panel"><p class="empty">{{ loadError }}</p></div>

  <article v-else-if="report" class="report-sheet" :style="accentStyle" :class="{ 'is-loading': loading }">
    <header class="rp-head">
      <div class="rp-head__brand">
        <img v-if="report.brand.logo" :src="report.brand.logo" alt="" class="rp-head__logo" />
        <div>
          <strong v-if="report.brand.name">{{ report.brand.name }}</strong>
          <p v-if="report.brand.contacts" class="rp-head__contacts">{{ report.brand.contacts }}</p>
        </div>
      </div>
      <div class="rp-head__title">
        <h1>Отчёт за {{ report.month_label }}</h1>
        <p>
          {{ report.project }}<template v-if="report.client"> · {{ report.client }}</template>
          <template v-if="report.site"> · {{ report.site }}</template>
        </p>
        <p class="rp-muted">
          {{ formatDate(report.date_from) }} — {{ formatDate(report.date_to) }}, сравнение с: {{ report.prev_label }}
          <template v-if="report.partial"> · месяц ещё идёт, данные по вчера</template>
        </p>
      </div>
    </header>

    <section v-if="sections.totals" class="rp-block">
      <h2>Итоги месяца</h2>
      <div class="rp-totals">
        <div v-for="t in report.totals" :key="t.key" class="rp-total">
          <span class="rp-total__label">{{ TOTAL_META[t.key].label }}</span>
          <strong class="rp-total__value">{{ fmt(t.key, t.value) }}</strong>
          <span class="rp-total__prev">
            было {{ fmt(t.key, t.prev) }}
            <DeltaChip v-if="t.delta !== null" :value="t.delta" :good="TOTAL_META[t.key].good" versus="к прошлому месяцу" />
          </span>
        </div>
      </div>
      <p v-if="report.goal_name" class="rp-muted">Конверсии — цель «{{ report.goal_name }}»</p>
    </section>

    <section v-if="sections.dynamics" class="rp-block">
      <div class="rp-block__head">
        <h2>Динамика по дням</h2>
        <UiSelect v-model="chartKey" :options="chartOptions" size="sm" aria-label="Показатель графика" class="no-print" />
      </div>
      <TrendChart
        :points="report.by_day"
        :prev-points="report.prev_by_day"
        :value-key="chartKey"
        :label="TOTAL_META[chartKey].label"
        :format-value="(v) => fmt(chartKey, v)"
        :height="200"
      />
      <p class="rp-muted">Пунктир — прошлый месяц</p>
    </section>

    <section v-if="sections.campaigns" class="rp-block">
      <h2>Кампании</h2>
      <p v-if="!report.campaigns.length" class="rp-muted">За месяц кампании не тратили бюджет.</p>
      <div v-else class="table-scroll">
        <table class="table rp-table">
          <thead>
            <tr>
              <th>Кампания</th>
              <th class="num">Расход</th>
              <th class="num">Клики</th>
              <th class="num">CTR</th>
              <th class="num">Заявки</th>
              <th class="num">Цена заявки</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in report.campaigns" :key="c.campaign_row_id || c.name">
              <td>{{ c.name }}</td>
              <td class="num">
                {{ rub(c.cost_kopecks) }}
                <small v-if="c.cost_delta !== null && c.cost_delta !== undefined" class="rp-muted">{{ delta(c.cost_delta) }}</small>
              </td>
              <td class="num">{{ num(c.clicks) }}</td>
              <td class="num">{{ c.ctr == null ? '—' : pct(c.ctr) }}</td>
              <td class="num">{{ num(c.conversions || 0) }}</td>
              <td class="num">{{ c.cpa_kopecks ? rub(c.cpa_kopecks) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="sections.done" class="rp-block">
      <h2>Что сделано</h2>
      <p v-if="!report.done.length" class="rp-muted">Изменений в рекламе за месяц не записано.</p>
      <ul v-else class="rp-done">
        <li v-for="item in report.done" :key="item.id">
          <span class="rp-done__day">{{ formatDate(item.day) }}</span>
          <div>
            <strong>{{ item.title }}</strong>
            <ul v-if="item.changes.length">
              <li v-for="(line, i) in item.changes" :key="i">{{ line }}</li>
            </ul>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="sections.plan" class="rp-block">
      <h2>План на следующий месяц</h2>
      <textarea
        v-model="plan"
        class="input rp-text no-print"
        rows="4"
        maxlength="5000"
        placeholder="Что собираемся сделать и к каким цифрам идём"
        aria-label="План на следующий месяц"
        :readonly="loading"
        @input="queueSave"
      />
      <p class="print-only rp-print-text">{{ plan || '—' }}</p>
    </section>

    <section v-if="sections.comment" class="rp-block">
      <div class="rp-block__head">
        <h2>Комментарий специалиста</h2>
        <button class="btn btn--ghost btn--sm no-print" type="button" :disabled="aiBusy" @click="aiComment">
          <Sparkles aria-hidden="true" />
          {{ aiBusy ? 'Пишем…' : 'Написать комментарий с ИИ' }}
        </button>
      </div>
      <p v-if="aiBusy && aiText" class="sync-status no-print">{{ aiText }}</p>
      <textarea
        v-model="comment"
        class="input rp-text no-print"
        rows="6"
        maxlength="5000"
        placeholder="Своими словами: что получилось, что нет и почему"
        aria-label="Комментарий специалиста"
        :readonly="loading || aiBusy"
        @input="queueSave"
      />
      <p class="print-only rp-print-text">{{ comment || '—' }}</p>
    </section>
  </article>
</template>

<script setup>
import '../../styles/print.css'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Check, Palette, Printer, Sparkles, Upload } from '@lucide/vue'
import { useRoute } from 'vue-router'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import DeltaChip from '../../components/DeltaChip.vue'
import TrendChart from '../../components/TrendChart.vue'
import { errorText, jobProgressText, reportApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { askConfirm } from '../../composables/useConfirm'
import { usePageContext } from '../../composables/usePageContext'
import { aiUsageText, delta, formatDate, num, pct, rub } from '../../utils/format'
import { useWs } from './context'

const TOTAL_META = {
  cost_kopecks: { label: 'Расход', good: '' },
  clicks: { label: 'Клики', good: 'up' },
  impressions: { label: 'Показы', good: 'up' },
  ctr: { label: 'CTR', good: 'up' },
  conversions: { label: 'Заявки', good: 'up' },
  cpa_kopecks: { label: 'Цена заявки', good: 'down' },
}
const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

const { ws } = useWs()
const report = ref(null)
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const plan = ref('')
const comment = ref('')
const sections = reactive({})
const chartKey = ref('cost_kopecks')
const aiBusy = ref(false)
const aiText = ref('')
const brandOpen = ref(false)
const brandBusy = ref(false)
const brand = reactive({ name: '', contacts: '', accent: '', logo: '' })

function monthKey(d) {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

// Текущий месяц (идёт, данные по вчера) и 12 прошлых; по умолчанию — прошлый. Месяцы — по Москве
// (UTC+3, без перехода на летнее время), как у сервера: иначе к востоку от Москвы в первые часы месяца
// список предлагал бы месяц, который сервер считает «ещё не начавшимся».
const MSK_OFFSET_MS = 3 * 3600 * 1000
const monthOptions = computed(() => {
  const now = new Date(Date.now() + MSK_OFFSET_MS)
  return Array.from({ length: 13 }, (_, i) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1))
    const label = `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}${i === 0 ? ' (идёт)' : ''}`
    return { value: monthKey(d), label: label[0].toUpperCase() + label.slice(1) }
  })
})
// ?month=2026-10 — со строки «Отчёты за октябрь» на сводке.
const route = useRoute()
const asked = String(route.query.month || '')
const month = ref(monthOptions.value.some((o) => o.value === asked) ? asked : monthOptions.value[1].value)
// Месяц, чьи план и комментарий сейчас на экране. Пока грузится другой месяц, правки и сохранения идут сюда,
// а не в выбранный в списке месяц (иначе тексты прошлого месяца затёрли бы новый).
const shownMonth = ref('')

const chartOptions = ['cost_kopecks', 'clicks', 'conversions', 'cpa_kopecks'].map((key) => ({
  value: key,
  label: TOTAL_META[key].label,
}))

const accentStyle = computed(() => (report.value?.brand?.accent ? { '--rp-accent': report.value.brand.accent } : {}))

function fmt(key, value) {
  if (value === null || value === undefined) return '—'
  if (key === 'cost_kopecks' || key === 'cpa_kopecks') return rub(value)
  if (key === 'ctr') return pct(value)
  return num(value)
}

function applyBrand(b) {
  Object.assign(brand, { name: b?.name || '', contacts: b?.contacts || '', accent: b?.accent || '', logo: b?.logo || '' })
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true // поля только для чтения, пока на экране тексты прошлого месяца
  loadError.value = ''
  try {
    await flushSave()
    const asked = month.value
    const data = await reportApi.get(ws.value.id, asked)
    if (seq !== loadSeq) return // пока грузили, выбрали другой месяц — его загрузка уже идёт
    report.value = data
    shownMonth.value = asked
    plan.value = data.plan || ''
    comment.value = data.comment || ''
    for (const key of Object.keys(sections)) delete sections[key]
    Object.assign(sections, data.sections || {})
    applyBrand(data.brand)
  } catch (err) {
    if (seq === loadSeq) loadError.value = errorText(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// План и комментарий сохраняются сами через секунду после правки.
let saveTimer = null
let pending = null
function queueSave() {
  if (!shownMonth.value) return
  pending = { month: shownMonth.value, wsId: ws.value.id, plan: plan.value, comment: comment.value }
  clearTimeout(saveTimer)
  saveTimer = setTimeout(flushSave, 900)
}

async function flushSave() {
  clearTimeout(saveTimer)
  if (!pending) return
  const { wsId, ...payload } = pending
  pending = null
  saving.value = true
  try {
    await reportApi.save(wsId, payload)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    saving.value = false
  }
}

async function setSection(key, value) {
  sections[key] = value
  try {
    await reportApi.save(ws.value.id, { month: month.value, sections: { ...sections } })
  } catch (err) {
    sections[key] = !value
    pushToast(errorText(err), 'error')
  }
}

async function setDone(done) {
  const res = await reportApi.done(ws.value.id, shownMonth.value, done)
  if (report.value) report.value.done_at = res?.done_at ?? (done ? new Date().toISOString() : null)
}

async function toggleDone() {
  try {
    await setDone(!report.value.done_at)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

// Ветка от .report-sheet до <body>: печатные стили прячут всё, что не на ней (меню, шапку, панели).
let pathEls = []
function markPath() {
  clearPath()
  let el = document.querySelector('.report-sheet')?.parentElement
  while (el && el !== document.body) {
    el.classList.add('rp-path')
    pathEls.push(el)
    el = el.parentElement
  }
}
function clearPath() {
  for (const el of pathEls) el.classList.remove('rp-path')
  pathEls = []
}

async function printReport() {
  await flushSave()
  await nextTick()
  markPath()
  document.documentElement.classList.add('rp-on')
  try {
    window.print()
  } finally {
    document.documentElement.classList.remove('rp-on')
  }
  // Печать не говорит, сохранили файл или нажали «Отмена», — готовым отчёт отмечает сам человек.
  if (report.value && !report.value.done_at) {
    const forMonth = shownMonth.value
    pushToast('Сохранили PDF? Отметьте отчёт готовым.', 'info', {
      action: {
        label: 'Отметить готовым',
        run: () => {
          if (shownMonth.value === forMonth) toggleDone()
        },
      },
    })
  }
}

function onBeforePrint() {
  markPath()
  document.documentElement.classList.add('rp-on')
}
function onAfterPrint() {
  document.documentElement.classList.remove('rp-on')
}

async function aiComment() {
  if (comment.value.trim()) {
    const ok = await askConfirm('Заменить комментарий текстом от ИИ? Свой текст пропадёт.', { confirmText: 'Заменить' })
    if (!ok) return
  }
  // Месяц и проект — те, для которых попросили: результат сохраняется только туда.
  const forMonth = shownMonth.value
  const wsId = ws.value.id
  if (!forMonth) return
  aiBusy.value = true
  aiText.value = ''
  try {
    const out = await reportApi.aiComment(wsId, forMonth, (job) => {
      aiText.value = jobProgressText(job)
    })
    const text = out?.comment || ''
    if (wsId === ws.value.id && forMonth === shownMonth.value) {
      comment.value = text
      queueSave()
      await flushSave()
    } else {
      await reportApi.save(wsId, { month: forMonth, comment: text })
    }
    pushToast(out?.usage ? `Комментарий готов, проверьте его. ${aiUsageText(out.usage)}` : 'Комментарий готов, проверьте его.', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiBusy.value = false
  }
}

async function saveBrand() {
  brandBusy.value = true
  try {
    const saved = await reportApi.saveBrand({ name: brand.name, contacts: brand.contacts, accent: brand.accent })
    applyBrand(saved)
    if (report.value) report.value.brand = saved
    pushToast('Оформление сохранено', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    brandBusy.value = false
  }
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function pickLogo(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > 1_000_000) {
    pushToast('Логотип больше 1 МБ — уменьшите картинку', 'error')
    return
  }
  try {
    const saved = await reportApi.setLogo(await readFile(file))
    applyBrand(saved)
    if (report.value) report.value.brand = saved
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function dropLogo() {
  try {
    const saved = await reportApi.dropLogo()
    applyBrand(saved)
    if (report.value) report.value.brand = saved
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

onMounted(() => {
  load()
  window.addEventListener('beforeprint', onBeforePrint)
  window.addEventListener('afterprint', onAfterPrint)
})
onBeforeUnmount(() => {
  flushSave()
  window.removeEventListener('beforeprint', onBeforePrint)
  window.removeEventListener('afterprint', onAfterPrint)
  document.documentElement.classList.remove('rp-on')
  clearPath()
})

usePageContext(() => {
  const opt = monthOptions.value.find((o) => o.value === month.value)
  return { view: 'analytics', filter: opt ? `отчёт клиенту за ${opt.label.toLowerCase()}` : 'отчёт клиенту' }
})
</script>

<style scoped>
.rp-tools {
  display: grid;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.rp-tools__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.rp-sections {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.25rem;
}
.rp-brand {
  display: grid;
  gap: 0.5rem;
  max-width: 36rem;
}
.rp-brand__accent,
.rp-brand__logo {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.rp-brand__accent input[type='color'] {
  width: 3rem;
  padding: 0.1rem;
}
.rp-brand__logo img {
  max-height: 3rem;
  max-width: 10rem;
  object-fit: contain;
}

.report-sheet {
  --rp-accent: var(--accent);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 1rem;
  padding: 1.5rem;
  display: grid;
  gap: 1.5rem;
  max-width: 60rem;
}
.rp-head {
  display: grid;
  gap: 1rem;
  padding-bottom: 1rem;
  border-bottom: 3px solid var(--rp-accent);
}
.rp-head__brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.rp-head__logo {
  max-height: 3.5rem;
  max-width: 12rem;
  object-fit: contain;
}
.rp-head__contacts {
  margin: 0.15rem 0 0;
  color: var(--muted);
  white-space: pre-line;
  font-size: 0.9rem;
}
.rp-head__title h1 {
  margin: 0 0 0.25rem;
  font-size: 1.6rem;
}
.rp-head__title p {
  margin: 0.15rem 0 0;
}
.rp-muted {
  color: var(--muted);
  font-size: 0.875rem;
}
.rp-block h2 {
  margin: 0 0 0.75rem;
  font-size: 1.15rem;
}
.rp-block__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.rp-block__head h2 {
  margin: 0;
}
.rp-totals {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
  gap: 0.75rem;
}
.rp-total {
  display: grid;
  gap: 0.2rem;
  padding: 0.75rem;
  border: 1px solid var(--line);
  border-radius: 0.75rem;
}
.rp-total__label {
  color: var(--muted);
  font-size: 0.85rem;
}
.rp-total__value {
  font-size: 1.3rem;
}
.rp-total__prev {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
  color: var(--faint);
  font-size: 0.8rem;
}
.rp-table .num {
  text-align: right;
  white-space: nowrap;
}
.rp-table small {
  display: block;
}
.rp-done {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.6rem;
}
.rp-done > li {
  display: grid;
  grid-template-columns: 6.5rem 1fr;
  gap: 0.75rem;
}
.rp-done__day {
  color: var(--muted);
  font-size: 0.875rem;
}
.rp-done ul {
  margin: 0.2rem 0 0;
  padding-left: 1.1rem;
  color: var(--muted);
}
.rp-text {
  width: 100%;
  resize: vertical;
}
.rp-print-text {
  white-space: pre-wrap;
  margin: 0;
}
@media (max-width: 640px) {
  .report-sheet {
    padding: 1rem;
  }
  .rp-done > li {
    grid-template-columns: 1fr;
    gap: 0.1rem;
  }
}
</style>
