<template>
  <p v-if="expert" class="page-lead">
    Логины Яндекса, их счётчики Метрики и кабинеты Директа. Каждый счётчик — проект сайта, кампании из любых
    кабинетов добавляются в настройках проекта.
  </p>
  <p v-else class="page-lead">Откуда сервис берёт данные: аккаунт Яндекса и сайты со счётчиками Метрики.</p>

  <div v-if="!expert && !loading" class="ccard">
    <template v-if="connections.length">
      <div v-for="c in connections" :key="c.id" class="prow">
        <div class="prow__main">
          <b class="conn-name"><Plug :size="16" aria-hidden="true" />Яндекс: {{ c.yandex_login || c.display_name || c.yandex_uid }}</b>
          <p v-if="c.last_sync_status === 'error' && c.last_sync_error">
            Не получилось обновить: {{ errorDetail(c.last_sync_error) }}. Переподключите аккаунт, если ошибка повторяется.
          </p>
          <p v-else>
            {{ roleText(c) }}. Сервис читает статистику, а менять рекламу может только после вашего «Выполнить».
          </p>
        </div>
        <div class="prow__acts">
          <span v-if="c.last_sync_status === 'error'" class="st st--warn">
            <TriangleAlert :size="14" aria-hidden="true" />Ошибка
          </span>
          <span v-else class="st st--ok"><CircleCheck :size="14" aria-hidden="true" />Подключён</span>
          <YandexConnectButton
            purpose="connect"
            label="Переподключить"
            variant="ghost"
            :wide="false"
            return-to="/cabinet/connections"
          />
        </div>
      </div>
      <div class="prow">
        <div class="prow__main">
          <b class="conn-name">
            <RefreshCw :size="16" aria-hidden="true" />
            {{ lastSyncAt ? `Данные обновлены ${timeAgo(lastSyncAt)}` : 'Данные ещё не обновлялись' }}
          </b>
          <p>Обновляются сами: кампании — каждый час, статистика — каждые 6 часов, поисковые запросы — раз в сутки.</p>
        </div>
        <div class="prow__acts">
          <button class="btn" type="button" :disabled="syncingAll || busyId !== null" @click="syncAll">
            <RefreshCw :size="14" aria-hidden="true" />{{ syncingAll ? 'Обновляем…' : 'Обновить сейчас' }}
          </button>
        </div>
      </div>
    </template>
    <div v-else class="prow">
      <div class="prow__main">
        <b class="conn-name"><Plug :size="16" aria-hidden="true" />Яндекс не подключён</b>
        <p>Подключите аккаунт с Директом и Метрикой — сервис загрузит кампании и статистику, обычно за пару минут.</p>
      </div>
      <div class="prow__acts">
        <YandexConnectButton purpose="connect" label="Подключить Яндекс" :wide="false" return-to="/cabinet/connections" />
      </div>
    </div>
  </div>

  <section v-if="expert" class="panel">
    <h2>Подключить</h2>
    <p class="hint">
      На странице Яндекса можно выбрать любой логин — так подключаются кабинеты клиентов с отдельными логинами.
      Агентский логин Директа подтянет все клиентские кабинеты сразу.
    </p>
    <div class="connect-grid connect-grid--three">
      <article class="connect-card">
        <div class="connect-card__top">
          <span class="connect-card__badge">Метрика</span>
          <span v-if="counters.length" class="chip">{{ counters.length }} {{ plural(counters.length, 'счётчик', 'счётчика', 'счётчиков') }}</span>
        </div>
        <h3>Яндекс Метрика</h3>
        <p>Все счётчики логина и их цели. Для каждого счётчика появится проект.</p>
        <YandexConnectButton purpose="connect" mode="metrika" label="Добавить логин Метрики" return-to="/cabinet/connections" />
      </article>
      <article class="connect-card">
        <div class="connect-card__top">
          <span class="connect-card__badge connect-card__badge--direct">Директ</span>
          <span v-if="accounts.length" class="chip">{{ accounts.length }} {{ plural(accounts.length, 'кабинет', 'кабинета', 'кабинетов') }}</span>
        </div>
        <h3>Яндекс Директ</h3>
        <p>Кабинеты и все их кампании. Источник каждой кампании сохраняется — с ней работаем через её кабинет.</p>
        <YandexConnectButton purpose="connect" mode="direct" label="Добавить логин Директа" return-to="/cabinet/connections" />
      </article>
    </div>
    <div class="connect-offers__one">
      <YandexConnectButton
        purpose="connect"
        label="Метрика и Директ одним логином"
        variant="ghost"
        :wide="false"
        return-to="/cabinet/connections"
      />
      <p>Если счётчики и кабинеты на одном логине — подключите обе роли за раз.</p>
    </div>
  </section>

  <section v-if="activeJobs.length" class="panel">
    <h2>Идёт синхронизация</h2>
    <p class="hint">Задачи выполняются на сервере — страницу можно закрыть, данные появятся сами.</p>
    <div class="list">
      <div v-for="job in activeJobs" :key="job.id" class="list-row">
        <div>
          <strong>{{ JOB_LABELS[job.kind] || job.kind }}</strong>
          <small>{{ jobProgressText(job) }}</small>
        </div>
        <span class="spinner" aria-hidden="true"></span>
      </div>
    </div>
  </section>

  <div v-if="loading" class="panel"><p class="empty">Загружаем подключения…</p></div>

  <template v-else>
    <SitesPanel ref="sitesPanel" @changed="reloadAll" />

    <ExpertHint>Логины и их роли, счётчики Метрики, кабинеты Директа, Яндекс Аудитории и история обновлений</ExpertHint>

    <template v-if="expert">
    <section class="panel">
      <h2>Логины Яндекса</h2>
      <p class="hint">
        Роли можно выключить, не удаляя логин: например, оставить только Директ, если счётчики этого логина не
        нужны. Данные и привязки к проектам сохраняются.
      </p>
      <div v-if="connections.length" class="list">
        <div v-for="c in connections" :key="c.id" class="list-row conn-row">
          <div>
            <strong>
              {{ c.display_name || c.yandex_login || c.yandex_uid }}
            </strong>
            <small>
              @{{ c.yandex_login || c.yandex_uid }} ·
              {{ c.counters }} {{ plural(c.counters, 'счётчик', 'счётчика', 'счётчиков') }} ·
              {{ c.accounts }} {{ plural(c.accounts, 'кабинет', 'кабинета', 'кабинетов') }} ·
              {{ c.campaigns }} {{ plural(c.campaigns, 'кампания', 'кампании', 'кампаний') }}
            </small>
            <div class="sync-status" :class="syncClass(c.last_sync_status)">
              <template v-if="c.last_sync_status">
                {{ SYNC_STATUS[c.last_sync_status] || c.last_sync_status }}
                <template v-if="c.last_sync_at"> · {{ formatDateTime(c.last_sync_at) }}</template>
                <template v-if="c.last_sync_error"> · {{ errorDetail(c.last_sync_error) }}</template>
              </template>
              <template v-else>ещё не синхронизировали</template>
            </div>
            <div class="role-toggles">
              <label class="check-row">
                <input
                  type="checkbox"
                  :checked="c.use_metrika"
                  :disabled="busyId === c.id"
                  @change="setRole(c, { use_metrika: $event.target.checked }, $event)"
                />
                Метрика
              </label>
              <label class="check-row">
                <input
                  type="checkbox"
                  :checked="c.use_direct"
                  :disabled="busyId === c.id"
                  @change="setRole(c, { use_direct: $event.target.checked }, $event)"
                />
                Директ
              </label>
            </div>
            <small v-if="needsAudience(c)" class="hint">
              Подключите Яндекс Аудитории, чтобы ИИ создавал новые LAL (похожих на покупателей). Войдите тем же
              логином @{{ c.yandex_login || c.yandex_uid }}.
            </small>
            <small v-else-if="me.audience_oauth && c.audience" class="hint">Яндекс Аудитории подключены.</small>
          </div>
          <div class="row-actions">
            <YandexConnectButton
              v-if="needsAudience(c)"
              purpose="audience"
              :connection-id="c.id"
              label="Подключить Аудитории"
              variant="ghost"
              :wide="false"
              return-to="/cabinet/connections"
            />
            <button class="btn btn--ghost" type="button" :disabled="busyId === c.id" @click="syncConnection(c)">
              {{ busyId === c.id ? 'Синхронизируем…' : 'Обновить' }}
            </button>
            <button class="btn btn--ghost btn--danger" type="button" :disabled="busyId === c.id" @click="removeConnection(c)">
              Отключить
            </button>
          </div>
        </div>
      </div>
      <p v-else class="empty">Пока ни одного логина — подключите Метрику или Директ выше.</p>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Счётчики Метрики</h2>
        <div v-if="counters.length > 5" class="filters filters--inline">
          <input v-model="counterSearch" type="search" placeholder="Сайт или номер" />
        </div>
      </div>
      <p class="hint">Выключенный счётчик не обновляется, его проект остаётся с историей.</p>
      <div v-if="filteredCounters.length" class="list">
        <div v-for="m in filteredCounters" :key="m.id" class="list-row" :class="{ 'is-off': !m.enabled }">
          <div>
            <strong>{{ m.name || `Счётчик ${m.counter_id}` }}</strong>
            <small>
              № {{ m.counter_id }}
              <template v-if="m.site"> · {{ m.site }}</template>
              · @{{ connectionLogin(m.connection_id) }}
            </small>
            <template v-if="m.enabled">
              <p v-if="counterLoad(m).canonical === false" class="sync-status">
                Этот счётчик виден и через другой подключённый логин — данные загружаются там.
              </p>
              <template v-else>
                <LoadStages :stages="counterLoad(m).stages" :active="Boolean(counterLoad(m).job)" />
                <div class="sync-status">
                  <template v-if="counterLoad(m).job">{{ jobText(counterLoad(m).job) }}</template>
                  <span v-else-if="stageFailure(counterLoad(m))" class="is-warn">{{ failureLine(counterLoad(m)) }}</span>
                  <template v-else-if="counterLoad(m).loaded_at">
                    загружен {{ formatDateTime(counterLoad(m).loaded_at) }}, визиты — каждые 6 часов
                  </template>
                  <template v-else>ещё не загружен целиком</template>
                </div>
              </template>
            </template>
          </div>
          <div class="row-actions">
            <RouterLink v-if="workspaceFor(m)" class="btn btn--ghost" :to="`/w/${workspaceFor(m).id}/summary`">
              Проект
            </RouterLink>
            <button
              v-if="m.enabled && counterLoad(m).canonical !== false"
              class="btn btn--ghost"
              type="button"
              :disabled="Boolean(counterLoad(m).job) || loadBusy === `c${m.id}`"
              @click="loadCounter(m)"
            >
              {{ counterLoad(m).job ? 'Загружается…' : 'Обновить данные' }}
            </button>
            <button class="btn btn--ghost" type="button" @click="toggleCounter(m)">
              {{ m.enabled ? 'Выключить' : 'Включить' }}
            </button>
          </div>
        </div>
      </div>
      <p v-else class="empty">
        {{ counters.length ? 'Ничего не найдено.' : 'Счётчиков пока нет — подключите логин с доступом к Метрике.' }}
      </p>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Кабинеты Директа</h2>
        <div v-if="accounts.length > 5" class="filters filters--inline">
          <input v-model="accountSearch" type="search" placeholder="Логин или название" />
        </div>
      </div>
      <p class="hint">
        Кампании кабинета раскладываются по проектам по счётчику в их настройках; остальные можно добавить
        вручную в настройках проекта. Включённый кабинет загружается целиком за 30 дней, дальше данные
        актуализируются сами: кампании и изменения структуры — каждый час, статистика — каждые 6 часов, поисковые
        запросы — раз в сутки.
      </p>
      <div v-if="filteredAccounts.length" class="list">
        <div v-for="a in filteredAccounts" :key="a.id" class="list-row" :class="{ 'is-off': !a.enabled }">
          <div>
            <strong>
              {{ a.name || a.client_login }}
            </strong>
            <small>
              {{ a.client_login }}
              <template v-if="a.currency"> · {{ a.currency }}</template>
              · @{{ connectionLogin(a.connection_id) }}
              · {{ a.campaigns }} {{ plural(a.campaigns, 'кампания', 'кампании', 'кампаний') }}
              · {{ a.workspaces ? `в ${a.workspaces} ${plural(a.workspaces, 'проекте', 'проектах', 'проектах')}` : 'не в проекте' }}
            </small>
            <template v-if="a.enabled">
              <LoadStages :stages="accountLoad(a).stages" :active="Boolean(accountLoad(a).job)" />
              <div class="sync-status">
                <template v-if="accountLoad(a).job">{{ jobText(accountLoad(a).job) }}</template>
                <span v-else-if="stageFailure(accountLoad(a))" class="is-warn">{{ failureLine(accountLoad(a)) }}</span>
                <template v-else-if="accountLoad(a).loaded_at">
                  загружен {{ formatDateTime(accountLoad(a).loaded_at) }}, дальше актуализируем каждый час
                </template>
                <template v-else>ещё не загружен целиком — продолжим автоматически</template>
                <template v-if="accountLoad(a).units">
                  · баллы API: {{ num(accountLoad(a).units.left) }} из {{ num(accountLoad(a).units.limit) }}
                </template>
              </div>
            </template>
            <div
              v-if="a.last_sync_status && a.last_sync_status !== 'ok'"
              class="sync-status"
              :class="syncClass(a.last_sync_status)"
            >
              кампании: {{ SYNC_STATUS[a.last_sync_status] || a.last_sync_status }}
              <template v-if="a.last_sync_at"> · {{ formatDateTime(a.last_sync_at) }}</template>
              <template v-if="a.last_sync_error"> · {{ errorDetail(a.last_sync_error) }}</template>
            </div>
          </div>
          <div class="row-actions">
            <template v-if="a.enabled">
              <button
                class="btn btn--ghost"
                type="button"
                :disabled="Boolean(accountLoad(a).job) || loadBusy === `a${a.id}`"
                title="Кампании, изменения структуры, свежая статистика"
                @click="refreshAccount(a)"
              >
                {{ accountLoad(a).job ? 'Загружается…' : 'Обновить' }}
              </button>
              <button
                class="btn btn--ghost"
                type="button"
                :disabled="Boolean(accountLoad(a).job) || loadBusy === `a${a.id}`"
                title="Все кампании, группы, объявления, фразы, статистика и запросы за 30 дней — с нуля"
                @click="reloadAccount(a)"
              >
                Загрузить заново
              </button>
            </template>
            <button class="btn btn--ghost" type="button" @click="toggleAccount(a)">
              {{ a.enabled ? 'Выключить' : 'Включить' }}
            </button>
          </div>
        </div>
      </div>
      <p v-else class="empty">
        {{ accounts.length ? 'Ничего не найдено.' : 'Кабинетов пока нет — подключите логин с доступом к Директу.' }}
      </p>
    </section>

    <section class="panel">
      <h2>История синхронизаций</h2>
      <p class="hint">Последние прогоны подключений, кампаний, статистики и целей.</p>
      <div v-if="syncRuns.length" class="list">
        <div v-for="run in syncRuns.slice(0, runsLimit)" :key="run.id" class="list-row">
          <div>
            <strong>{{ RUN_KINDS[run.kind] || run.kind || 'Синк' }}</strong>
            <small>
              <span class="sync-status" :class="syncClass(run.status)">{{ SYNC_STATUS[run.status] || run.status }}</span>
              · {{ formatDateTime(run.started_at) }}
              <template v-if="run.error"> · {{ errorDetail(run.error) }}</template>
            </small>
          </div>
        </div>
      </div>
      <p v-else class="empty">Пока нет записей.</p>
      <div v-if="syncRuns.length > runsLimit" class="pager">
        <button class="btn btn--ghost" type="button" @click="runsLimit += 20">Показать ещё</button>
      </div>
    </section>
    </template>
  </template>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import LoadStages from '../components/LoadStages.vue'
