<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <h1>Аналитика</h1>
            <p>Сводка по Директу и связанным счётчикам Метрики за период.</p>
          </div>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncStats(1)">
              Вчера
            </button>
            <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncStats(7)">
              7 дней
            </button>
            <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncStats(14)">
              14 дней
            </button>
            <button class="btn btn--accent" type="button" :disabled="syncing" @click="syncStats(30)">
              {{ syncing ? syncLabel || 'Загрузка…' : '30 дней' }}
            </button>
          </div>
        </div>
        <CabinetNav />

        <p v-if="data && data.links_count === 0" class="hint">
          Нет связей Директ ↔ Метрика — визиты и цели по аккаунтам появятся после склейки в кабинете.
        </p>

        <div class="filters">
          <div class="field">
            <label>С</label>
            <input v-model="dateFrom" type="date" @change="load" />
          </div>
          <div class="field">
            <label>По</label>
            <input v-model="dateTo" type="date" @change="load" />
          </div>
          <div class="field">
            <label>Аккаунт</label>
            <select v-model="accountId" @change="load">
              <option value="">Все</option>
              <option v-for="a in accounts" :key="a.id" :value="String(a.id)">
                {{ a.name || a.client_login }}
              </option>
            </select>
          </div>
          <div class="field">
            <label>Цель Метрики</label>
            <select v-model="goalRowId" @change="load">
              <option value="">Любые цели</option>
              <option v-for="g in goals" :key="g.id" :value="String(g.id)">
                {{ g.name || `Цель ${g.goal_id}` }}
              </option>
            </select>
          </div>
          <button class="btn btn--ghost" type="button" :disabled="!data?.by_day?.length" @click="exportCsv">
            CSV
          </button>
        </div>

        <div v-if="data" class="stats-grid">
          <div class="stat-card">
            <small>Расход</small>
            <strong>{{ formatMoneyKopecks(data.totals.cost_kopecks) }}</strong>
          </div>
          <div class="stat-card">
            <small>Клики</small>
            <strong>{{ data.totals.clicks }}</strong>
          </div>
          <div class="stat-card">
            <small>Конверсии Директа</small>
            <strong>{{ data.totals.conversions }}</strong>
          </div>
          <div class="stat-card">
            <small>CPA (Директ)</small>
            <strong>{{ formatMoneyKopecks(data.totals.cpa_kopecks) }}</strong>
          </div>
          <div class="stat-card">
            <small>Визиты Метрики</small>
            <strong>{{ data.totals.visits }}</strong>
          </div>
          <div class="stat-card">
            <small>Цели Метрики</small>
            <strong>{{ data.totals.goal_reaches }}</strong>
          </div>
          <div class="stat-card">
            <small>CPA по целям</small>
            <strong>{{ formatMoneyKopecks(data.totals.cpa_goal_kopecks) }}</strong>
          </div>
        </div>

        <section v-if="data?.by_day?.length" class="panel">
          <div class="panel__head">
            <h2>Тренд по дням</h2>
            <div class="field" style="margin: 0; min-width: 140px">
              <label>Метрика</label>
              <select v-model="chartMetric">
                <option value="cost_kopecks">Расход</option>
                <option value="clicks">Клики</option>
                <option value="conversions">Конверсии</option>
                <option value="visits">Визиты</option>
                <option value="goal_reaches">Цели</option>
              </select>
            </div>
          </div>
          <TrendChart
            :points="data.by_day"
            :value-key="chartMetric"
            :format-value="chartFormat"
            :label="`Тренд: ${chartMetric}`"
          />
        </section>

        <section class="panel">
          <h2>По аккаунтам</h2>
          <div v-if="data?.by_account?.length" class="list">
            <div v-for="row in data.by_account" :key="row.id" class="list-row">
              <div>
                <strong>{{ row.name || row.client_login }}</strong>
                <small>
                  {{ row.clicks }} кликов · {{ row.conversions }} конв. Директа
                  <template v-if="row.links">
                    · {{ row.visits }} визитов · {{ row.goal_reaches }} целей
                  </template>
                  <template v-else> · нет связи с Метрикой</template>
                </small>
              </div>
              <strong>{{ formatMoneyKopecks(row.cost_kopecks) }}</strong>
            </div>
          </div>
          <p v-else class="empty">Нет данных. Загрузите статистику за 7 или 30 дней.</p>
        </section>

        <section class="panel">
          <h2>По кампаниям</h2>
          <div v-if="data?.by_campaign?.length" class="list">
            <div v-for="row in data.by_campaign" :key="`${row.account_id}-${row.campaign_id}`" class="list-row">
              <div>
                <strong>{{ row.name }}</strong>
                <small>{{ row.client_login }} · {{ row.clicks }} кликов · {{ row.conversions }} конв.</small>
              </div>
              <strong>{{ formatMoneyKopecks(row.cost_kopecks) }}</strong>
            </div>
          </div>
          <p v-else class="empty">Нет данных по кампаниям.</p>
        </section>

        <section class="panel">
          <h2>Топы структуры</h2>
          <p class="hint">{{ data?.tops_note || 'Синк статистики на странице кампании.' }}</p>
          <h3 class="subhead">Ключи</h3>
          <div v-if="data?.top_keywords?.length" class="list">
            <div v-for="(row, i) in data.top_keywords" :key="`kw-${i}`" class="list-row">
              <div>
                <strong>{{ row.name }}</strong>
                <small>
                  {{ row.campaign_name }} · {{ row.adgroup_name }} ·
                  {{ row.clicks }} кликов · {{ row.conversions }} конв.
                </small>
              </div>
              <strong>{{ formatMoneyKopecks(row.cost_kopecks) }}</strong>
            </div>
          </div>
          <p v-else class="empty">Нет топа ключей.</p>

          <h3 class="subhead">Группы</h3>
          <div v-if="data?.top_adgroups?.length" class="list">
            <div v-for="(row, i) in data.top_adgroups" :key="`ag-${i}`" class="list-row">
              <div>
                <strong>{{ row.name }}</strong>
                <small>{{ row.campaign_name }} · {{ row.clicks }} кликов · {{ row.conversions }} конв.</small>
              </div>
              <strong>{{ formatMoneyKopecks(row.cost_kopecks) }}</strong>
            </div>
          </div>
          <p v-else class="empty">Нет топа групп.</p>

          <h3 class="subhead">Поисковые запросы</h3>
          <div v-if="data?.top_queries?.length" class="list">
            <div v-for="(row, i) in data.top_queries" :key="`q-${i}`" class="list-row">
              <div>
                <strong>{{ row.name }}</strong>
                <small>
                  {{ row.campaign_name }} · {{ row.adgroup_name }} ·
                  {{ row.clicks }} кликов · {{ row.conversions }} конв.
                </small>
              </div>
              <strong>{{ formatMoneyKopecks(row.cost_kopecks) }}</strong>
            </div>
          </div>
          <p v-else class="empty">Нет топа запросов.</p>
        </section>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import TrendChart from '../components/TrendChart.vue'
