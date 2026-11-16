<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <button class="btn btn--primary btn--sm" type="button" :disabled="loading" @click="check">
        {{ loading ? 'Проверяем…' : 'Проверить' }}
      </button>
      <span v-if="data" class="adm-dash__versus">
        Проверено в {{ mskTime(data.generated_at) }}<template v-if="data.version"> · версия {{ data.version }}</template>
      </span>
    </div>

    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>
    <template v-else>
      <section class="panel">
        <div class="panel__head"><h2>Сервисы</h2></div>
        <ul class="adm-sys__services">
          <li v-for="s in data.services" :key="s.key">
            <span class="sdot" :class="dot(s)"></span>
            <RouterLink v-if="SETTINGS[s.key]" :to="SETTINGS[s.key]">{{ s.label }}</RouterLink>
            <span v-else>{{ s.label }}</span>
            <small class="cell-note">{{ serviceNote(s) }}</small>
          </li>
        </ul>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>Очередь задач</h2>
          <span class="adm-charts__total">ждут {{ num(waiting) }} · выполняются {{ num(running) }}</span>
        </div>
        <p v-if="q.oldest" class="hint" :class="{ 'tone-warn': q.oldest.lag_minutes >= q.stuck_minutes }">
          Старейшая ждёт {{ n(q.oldest.lag_minutes, 'минуту', 'минуты', 'минут') }}: {{ q.oldest.kind }} № {{ q.oldest.id }}
          <template v-if="q.oldest.email"> ({{ q.oldest.email }})</template>, попыток {{ num(q.oldest.attempts) }}<template v-if="q.oldest.error"> — {{ q.oldest.error }}</template>
        </p>
        <p v-if="!q.kinds.length" class="empty">Очередь пуста, за сутки задач не было.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Вид</th>
                <th class="num">Ждут</th>
                <th class="num">Позже</th>
                <th class="num">Выполняются</th>
                <th class="num">Готово за сутки</th>
                <th class="num">Ошибок за сутки</th>
                <th class="num">Ожидание</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="k in q.kinds" :key="k.kind">
                <td>{{ k.kind }}</td>
                <td class="num">{{ num(k.waiting) }}</td>
                <td class="num">{{ num(k.later) }}</td>
                <td class="num">{{ num(k.running) }}</td>
                <td class="num">{{ num(k.done) }}</td>
                <td class="num" :class="{ 'tone-bad': k.failed }">{{ num(k.failed) }}</td>
                <td class="num" :class="{ 'tone-warn': k.lag_minutes >= q.stuck_minutes }">{{ k.waiting ? `${num(k.lag_minutes)} мин` : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head"><h2>Расписания</h2></div>
        <p class="hint">Просрочено — с последнего запуска прошло больше двух интервалов: планировщик стоит или падает.</p>
        <div v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr><th>Что</th><th>Как часто</th><th>Последний запуск</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="s in data.schedules" :key="s.name">
                <td>{{ SCHEDULE_LABELS[s.name] || s.name }} <small class="cell-note">· {{ s.name }}</small></td>
                <td>{{ every(s.interval_seconds) }}</td>
                <td>{{ s.last_run_at ? formatDateTime(s.last_run_at) : 'не запускалось' }}</td>
                <td><span class="chip" :class="s.late ? 'chip--warning' : 'chip--ok'">{{ s.late ? 'просрочено' : 'в срок' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>Свежие сбои</h2>
          <nav class="seg seg--sm" aria-label="Вид сбоев">
            <button v-for="e in ERROR_TABS" :key="e.key" type="button" :class="{ active: errTab === e.key }" @click="errTab = e.key">
              {{ e.label }} · {{ num(data.errors[e.key].length) }}
            </button>
          </nav>
        </div>
        <p v-if="!errors.length" class="empty">Сбоев нет.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr><th>Когда</th><th>Что</th><th>Кто</th><th>Ошибка</th></tr>
            </thead>
            <tbody>
              <tr v-for="(e, i) in errors" :key="e.id || i">
                <td>{{ e.at ? formatDateTime(e.at) : '—' }}</td>
                <td>
                  <template v-if="errTab === 'ai'">
                    {{ e.task }}<small class="cell-note"> · {{ e.model || 'без модели' }}{{ e.landing ? ' · лендинг' : '' }}</small>
                    <span v-if="e.answered" class="chip chip--ok" v-tip="'Запасная модель ответила'">ответ был</span>
                  </template>
                  <template v-else>{{ e.kind }}<small v-if="e.attempts" class="cell-note"> · попыток {{ e.attempts }}</small></template>
                </td>
                <td>
                  <RouterLink v-if="e.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: e.user_id } }">{{ e.email || `#${e.user_id}` }}</RouterLink>
                  <span v-else class="cell-note">—</span>
                </td>
                <td class="adm-costs__wrap tone-bad">
                  <small v-if="e.code" class="cell-note">{{ e.code }} · </small>{{ e.error || e.message || '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>База данных</h2>
          <span class="adm-charts__total">{{ bytes(db.size_bytes) }}</span>
        </div>
        <p class="hint">PostgreSQL {{ db.postgres }} · миграция {{ db.migration || '—' }}</p>
        <table class="table adm-costs__table">
          <thead>
            <tr><th>Самые большие таблицы</th><th class="num">Строк (оценка)</th><th class="num">Размер</th></tr>
          </thead>
          <tbody>
            <tr v-for="t in db.tables" :key="t.name">
              <td>{{ t.name }}</td>
              <td class="num">{{ num(t.rows) }}</td>
              <td class="num">{{ bytes(t.size_bytes) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ADMIN_BASE } from './adminNav'
import { mskTime } from './adminUsers'
import { useAdminLoad } from './useAdminLoad'
import { adminApi } from '../../api/admin'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num, plural } from '../../utils/format'

/** Система (docs/admin-plan.md, этап 7): сервисы с кнопкой «Проверить», очередь, расписания, сбои, база. */
const S = `${ADMIN_BASE}/settings`
const SETTINGS = {
  ai: `${S}/ai`,
  wordstat: `${S}/wordstat`,
  images: `${S}/images`,
  payments: `${S}/payments`,
  bridge: `${S}/bridge`,
  telegram: `${S}/alerts`,
}
const SCHEDULE_LABELS = {
  connections: 'Подключения Яндекса',
  accounts: 'Аккаунты Директа',
  counters: 'Счётчики Метрики',
  today: 'Расход за сегодня',
  digest: 'Дайджесты',
  rules: 'Правила',
  purge_jobs: 'Чистка очереди',
  plan_reminders: 'Напоминания о подписке',
  signals: 'Сигналы ИИ',
  payments: 'Сверка платежей',
  support_emails: 'Письма о поддержке',
  alerts: 'Уведомления админу',
}
const ERROR_TABS = [
  { key: 'jobs', label: 'Задачи' },
  { key: 'sync', label: 'Синхронизация' },
  { key: 'ai', label: 'ИИ' },
]

const { data, loading, error, reload } = useAdminLoad(adminApi.system)
const errTab = ref('jobs')

const q = computed(() => data.value.queue)
const db = computed(() => data.value.database)
const waiting = computed(() => q.value.kinds.reduce((s, k) => s + k.waiting, 0))
const running = computed(() => q.value.kinds.reduce((s, k) => s + k.running, 0))
const errors = computed(() => data.value.errors[errTab.value] || [])
const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`

async function check() {
  await reload()
  if (error.value) pushToast(error.value, 'error')
  else {
    const bad = data.value.services.filter((s) => s.ok === false).map((s) => s.label)
    pushToast(bad.length ? `Не работает: ${bad.join(', ')}` : 'Всё работает')
  }
}

const dot = (s) => (s.ok ? 'sdot--ok' : s.ok === null ? '' : 'sdot--warn')

function serviceNote(s) {
  if (s.key === 'database') return `отвечает за ${num(s.ping_ms, 1)} мс`
  if (s.key === 'queue') return s.ok ? `ждут ${num(s.waiting)}` : `ждут ${num(s.waiting)}, старейшая — ${num(s.lag_minutes)} мин`
  if (s.key === 'telegram') return s.ok ? (s.support ? 'бот и группа поддержки' : 'бот есть, группы поддержки нет') : 'не настроен'
  return s.ok ? 'работает' : s.ok === null ? 'выключено' : 'не настроено'
}

function every(sec) {
  if (sec % 86400 === 0) return sec === 86400 ? 'раз в сутки' : `раз в ${sec / 86400} дн`
  if (sec % 3600 === 0) return sec === 3600 ? 'раз в час' : `раз в ${sec / 3600} ч`
  return `раз в ${Math.round(sec / 60)} мин`
}

function bytes(v) {
  if (v < 1024 * 1024) return `${num(v / 1024, 0)} КБ`
  if (v < 1024 ** 3) return `${num(v / 1024 ** 2, 1)} МБ`
  return `${num(v / 1024 ** 3, 2)} ГБ`
}
</script>

<style scoped>
.adm-sys__services {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px 18px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.adm-sys__services li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}

.adm-sys__services small {
  flex-basis: 100%;
  padding-left: 16px;
}
</style>