import { CircleCheck, Plug, RefreshCw, TriangleAlert } from '@lucide/vue'
import ExpertHint from '../components/ExpertHint.vue'
import SitesPanel from '../components/SitesPanel.vue'
import YandexConnectButton from '../components/YandexConnectButton.vue'
import { errorDetail, errorText, JOB_LABELS, jobProgressText, webApi } from '../api/web'
import { jobText, refreshLoads, stageErrorText, stageFailure, useLoads } from '../composables/useLoads'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { useExpert } from '../composables/useExpert'
import { formatDateTime, num, plural, timeAgo } from '../utils/format'

useHead('Подключения — AiMarketing', true)

const SYNC_STATUS = { ok: 'обновлено', error: 'ошибка', partial: 'частично', running: 'идёт' }
const RUN_KINDS = {
  connection: 'Подключение',
  direct_campaigns: 'Кампании',
  direct_adgroups: 'Группы',
  direct_keywords: 'Ключевые фразы',
  direct_ads: 'Объявления',
  direct_search_queries: 'Поисковые запросы',
  direct_entity_stats: 'Статистика групп и фраз',
  stats_daily: 'Статистика',
  stats_backfill: 'Статистика за период',
  metrika_goals: 'Цели Метрики',
}
const JOBS_POLL_MS = 3000
// Загрузки кабинетов и счётчиков показываем на их карточках, а не в общем списке задач.
const LOAD_KINDS = new Set(['load_account', 'load_counter'])

