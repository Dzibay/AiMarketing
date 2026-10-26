<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div v-if="loading" class="panel"><p class="empty">Загружаем кабинет…</p></div>

        <template v-else-if="me">
          <div class="cabinet__head">
            <div>
              <h1>Главная</h1>
              <p>
                {{ me.user.name || me.user.email || 'Аккаунт' }}
                <span v-if="me.user.email && me.user.name"> · {{ me.user.email }}</span>
              </p>
            </div>
            <button class="btn btn--ghost" type="button" @click="logout">Выйти</button>
          </div>

          <CabinetNav />

          <section v-if="activeJobs.length" class="panel">
            <h2>Фоновые задачи</h2>
            <p class="hint">Синки идут на сервере — страницу можно закрыть, данные обновятся сами.</p>
            <div class="list">
              <div v-for="job in activeJobs" :key="job.id" class="list-row">
                <div>
                  <strong>{{ JOB_LABELS[job.kind] || job.kind }}</strong>
                  <small>{{ jobProgressText(job) }}</small>
                </div>
              </div>
            </div>
          </section>

          <section v-if="checklist.some((s) => !s.done)" class="panel">
            <h2>Старт</h2>
            <p class="hint">Пройдите шаги — кабинет начнёт давать пользу.</p>
            <ul class="checklist">
              <li v-for="step in checklist" :key="step.id" :class="{ done: step.done }">
                <span class="checklist__mark">{{ step.done ? '✓' : '○' }}</span>
                <div>
                  <strong>{{ step.title }}</strong>
                  <small v-if="step.hint">{{ step.hint }}</small>
                </div>
                <RouterLink v-if="!step.done && step.to" class="btn btn--ghost" :to="step.to">
                  Открыть
                </RouterLink>
              </li>
            </ul>
          </section>

          <ConnectOffers
            :connections="me.connections"
            :metrika="me.metrika"
            :direct="me.direct"
          />

          <section class="panel">
            <h2>Telegram</h2>
            <p class="hint">
              Алерты при срабатывании правил (вне dry-run или для действия «уведомить»).
            </p>
            <template v-if="!telegram.configured">
              <p class="empty">Бот не настроен на сервере (TELEGRAM_BOT_TOKEN).</p>
            </template>
            <template v-else-if="telegram.linked">
              <p class="hint">Подключено.</p>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" :disabled="tgBusy" @click="testTelegram">
                  Тест
                </button>
                <button class="btn btn--ghost" type="button" :disabled="tgBusy" @click="unlinkTelegram">
                  Отвязать
                </button>
              </div>
            </template>
            <template v-else>
              <p class="hint">Нажмите «Получить ссылку» и откройте бота → Start.</p>
              <div class="row-actions">
                <button class="btn btn--accent" type="button" :disabled="tgBusy" @click="linkTelegram">
                  Получить ссылку
                </button>
                <a v-if="tgLink" class="btn btn--ghost" :href="tgLink" target="_blank" rel="noopener">
                  Открыть бота
                </a>
              </div>
              <p v-if="tgCode && !tgLink" class="hint">Код для /start: {{ tgCode }}</p>
            </template>
          </section>

          <template v-if="me.connections.length">
            <section class="panel">
              <h2>Аккаунты Яндекса</h2>
              <p class="hint">Статус последнего синка и управление подключением.</p>
              <div class="list">
                <div v-for="item in me.connections" :key="item.id" class="list-row">
                  <div>
                    <strong>{{ item.display_name || item.yandex_login || item.yandex_uid }}</strong>
                    <small>@{{ item.yandex_login || item.yandex_uid }}</small>
                    <div
                      class="sync-status"
                      :class="{
                        'is-ok': item.last_sync_status === 'ok',
                        'is-error': item.last_sync_status === 'error',
                      }"
                    >
                      <template v-if="item.last_sync_status">
                        {{ item.last_sync_status }}
                        <template v-if="item.last_sync_error"> · {{ item.last_sync_error }}</template>
                        <template v-if="item.last_sync_at"> · {{ item.last_sync_at }}</template>
                      </template>
                      <template v-else>ещё не синхронизировали</template>
                    </div>
                  </div>
                  <div class="row-actions">
                    <button class="btn btn--ghost" type="button" :disabled="busyId === item.id" @click="sync(item.id)">
                      Обновить
                    </button>
                    <button class="btn btn--ghost" type="button" :disabled="busyId === item.id" @click="remove(item.id)">
                      Отключить
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section class="panel">
              <h2>Счётчики Метрики</h2>
              <div v-if="me.metrika.length" class="list">
                <div
                  v-for="item in me.metrika"
                  :key="item.id"
                  class="list-row"
                  :class="{ 'is-off': !item.enabled }"
                >
                  <div>
                    <strong>{{ item.name || `Счётчик ${item.counter_id}` }}</strong>
                    <small>
                      {{ item.site || `ID ${item.counter_id}` }}
                      <template v-if="clientName(item.client_row_id)">
                        · {{ clientName(item.client_row_id) }}
                      </template>
                    </small>
                  </div>
                  <div class="row-actions">
                    <span class="chip">{{ item.counter_id }}</span>
                    <button class="btn btn--ghost" type="button" @click="syncGoals(item.id)">Цели</button>
                    <button class="btn btn--ghost" type="button" @click="toggleMetrika(item)">
                      {{ item.enabled ? 'Выключить' : 'Включить' }}
                    </button>
                  </div>
                </div>
              </div>
              <p v-else class="empty">Счётчиков пока нет.</p>
              <div v-if="goals.length" class="list" style="margin-top: 0.85rem">
                <div v-for="g in goals" :key="g.id" class="list-row">
                  <div>
                    <strong>{{ g.name || `Цель ${g.goal_id}` }}</strong>
                    <small>{{ g.counter_name }} · {{ g.goal_type || g.goal_id }}</small>
                  </div>
                </div>
              </div>
            </section>

            <section class="panel">
              <h2>Кабинеты Директа</h2>
              <div v-if="me.direct.length" class="list">
                <div
                  v-for="item in me.direct"
                  :key="item.id"
                  class="list-row"
                  :class="{ 'is-off': !item.enabled }"
                >
                  <div>
                    <strong>{{ item.name || item.client_login }}</strong>
                    <small>
                      {{ item.client_login }}
                      <template v-if="item.currency"> · {{ item.currency }}</template>
                      <template v-if="clientName(item.client_row_id)">
                        · {{ clientName(item.client_row_id) }}
                      </template>
                    </small>
                    <div
                      v-if="item.last_sync_status"
                      class="sync-status"
                      :class="{
                        'is-ok': item.last_sync_status === 'ok',
                        'is-error': item.last_sync_status === 'error',
                      }"
                    >
                      кампании: {{ item.last_sync_status }}
                      <template v-if="item.last_sync_error"> · {{ item.last_sync_error }}</template>
                    </div>
                  </div>
                  <div class="row-actions">
                    <button class="btn btn--ghost" type="button" @click="syncAccount(item.id)">Кампании</button>
                    <button class="btn btn--ghost" type="button" @click="toggleDirect(item)">
                      {{ item.enabled ? 'Выключить' : 'Включить' }}
                    </button>
                  </div>
                </div>
              </div>
              <p v-else class="empty">Кабинетов пока нет.</p>
            </section>

            <section class="panel">
              <h2>Связи Директ ↔ Метрика</h2>
              <p class="hint">
                Нужны для склейки: в аналитике визиты и цели Метрики считаются по связанным счётчикам
                аккаунта (фильтр по кабинету Директа).
              </p>
              <div class="filters">
                <div class="field">
                  <label>Аккаунт Директа</label>
                  <select v-model.number="linkAccountId">
                    <option :value="0">Выберите</option>
                    <option v-for="a in me.direct" :key="a.id" :value="a.id">
                      {{ a.name || a.client_login }}
                    </option>
                  </select>
                </div>
                <div class="field">
                  <label>Счётчик</label>
                  <select v-model.number="linkCounterId">
                    <option :value="0">Выберите</option>
                    <option v-for="c in me.metrika" :key="c.id" :value="c.id">
                      {{ c.name || c.counter_id }}
                    </option>
                  </select>
                </div>
                <button class="btn btn--accent" type="button" @click="addLink">Связать</button>
              </div>
              <div v-if="me.links?.length" class="list">
                <div v-for="link in me.links" :key="link.id" class="list-row">
                  <div>
                    <strong>{{ link.account_name || link.client_login }}</strong>
                    <small>→ {{ link.counter_name }} ({{ link.counter_id }})</small>
                  </div>
                  <button class="btn btn--ghost" type="button" @click="removeLink(link.id)">Удалить</button>
                </div>
              </div>
              <p v-else class="empty">Связей пока нет.</p>
            </section>

            <section class="panel">
              <h2>История синков</h2>
              <p class="hint">Последние прогоны подключений, кампаний и статистики.</p>
              <div v-if="syncRuns.length" class="list">
                <div v-for="run in syncRuns" :key="run.id" class="list-row">
                  <div>
                    <strong>{{ kindLabel(run.kind) }}</strong>
                    <small>
                      <span
                        class="sync-status"
                        :class="{
                          'is-ok': run.status === 'ok',
                          'is-error': run.status === 'error' || run.status === 'partial',
                        }"
                      >
                        {{ run.status }}
                      </span>
                      <template v-if="run.error"> · {{ run.error }}</template>
                      · {{ run.started_at }}
                    </small>
                  </div>
                </div>
              </div>
              <p v-else class="empty">Пока нет записей — запустите синк подключений или статистики.</p>
            </section>

            <section class="panel">
              <h2>Лог действий в Директе</h2>
              <p class="hint">
                Кратко. Полный список —
                <RouterLink to="/cabinet/campaigns">в кампаниях</RouterLink>.
              </p>
              <div v-if="actionLogs.length" class="list">
                <div v-for="log in actionLogs" :key="log.id" class="list-row">
                  <div>
                    <strong>{{ log.action }} · {{ log.status }}</strong>
                    <small>{{ log.created_at }}</small>
                  </div>
                </div>
              </div>
              <p v-else class="empty">Действий пока не было.</p>
            </section>
          </template>

          <section class="panel">
            <h2>Смена пароля</h2>
            <form class="filters" @submit.prevent="changePassword">
              <div class="field">
                <label>Текущий</label>
                <input v-model="pwd.current" type="password" autocomplete="current-password" required />
              </div>
              <div class="field">
                <label>Новый</label>
                <input v-model="pwd.next" type="password" autocomplete="new-password" required minlength="8" />
              </div>
              <button class="btn btn--accent" type="submit" :disabled="pwdBusy">Сохранить</button>
            </form>
          </section>
        </template>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import ConnectOffers from '../components/ConnectOffers.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, JOB_LABELS, jobProgressText, webApi } from '../api/web'
