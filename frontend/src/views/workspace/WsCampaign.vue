<template>
  <p class="page-crumbs">
    <RouterLink :to="`/w/${ws.id}/campaigns`">← Все кампании</RouterLink>
  </p>

  <div v-if="loadError" class="panel">
    <p class="empty">{{ loadError }}</p>
    <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/campaigns`">К списку</RouterLink>
  </div>

  <template v-else-if="!expert">
    <section class="camp-head">
      <div class="camp-head__title">
        <p class="camp-head__eyebrow">{{ [CAMPAIGN_TYPES[type] || type, ws.name].filter(Boolean).join(' · ') }}</p>
        <h1>{{ name || 'Кампания' }}</h1>
      </div>
      <div class="row-actions">
        <label v-if="canToggle" class="camp-switch">
          <span class="switch">
            <input
              type="checkbox"
              :checked="state === 'ON'"
              :disabled="busy || !state"
              :aria-label="state === 'ON' ? 'Остановить показы' : 'Запустить показы'"
              @change="toggleState"
            />
          </span>
          {{ state === 'ON' ? 'Идут показы' : 'Остановлена' }}
        </label>
        <RouterLink class="btn btn--accent" :to="aiLink">Разобрать с ИИ</RouterLink>
      </div>
    </section>

    <template v-if="tab === 'settings'">
      <p class="camp-back"><button class="btn btn--ghost" type="button" @click="setTab('structure')">← К кампании</button></p>
      <CampaignSettingsForm :key="`c${campaignId}`" :workspace="ws" :campaign-id="campaignId" :campaign="campaign" @saved="loadRow" />
    </template>

    <template v-else>
      <p v-if="!row" class="empty">Загружаем кампанию…</p>
      <template v-else>
        <p class="camp-lead">
          <span class="st" :class="`st--${health.id}`">
            <component :is="HEALTH_ICONS[health.id]" :size="16" aria-hidden="true" />
            {{ HEALTH_LABELS[health.id] }}
          </span>
          <span>{{ health.text }}</span>
        </p>

        <div class="bigstats">
          <div class="bigstat">
            <span>Потрачено за 7 дней</span>
            <b>{{ rub(row.cost_kopecks || 0) }}</b>
            <small v-if="weekBudget">{{ budgetShare }}% недельного бюджета</small>
            <small v-else>{{ num(row.clicks || 0) }} {{ plural(row.clicks || 0, 'клик', 'клика', 'кликов') }}</small>
          </div>
          <div class="bigstat">
            <span>Конверсии</span>
            <b>{{ num(row.conversions || 0) }}</b>
            <small>из {{ num(row.clicks || 0) }} {{ plural(row.clicks || 0, 'клика', 'кликов', 'кликов') }}</small>
          </div>
          <div class="bigstat">
            <span>{{ kpi?.type === 'drr' ? 'Доля расходов' : 'Цена конверсии' }}</span>
            <b>{{ kpiNow }}</b>
            <small :class="kpiTone">{{ kpiGoal }}</small>
          </div>
        </div>

        <div class="ccard camp-budget">
          <div class="prow">
            <div class="prow__main">
              <b>{{ dailyBudget ? `Бюджет ${rubles(dailyBudget)} в день` : 'Бюджет задан стратегией' }}</b>
              <span class="meta-note">
                <template v-if="weekBudget">
                  За 7 дней потрачено {{ rub(row.cost_kopecks || 0) }} — {{ budgetShare }}% от {{ rubles(weekBudget) }}
                </template>
                <template v-else>Сколько тратить в неделю, Директ решает по настройкам стратегии</template>
              </span>
              <span v-if="weekBudget" class="hbar__track camp-budget__track">
                <span class="hbar__fill" :style="{ width: `${Math.min(budgetShare, 100)}%` }"></span>
              </span>
            </div>
            <div class="prow__acts">
              <button class="btn btn--ghost" type="button" @click="setTab('settings')">Изменить</button>
            </div>
          </div>
        </div>

        <h3 class="section-h">Что внутри <span class="cgroup__n">группы объявлений и их доля расхода</span></h3>
        <div class="ccard">
          <p v-if="structLoading" class="ccard--note">Загружаем группы…</p>
          <p v-else-if="structError" class="ccard--note">{{ structError }}</p>
          <p v-else-if="!groups.length" class="ccard--note">В кампании нет групп объявлений.</p>
          <div v-else class="hbars">
            <div v-for="g in groups" :key="g.id" class="hbar camp-group">
              <div class="hbar__name">
                <b>{{ g.name }}</b>
                <small>{{ g.keywords }} {{ plural(g.keywords, 'фраза', 'фразы', 'фраз') }} · {{ g.ads }} {{ plural(g.ads, 'объявление', 'объявления', 'объявлений') }}</small>
              </div>
              <span class="hbar__track"><span class="hbar__fill" :style="{ width: `${g.share}%` }"></span></span>
              <span class="hbar__val">{{ rub(g.cost) }}</span>
            </div>
          </div>
        </div>

        <h3 class="section-h">
          Недавние изменения
          <RouterLink class="home-link" :to="`/w/${ws.id}/history`">Вся история</RouterLink>
        </h3>
        <div class="ccard">
          <p v-if="!recent.length" class="ccard--note">В этой кампании пока ничего не меняли.</p>
          <div v-for="a in recent" :key="a.id" class="prow">
            <div class="prow__main">
              <b>{{ a.title || a.action }}</b>
              <span class="meta-note">{{ ACTION_SOURCES[a.source] || a.source }} · {{ formatDateTime(a.created_at) }}</span>
            </div>
          </div>
        </div>
      </template>

      <ExpertHint>Объявления, фразы, ставки, модерация, копии и архив</ExpertHint>
    </template>
  </template>

  <template v-else>
    <section class="panel campaign-head">
      <div class="panel__head">
        <div>
          <h2>{{ row?.name || campaign?.name || 'Кампания' }}</h2>
          <p class="ws-meta ws-meta--inline">
            <span v-if="state" class="chip" :class="state === 'ON' ? 'chip--ok' : state === 'SUSPENDED' ? 'chip--warning' : ''">
              {{ CAMPAIGN_STATES[state] || state }}
            </span>
            <span v-if="status" class="chip">{{ CAMPAIGN_STATUSES[status] || status }}</span>
            <span v-if="type">{{ CAMPAIGN_TYPES[type] || type }}</span>
            <span v-if="login">кабинет {{ login }}</span>
            <span v-if="directId">№ {{ directId }}</span>
            <span v-if="dailyBudget != null">бюджет {{ rubles(dailyBudget) }}/день</span>
          </p>
        </div>
        <div class="row-actions">
          <button
            v-if="state === 'ON' || status !== 'DRAFT'"
            class="btn btn--ghost"
            type="button"
            :disabled="busy || !state"
            @click="toggleState"
          >
            {{ state === 'ON' ? 'Остановить' : 'Запустить' }}
          </button>
          <button class="btn btn--ghost" type="button" :disabled="busy" @click="moderate">На модерацию</button>
          <button class="btn btn--ghost" type="button" :disabled="busy" @click="duplicateOpen = true">Копия</button>
          <button v-if="state === 'ARCHIVED'" class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('unarchive')">
            Из архива
          </button>
          <button v-else class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('archive')">В архив</button>
          <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="bulk('delete')">Удалить</button>
          <RouterLink class="btn btn--accent" :to="aiLink">Разобрать с ИИ</RouterLink>
        </div>
      </div>

      <div v-if="row" class="metric-grid">
        <div class="metric-grid__item is-static">
          <small>Расход 7 дн.</small>
          <strong>{{ rub(row.cost_kopecks) }}</strong>
        </div>
        <div class="metric-grid__item is-static">
          <small>Показы</small>
          <strong>{{ num(row.impressions) }}</strong>
        </div>
        <div class="metric-grid__item is-static">
          <small>Клики · CTR</small>
          <strong>{{ num(row.clicks) }}</strong>
          <span>{{ row.ctr != null ? pct(row.ctr, 2) : '—' }}</span>
        </div>
        <div class="metric-grid__item is-static">
          <small>CPC</small>
          <strong>{{ row.cpc_kopecks != null ? rubExact(row.cpc_kopecks) : '—' }}</strong>
        </div>
        <div class="metric-grid__item is-static">
          <small>Конверсии</small>
          <strong :class="{ 'tone-bad': !row.conversions && row.cost_kopecks > 0 }">{{ num(row.conversions) }}</strong>
        </div>
        <div class="metric-grid__item is-static">
          <small>CPA</small>
          <strong>{{ row.cpa_kopecks != null ? rub(row.cpa_kopecks) : '—' }}</strong>
        </div>
      </div>
    </section>

    <nav class="seg seg--tabs" aria-label="Разделы кампании">
      <button type="button" :class="{ active: tab === 'structure' }" @click="setTab('structure')">
        Группы, объявления, фразы
      </button>
      <button type="button" :class="{ active: tab === 'settings' }" @click="setTab('settings')">Настройки кампании</button>
    </nav>

    <CampaignStructure
      v-if="tab === 'structure'"
      :key="`s${campaignId}`"
      :workspace-id="ws.id"
      :campaign-id="campaignId"
      @loaded="campaign = $event"
    />
    <CampaignSettingsForm v-else :key="`c${campaignId}`" :workspace="ws" :campaign-id="campaignId" :campaign="campaign" @saved="loadRow" />

    <DuplicateDialog
      :open="duplicateOpen"
      :workspace="ws"
      :campaign="duplicateSource"
      @close="duplicateOpen = false"
      @done="onDuplicated"
    />
  </template>
</template>

<script setup>
import { askConfirm } from '../../composables/useConfirm'
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { CircleCheck, CircleDashed, CirclePause, OctagonAlert, TriangleAlert } from '@lucide/vue'
import CampaignStructure from '../../components/CampaignStructure.vue'
import ExpertHint from '../../components/ExpertHint.vue'
import { useExpert } from '../../composables/useExpert'
import { campaignHealth } from '../../utils/campaignHealth'
import { errorText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  ACTION_SOURCES,
  CAMPAIGN_STATES,
  CAMPAIGN_STATUSES,
  CAMPAIGN_TYPES,
  formatDateTime,
  lastDays,
  num,
  pct,
  plural,
  rub,
  rubExact,
  rubles,
} from '../../utils/format'
import CampaignSettingsForm from './CampaignSettingsForm.vue'
import DuplicateDialog from './DuplicateDialog.vue'
import { useWs } from './context'

const HEALTH_ICONS = { bad: OctagonAlert, warn: TriangleAlert, ok: CircleCheck, idle: CircleDashed, off: CirclePause }
const HEALTH_LABELS = {
  bad: 'Тратит впустую',
  warn: 'Чуть дороже цели',
  ok: 'Работает хорошо',
  idle: 'Без расхода',
  off: 'Остановлена',
}

const route = useRoute()
const { expert } = useExpert()
const router = useRouter()
const { ws, reload: reloadWs } = useWs()
const campaignId = computed(() => Number(route.params.cid))
const row = ref(null)
const campaign = ref(null)
const loadError = ref('')
const busy = ref(false)
const duplicateOpen = ref(false)
const kpi = ref(null)
const structure = ref(null)
const structLoading = ref(false)
const structError = ref('')
const actions = ref([])
const tab = computed(() => (route.query.tab === 'settings' ? 'settings' : 'structure'))

const state = computed(() => row.value?.state || campaign.value?.state || '')
const status = computed(() => row.value?.status || campaign.value?.status || '')
const type = computed(() => row.value?.type || campaign.value?.campaign_type || '')
const login = computed(() => row.value?.account_login || campaign.value?.client_login || '')
const directId = computed(() => row.value?.campaign_id || campaign.value?.campaign_id || '')
const dailyBudget = computed(() => {
  if (row.value) return row.value.daily_budget_rub
  return campaign.value?.daily_budget_kopecks ? campaign.value.daily_budget_kopecks / 100 : null
})
const name = computed(() => row.value?.name || campaign.value?.name || '')
const aiLink = computed(() => ({
  path: `/w/${ws.value.id}/chat`,
  query: { ask: 'campaign', cid: String(campaignId.value), name: name.value || undefined },
}))
const canToggle = computed(() => state.value !== 'ARCHIVED' && (state.value === 'ON' || status.value !== 'DRAFT'))
const health = computed(() => campaignHealth(row.value || {}, kpi.value))

// Недельный бюджет — семь дневных: столько Директ может потратить за период статистики.
const weekBudget = computed(() => (dailyBudget.value ? dailyBudget.value * 7 : null))
const budgetShare = computed(() =>
  weekBudget.value ? Math.round((row.value?.cost_kopecks || 0) / (weekBudget.value * 100) * 100) : 0,
)

const kpiNow = computed(() => {
  const r = row.value
  if (!r) return '—'
  if (kpi.value?.type === 'drr') return r.drr != null ? pct(r.drr) : '—'
  return r.cpa_kopecks != null ? rub(r.cpa_kopecks) : '—'
})
const kpiGoal = computed(() => {
  const k = kpi.value
  if (!k?.type || k.target == null) return 'цель не задана'
  return k.type === 'cpa' ? `цель — до ${rubles(k.target)}` : `цель — до ${k.target}%`
})
const kpiTone = computed(() => {
  const id = health.value.id
  return id === 'ok' ? 'is-ok' : id === 'warn' || id === 'bad' ? 'is-warn' : ''
})

const groups = computed(() => {
  const s = structure.value
  if (!s) return []
  const count = (list, id) => (list || []).filter((x) => x.adgroup_row_id === id).length
  const total = (s.adgroups || []).reduce((sum, g) => sum + (g.period_cost_kopecks || 0), 0)
  return (s.adgroups || [])
    .map((g) => ({
      id: g.id,
      name: g.name || `Группа ${g.adgroup_id || g.id}`,
      keywords: count(s.keywords, g.id),
      ads: count(s.ads, g.id),
      cost: g.period_cost_kopecks || 0,
      share: total ? Math.round(((g.period_cost_kopecks || 0) / total) * 100) : 0,
    }))
    .sort((a, b) => b.cost - a.cost)
})

function touches(payload, id) {
  if (!payload) return false
  if (Number(payload.campaign_row_id) === id) return true
  return ['campaign_row_ids', 'campaign_ids'].some((k) => (payload[k] || []).map(Number).includes(id))
}
const recent = computed(() => actions.value.filter((a) => touches(a.payload, campaignId.value)).slice(0, 3))

async function loadSimple() {
  if (expert.value) return
  const id = campaignId.value
  structLoading.value = true
  structError.value = ''
  const [s, a] = await Promise.allSettled([
    wsApi.campaign(ws.value.id, id),
    wsApi.actions(ws.value.id, { limit: 100 }),
  ])
  if (id !== campaignId.value) return
  if (s.status === 'fulfilled') {
    structure.value = s.value
    campaign.value = campaign.value || s.value.campaign
  } else structError.value = errorText(s.reason)
  actions.value = a.status === 'fulfilled' ? a.value.items || [] : []
  structLoading.value = false
}

const duplicateSource = computed(() => ({
  id: campaignId.value,
  name: row.value?.name || campaign.value?.name || '',
  account_id: row.value?.account_id || campaign.value?.account_id,
}))

function setTab(name) {
  router.replace({ query: { ...route.query, tab: name === 'structure' ? undefined : name } })
}

async function loadRow() {
  try {
    const data = await wsApi.campaigns(ws.value.id, lastDays(7))
    kpi.value = data.kpi || null
    row.value = data.items.find((c) => c.campaign_row_id === campaignId.value) || null
    if (!row.value) loadError.value = 'Кампания не входит в этот проект. Добавьте её в настройках проекта.'
  } catch (err) {
    loadError.value = errorText(err)
  }
}

async function bulk(action) {
  const name = row.value?.name || campaign.value?.name || ''
  if (action === 'delete' && !await askConfirm(`Удалить кампанию «${name}» в Директе? Удаляются только черновики и кампании без показов.`))
    return
  if (action === 'archive' && !await askConfirm(`Отправить «${name}» в архив?`)) return
  busy.value = true
  try {
    const res = await wsApi.bulk(ws.value.id, { campaign_ids: [campaignId.value], action })
    const error = (res.errors || [])[0]
    if (error) {
      pushToast(error.message || errorText({ code: error.error }), 'error')
      return
    }
    if (action === 'delete') {
      pushToast('Кампания удалена', 'ok')
      reloadWs()
      router.push(`/w/${ws.value.id}/campaigns`)
      return
    }
    pushToast('Готово', 'ok')
    await loadRow()
    if (!expert.value) loadSimple()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function toggleState() {
  return bulk(state.value === 'ON' ? 'pause' : 'resume')
}

async function moderate() {
  busy.value = true
  try {
    const res = await wsApi.moderate(ws.value.id, campaignId.value)
    const errors = res.errors || []
    if (!res.drafts) pushToast('Черновиков нет — всё уже на модерации или принято')
    else
      pushToast(
        `Отправлено на модерацию: ${res.sent} из ${res.drafts}${errors.length ? `. Ошибки: ${errors.slice(0, 2).join('; ')}` : ''}`,
        errors.length ? 'error' : 'ok',
      )
    await loadRow()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function onDuplicated(result) {
  duplicateOpen.value = false
  pushToast(`Копия готова: групп ${result.groups ?? 0}, фраз ${result.keywords ?? 0}, объявлений ${result.ads ?? 0}`, 'ok')
  reloadWs()
  const target = result.target_workspace_id || ws.value.id
  router.push(`/w/${target}/campaigns/${result.campaign.id}`)
}

watch(
  campaignId,
  () => {
    row.value = null
    campaign.value = null
    structure.value = null
    actions.value = []
    loadError.value = ''
    loadRow()
    loadSimple()
  },
  { immediate: true },
)

watch(expert, (on) => {
  if (!on && !structure.value) loadSimple()
})
</script>

<style scoped>
.camp-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 0 14px;
}
.camp-head h1 {
  margin: 2px 0 0;
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.camp-head__eyebrow {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.camp-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.camp-lead {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 0 0 16px;
  font-size: 15px;
}
.camp-lead .st {
  font-size: 14px;
}
.camp-back {
  margin: 0 0 12px;
}
.camp-budget {
  margin-top: 12px;
}
.camp-budget .prow__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.camp-budget .meta-note {
  margin: 0;
}
.camp-budget__track {
  display: block;
  max-width: 420px;
  margin-top: 6px;
}
.camp-group .hbar__name {
  display: flex;
  flex-direction: column;
}
.camp-group small {
  color: var(--faint);
  font-size: 12px;
}
.ccard .prow .meta-note {
  margin: 0;
}
.ccard .prow .prow__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