const loading = ref(true)
const me = ref({ connections: [], metrika: [], direct: [], workspaces: [] })
const syncRuns = ref([])
const runsLimit = ref(15)
const activeJobs = ref([])
const busyId = ref(null)
const loadBusy = ref('')
const counterSearch = ref('')
const accountSearch = ref('')
const sitesPanel = ref(null)
const syncingAll = ref(false)
const { expert } = useExpert()
let jobsTimer = null

// Загрузка закончилась — тихо обновляем счётчики кампаний и проекта.
const loads = useLoads({ onFinished: () => reloadAll() })

const connections = computed(() => me.value.connections || [])
const counters = computed(() => me.value.metrika || [])
const accounts = computed(() => me.value.direct || [])
const lastSyncAt = computed(() =>
  connections.value.reduce((best, c) => (c.last_sync_at && (!best || c.last_sync_at > best) ? c.last_sync_at : best), ''),
)
const connById = computed(() => new Map(connections.value.map((c) => [c.id, c])))
const accountLoads = computed(() => new Map(loads.overview.value.accounts.map((a) => [a.id, a])))
const counterLoads = computed(() => new Map(loads.overview.value.counters.map((c) => [c.id, c])))

/** Этапы загрузки: свежие из опроса /loads, до первого ответа — из профиля. */
function accountLoad(a) {
  return accountLoads.value.get(a.id) || { ...(a.load || {}), job: null }
}