import { clearSession, getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Главная — AiMarketing', true)

const router = useRouter()
const me = ref(null)
const loading = ref(true)
const busyId = ref(null)
const goals = ref([])
const syncRuns = ref([])
const actionLogs = ref([])
const rulesCount = ref(0)
const hasStats = ref(false)
const hasStructureStats = ref(false)
const telegram = ref({ configured: false, linked: false })
const tgLink = ref('')
const tgCode = ref('')
const tgBusy = ref(false)
const linkAccountId = ref(0)
const linkCounterId = ref(0)
const pwd = reactive({ current: '', next: '' })
const pwdBusy = ref(false)
const activeJobs = ref([])
let jobsTimer = null
const JOBS_POLL_MS = 3000

function clientName(clientRowId) {
  if (!clientRowId) return ''
  return (me.value?.clients || []).find((c) => c.id === clientRowId)?.name || ''
}

const checklist = computed(() => {
  const profile = me.value
  if (!profile) return []
  const hasConn = (profile.connections || []).length > 0
  const hasDirect = (profile.direct || []).some((a) => a.enabled)
  const hasMetrika = (profile.metrika || []).some((c) => c.enabled)
  const hasLink = (profile.links || []).length > 0
  return [
    {
      id: 'yandex',
      title: 'Подключить Яндекс',
      done: hasConn,
      hint: 'Метрика и Директ одним OAuth',
    },
    {
      id: 'sync',
      title: 'Синхронизировать кабинеты',
      done: hasDirect || hasMetrika,
      hint: 'Кнопка «Обновить» у аккаунта Яндекса',
    },
    {
      id: 'link',
      title: 'Связать Директ ↔ Метрика',
      done: hasLink,
      hint: 'Нужно для склейки аналитики',
    },
    {
      id: 'stats',
      title: 'Загрузить статистику',
      done: hasStats.value,
      hint: 'Аналитика → 7 или 30 дней',
      to: '/cabinet/analytics',
    },
    {
      id: 'structure',
      title: 'Синк статистики кампании',
      done: hasStructureStats.value,
      hint: 'Кампания → «Синк статистики» (ключи и запросы)',
      to: '/cabinet/campaigns',
    },
    {
      id: 'rule',
      title: 'Создать правило (dry-run)',
      done: rulesCount.value > 0,
      hint: 'Кампании, ключи или запросы → минуса',
      to: '/cabinet/rules',
    },
  ]
})

function kindLabel(kind) {
  return (
    {
      connection: 'Подключение',
      direct_campaigns: 'Кампании',
      direct_adgroups: 'Группы',
      direct_keywords: 'Ключи',
      direct_search_queries: 'Поисковые запросы',
      direct_entity_stats: 'Стата групп/ключей',
      stats_daily: 'Статистика',
      stats_backfill: 'Бэкфилл статистики',
      metrika_goals: 'Цели Метрики',
    }[kind] || kind || 'Синк'
  )
}

async function load(quiet = false) {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  if (!quiet) loading.value = true
  try {
    const [profile, runs, tg, rules, analytics, actions] = await Promise.all([
      webApi.me(),
      webApi.syncRuns(),
      webApi.telegramStatus(),
      webApi.rules().catch(() => ({ items: [] })),
      webApi.analytics({}).catch(() => null),
      webApi.actionLogs().catch(() => ({ items: [] })),
    ])
    me.value = profile
    syncRuns.value = runs.items || []
    actionLogs.value = (actions.items || []).slice(0, 8)
    telegram.value = tg
    rulesCount.value = (rules.items || []).length
    const t = analytics?.totals
    hasStats.value = Boolean(
      t && (t.cost_kopecks || t.clicks || t.visits || t.conversions || t.goal_reaches),
    )
    const structureKinds = new Set([
      'direct_search_queries',
      'direct_entity_stats',
      'direct_keywords',
    ])
    hasStructureStats.value = (runs.items || []).some(
      (r) => structureKinds.has(r.kind) && r.status === 'ok',
    )
    if (tg.linked) {
      tgLink.value = ''
      tgCode.value = ''
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
    if (!quiet) router.replace('/login')
  } finally {
    loading.value = false
  }
}

// Пока на сервере идут задачи (например, синк сразу после подключения Яндекса), опрашиваем их
// и тихо перезагружаем кабинет, когда какая-то завершилась.
async function pollJobs() {
  clearTimeout(jobsTimer)
  jobsTimer = null
  try {
    const data = await webApi.jobs({ active: true })
    const next = data.items || []
    const nextIds = new Set(next.map((j) => j.id))
    const finished = activeJobs.value.some((j) => !nextIds.has(j.id))
    activeJobs.value = next
    if (finished) await load(true)
  } catch {
    // Сеть моргнула — попробуем на следующем тике.
  }
  if (activeJobs.value.length) jobsTimer = setTimeout(pollJobs, JOBS_POLL_MS)
}

async function linkTelegram() {
  tgBusy.value = true
  try {
    const data = await webApi.telegramLink()
    if (!data.configured) {
      pushToast('Бот не настроен на сервере', 'error')
      return
    }
    tgLink.value = data.url || ''
    tgCode.value = data.code || ''
    pushToast(data.url ? 'Ссылка готова — откройте бота' : `Отправьте боту: /start ${data.code}`)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function unlinkTelegram() {
  tgBusy.value = true
  try {
    await webApi.telegramUnlink()
    pushToast('Telegram отвязан')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function testTelegram() {
  tgBusy.value = true
  try {
    await webApi.telegramTest()
    pushToast('Тестовое сообщение отправлено')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function changePassword() {
  pwdBusy.value = true
  try {
    await webApi.changePassword({
      current_password: pwd.current,
      new_password: pwd.next,
    })
    pwd.current = ''
    pwd.next = ''
    pushToast('Пароль обновлён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    pwdBusy.value = false
  }
}

async function sync(id) {
  busyId.value = id
  try {
    const stats = await webApi.syncConnection(id, () => {
      if (!jobsTimer) pollJobs()
    })
    pushToast(`Синк: Метрика ${stats.metrika}, Директ ${stats.direct} (${stats.status || 'ok'})`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function remove(id) {
  busyId.value = id
  try {
    await webApi.deleteConnection(id)
    pushToast('Подключение удалено')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function toggleMetrika(item) {
  try {
    await webApi.patchMetrika(item.id, !item.enabled)
    item.enabled = !item.enabled
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function toggleDirect(item) {
  try {
    await webApi.patchDirect(item.id, !item.enabled)
    item.enabled = !item.enabled
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function syncAccount(id) {
  try {
    const res = await webApi.syncAccount(id)
    pushToast(`Кампаний: ${res.campaigns}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function syncGoals(counterRowId) {
  try {
    const res = await webApi.syncGoals(counterRowId)
    pushToast(`Целей: ${res.goals}`)
    const data = await webApi.goals()
    goals.value = data.items || []
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function addLink() {
  if (!linkAccountId.value || !linkCounterId.value) {
    pushToast('Выберите аккаунт и счётчик', 'error')
    return
  }
  try {
    await webApi.createLink({
      account_id: linkAccountId.value,
      counter_row_id: linkCounterId.value,
    })
    pushToast('Связь создана')
    linkAccountId.value = 0
    linkCounterId.value = 0
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function removeLink(id) {
  try {
    await webApi.deleteLink(id)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

function logout() {
  clearSession()
  router.replace('/login')
}

onUnmounted(() => clearTimeout(jobsTimer))

onMounted(async () => {
  await load()
  pollJobs()
  try {
    const data = await webApi.goals()
    goals.value = data.items || []
  } catch {
    goals.value = []
  }
})
</script>