import { errorText, formatMoneyKopecks, jobProgressText, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Аналитика — AiMarketing', true)

const router = useRouter()
const data = ref(null)
const accounts = ref([])
const goals = ref([])
const syncing = ref(false)
const syncLabel = ref('')
const goalRowId = ref('')
const chartMetric = ref('cost_kopecks')

function chartFormat(v) {
  if (chartMetric.value === 'cost_kopecks') return formatMoneyKopecks(v)
  return String(v)
}

function exportCsv() {
  const rows = data.value?.by_day || []
  if (!rows.length) return
  const header = ['day', 'cost_rub', 'clicks', 'conversions', 'visits', 'goal_reaches']
  const lines = [header.join(',')]
  for (const r of rows) {
    lines.push(
      [
        r.day,
        (Number(r.cost_kopecks || 0) / 100).toFixed(2),
        r.clicks,
        r.conversions,
        r.visits,
        r.goal_reaches,
      ].join(','),
    )
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `analytics_${dateFrom.value}_${dateTo.value}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function isoDaysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

const dateFrom = ref(isoDaysAgo(7))
const dateTo = ref(isoDaysAgo(1))
const accountId = ref('')

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    const [analytics, me, g] = await Promise.all([
      webApi.analytics({
        date_from: dateFrom.value,
        date_to: dateTo.value,
        account_id: accountId.value || undefined,
        goal_row_id: goalRowId.value || undefined,
      }),
      webApi.me(),
      webApi.goals(),
    ])
    data.value = analytics
    accounts.value = (me.direct || []).filter((a) => a.enabled)
    goals.value = g.items || []
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function syncStats(days = 1) {
  syncing.value = true
  try {
    const result = await webApi.analyticsSync({ days }, (job) => {
      syncLabel.value = jobProgressText(job)
    })
    const errN = result?.errors || 0
    if (errN) {
      pushToast(`Загружено с ошибками: ${errN} дн.`, 'error')
    } else {
      pushToast(days === 1 ? 'Статистика за вчера обновлена' : `Статистика за ${days} дн. загружена`)
    }
    if (days > 1) {
      dateFrom.value = isoDaysAgo(days)
      dateTo.value = isoDaysAgo(1)
    }
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
    syncLabel.value = ''
  }
}

onMounted(load)
</script>