function counterLoad(m) {
  return counterLoads.value.get(m.id) || { ...(m.load || {}), job: null }
}

/** Почему загрузка встала: этап и ответ API — видно сразу, без наведения на этап. */
function failureLine(load) {
  const failure = stageFailure(load)
  return `${failure.title}: ${stageErrorText(failure)}`
}

function connectionLogin(id) {
  const c = connById.value.get(id)
  return c ? c.yandex_login || c.yandex_uid : '—'
}

function workspaceFor(counter) {
  return (me.value.workspaces || []).find((w) => w.counter_id === counter.counter_id) || null
}

function syncClass(status) {
  return { 'is-ok': status === 'ok', 'is-error': status === 'error' || status === 'partial' }
}

// Яндекс Аудитории (новые LAL из чата ИИ) — отдельное приложение; не задано на сервере — кнопки нет.
function needsAudience(c) {
  return Boolean(me.value.audience_oauth && c.use_direct && !c.audience)
}

function textMatch(values, query) {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return values.some((v) => String(v || '').toLowerCase().includes(q))
}

const filteredCounters = computed(() =>
  counters.value.filter((m) => textMatch([m.name, m.site, m.counter_id], counterSearch.value)),
)
const filteredAccounts = computed(() =>
  accounts.value.filter((a) => textMatch([a.name, a.client_login], accountSearch.value)),
)

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    const [profile, runs] = await Promise.all([webApi.me(), webApi.syncRuns().catch(() => ({ items: [] }))])
    me.value = profile
    syncRuns.value = runs.items || []
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

