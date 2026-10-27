<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Аккаунты</h1>
        <p>
          Логины Яндекса, их счётчики Метрики и кабинеты Директа. Каждый счётчик — рабочее пространство сайта,
          кампании из любых кабинетов добавляются в его настройках.
        </p>
      </div>
    </div>

    <section class="panel">
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
          <p>Все счётчики логина и их цели. Для каждого счётчика появится рабочее пространство.</p>
          <YandexConnectButton purpose="connect" mode="metrika" label="Добавить аккаунт Метрики" return-to="/cabinet/accounts" />
        </article>
        <article class="connect-card">
          <div class="connect-card__top">
            <span class="connect-card__badge connect-card__badge--direct">Директ</span>
            <span v-if="liveAccounts.length" class="chip">{{ liveAccounts.length }} {{ plural(liveAccounts.length, 'кабинет', 'кабинета', 'кабинетов') }}</span>
          </div>
          <h3>Яндекс Директ</h3>
          <p>Кабинеты и все их кампании. Источник каждой кампании сохраняется — с ней работаем через её кабинет.</p>
          <YandexConnectButton purpose="connect" mode="direct" label="Добавить аккаунт Директа" return-to="/cabinet/accounts" />
        </article>
        <article class="connect-card">
          <div class="connect-card__top">
            <span class="connect-card__badge connect-card__badge--sandbox">Песочница</span>
            <span v-if="sandboxAccounts.length" class="chip">{{ sandboxAccounts.length }}</span>
          </div>
          <h3>Песочница Директа</h3>
          <p>Тестовый Директ без денег и показов: попробовать создание кампаний и ИИ-ассистента без риска.</p>
          <YandexConnectButton
            purpose="connect"
            mode="sandbox"
            label="Подключить песочницу"
            variant="ghost"
            return-to="/cabinet/accounts"
          />
        </article>
      </div>
      <div class="connect-offers__one">
        <YandexConnectButton
          purpose="connect"
          label="Метрика и Директ одним логином"
          variant="ghost"
          :wide="false"
          return-to="/cabinet/accounts"
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

    <div v-if="loading" class="panel"><p class="empty">Загружаем аккаунты…</p></div>

    <template v-else>
      <section class="panel">
        <h2>Логины Яндекса</h2>
        <p class="hint">
          Роли можно выключить, не удаляя логин: например, оставить только Директ, если счётчики этого логина не
          нужны. Данные и привязки к пространствам сохраняются.
        </p>
        <div v-if="connections.length" class="list">
          <div v-for="c in connections" :key="c.id" class="list-row conn-row">
            <div>
              <strong>
                {{ c.display_name || c.yandex_login || c.yandex_uid }}
                <span v-if="c.sandbox" class="chip chip--info">песочница</span>
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
                    :disabled="c.sandbox || busyId === c.id"
                    @change="setRole(c, { use_metrika: $event.target.checked }, $event)"
                  />
                  Метрика
                </label>
                <label class="check-row">
                  <input
                    type="checkbox"
                    :checked="c.use_direct"
                    :disabled="c.sandbox || busyId === c.id"
                    @change="setRole(c, { use_direct: $event.target.checked }, $event)"
                  />
                  Директ
                </label>
              </div>
            </div>
            <div class="row-actions">
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
        <p class="hint">Выключенный счётчик не обновляется, его пространство остаётся с историей.</p>
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
                Пространство
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
          Кампании кабинета раскладываются по пространствам по счётчику в их настройках; остальные можно добавить
          вручную в настройках пространства. Включённый кабинет загружается целиком за 30 дней, дальше данные
          актуализируются сами: кампании и изменения структуры — каждый час, статистика — каждые 6 часов, поисковые
          запросы — раз в сутки.
        </p>
        <div v-if="filteredAccounts.length" class="list">
          <div v-for="a in filteredAccounts" :key="a.id" class="list-row" :class="{ 'is-off': !a.enabled }">
            <div>
              <strong>
                {{ a.name || a.client_login }}
                <span v-if="isSandbox(a)" class="chip chip--info">песочница</span>
              </strong>
              <small>
                {{ a.client_login }}
                <template v-if="a.currency"> · {{ a.currency }}</template>
                · @{{ connectionLogin(a.connection_id) }}
                · {{ a.campaigns }} {{ plural(a.campaigns, 'кампания', 'кампании', 'кампаний') }}
                · {{ a.workspaces ? `в ${a.workspaces} ${plural(a.workspaces, 'пространстве', 'пространствах', 'пространствах')}` : 'не в пространстве' }}
              </small>
              <template v-if="a.enabled">
                <LoadStages :stages="accountLoad(a).stages" :active="Boolean(accountLoad(a).job)" />
                <div class="sync-status">
                  <template v-if="accountLoad(a).job">{{ jobText(accountLoad(a).job) }}</template>
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
  </AppShell>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import LoadStages from '../components/LoadStages.vue'
import YandexConnectButton from '../components/YandexConnectButton.vue'
import { errorDetail, errorText, JOB_LABELS, jobProgressText, webApi } from '../api/web'
import { jobText, refreshLoads, useLoads } from '../composables/useLoads'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { formatDateTime, num, plural } from '../utils/format'

useHead('Аккаунты — AiMarketing', true)

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
let jobsTimer = null

// Загрузка закончилась — тихо обновляем счётчики кампаний и пространства.
const loads = useLoads({ onFinished: () => reloadAll() })

const connections = computed(() => me.value.connections || [])
const counters = computed(() => me.value.metrika || [])
const accounts = computed(() => me.value.direct || [])
const connById = computed(() => new Map(connections.value.map((c) => [c.id, c])))
const sandboxAccounts = computed(() => accounts.value.filter(isSandbox))
const liveAccounts = computed(() => accounts.value.filter((a) => !isSandbox(a)))
const accountLoads = computed(() => new Map(loads.overview.value.accounts.map((a) => [a.id, a])))
const counterLoads = computed(() => new Map(loads.overview.value.counters.map((c) => [c.id, c])))

/** Этапы загрузки: свежие из опроса /loads, до первого ответа — из профиля. */
function accountLoad(a) {
  return accountLoads.value.get(a.id) || { ...(a.load || {}), job: null }
}

function counterLoad(m) {
  return counterLoads.value.get(m.id) || { ...(m.load || {}), job: null }
}

function isSandbox(account) {
  return Boolean(connById.value.get(account.connection_id)?.sandbox)
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
  await Promise.all([load(true), loadWorkspaces(true).catch(() => null)])
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
    `Отключить логин ${name}? Его счётчики, кабинеты и кампании пропадут из кабинета, пространства останутся без данных этого логина. В Яндексе ничего не изменится.`,
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