async function reloadAll() {
  await Promise.all([load(true), loadWorkspaces(true).catch(() => null), sitesPanel.value?.load()])
}

// Пока на сервере идут задачи (синк сразу после подключения логина), опрашиваем их
// и тихо перезагружаем страницу, когда какая-то завершилась.
async function pollJobs() {
  clearTimeout(jobsTimer)
  jobsTimer = null
  try {
    const data = await webApi.jobs({ active: true })
    const next = (data.items || []).filter((j) => !LOAD_KINDS.has(j.kind))
    const nextIds = new Set(next.map((j) => j.id))
    const finished = activeJobs.value.some((j) => !nextIds.has(j.id))
    activeJobs.value = next
    if (finished) await reloadAll()
  } catch {
    // Сеть моргнула — попробуем на следующем тике.
  }
  if (activeJobs.value.length) jobsTimer = setTimeout(pollJobs, JOBS_POLL_MS)
}

async function syncConnection(c) {
  busyId.value = c.id
  try {
    const res = await webApi.syncConnection(c.id, () => {
      if (!jobsTimer) pollJobs()
    })
    const parts = [`счётчиков ${res.metrika ?? 0}`, `кабинетов ${res.direct ?? 0}`]
    const tail = res.loads ? ` Загружаем данные: ${res.loads} — прогресс на карточках и в шапке.` : ''
    pushToast(`Обновлено: ${parts.join(', ')}.${tail}`)
    await Promise.all([reloadAll(), refreshLoads()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

function roleText(c) {
  if (c.use_direct && c.use_metrika) return 'Доступ к Директу и Метрике'
  if (c.use_direct) return 'Доступ к Директу'
  if (c.use_metrika) return 'Доступ к Метрике'
  return 'Роли выключены'
}

/** «Обновить сейчас» простого режима: все логины по очереди, один итог. */
async function syncAll() {
  syncingAll.value = true
  let loadsStarted = 0
  let failed = 0
  try {
    for (const c of connections.value) {
      try {
        const res = await webApi.syncConnection(c.id, () => {
          if (!jobsTimer) pollJobs()
        })
        loadsStarted += res.loads || 0
      } catch (err) {
        failed += 1
        pushToast(errorText(err), 'error')
      }
    }
    if (!failed) {
      pushToast(
        loadsStarted
          ? 'Данные обновлены. Догружаем кампании и статистику — прогресс в шапке'
          : 'Данные обновлены',
      )
    }
    await Promise.all([reloadAll(), refreshLoads()])
  } finally {
    syncingAll.value = false
  }
}

async function setRole(c, patch, event) {
  busyId.value = c.id
  try {
    const res = await webApi.patchConnection(c.id, patch)
    Object.assign(c, res)
    await reloadAll()
  } catch (err) {
    if (event?.target) event.target.checked = !event.target.checked
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function removeConnection(c) {
  const name = c.yandex_login || c.display_name || c.yandex_uid
  const ok = await askConfirm(
    `Отключить логин ${name}? Его счётчики, кабинеты и кампании пропадут из кабинета, проекты останутся без данных этого логина. В Яндексе ничего не изменится.`,
  )
  if (!ok) return
  busyId.value = c.id
  try {
    await webApi.deleteConnection(c.id)
    pushToast('Логин отключён')
    await reloadAll()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function toggleCounter(m) {
  try {
    const res = await webApi.patchMetrika(m.id, !m.enabled)
    m.enabled = res.enabled
    await loadWorkspaces(true).catch(() => null)
    if (m.enabled) await load(true)
    refreshLoads()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function toggleAccount(a) {
  try {
    const res = await webApi.patchDirect(a.id, !a.enabled)
    a.enabled = res.enabled
    // Включили — сервер сразу ставит загрузку; выключили — идущая остановится на ближайшем шаге.
    refreshLoads()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

/** Ставит загрузку и не ждёт её: прогресс виден на карточке и в шапке. */
async function startLoad(key, call, message) {
  loadBusy.value = key
  try {
    await call()
    pushToast(message)
    await refreshLoads()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loadBusy.value = ''
  }
}

function refreshAccount(a) {
  return startLoad(`a${a.id}`, () => webApi.refreshAccount(a.id), 'Обновляем кабинет — прогресс на карточке')
}

async function reloadAccount(a) {
  const ok = await askConfirm(
    `Загрузить кабинет ${a.name || a.client_login} заново? Перечитаем все кампании, группы, объявления, фразы, статистику и запросы за 30 дней — это займёт время и баллы API Директа.`,
  )
  if (!ok) return
  return startLoad(`a${a.id}`, () => webApi.reloadAccount(a.id), 'Загружаем кабинет заново — прогресс на карточке')
}

function loadCounter(m) {
  return startLoad(`c${m.id}`, () => webApi.loadCounter(m.id), 'Обновляем цели и визиты счётчика')
}

onMounted(async () => {
  loads.start()
  await load()
  pollJobs()
})
onUnmounted(() => {
  clearTimeout(jobsTimer)
  loads.stop()
})
</script>

<style scoped>
.conn-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.conn-name svg {
  flex: none;
  color: var(--muted);
}
.prow__acts .btn {
  gap: 6px;
}
@media (max-width: 640px) {
  .prow {
    flex-wrap: wrap;
  }
}
</style>
