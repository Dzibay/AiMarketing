<template>
  <p class="ws-head__crumbs">
    <RouterLink :to="`/w/${ws.id}/campaigns`">← Все кампании</RouterLink>
  </p>

  <div v-if="loadError" class="panel">
    <p class="empty">{{ loadError }}</p>
    <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/campaigns`">К списку</RouterLink>
  </div>

  <template v-else>
    <section class="panel campaign-head">
      <div class="panel__head">
        <div>
          <h2>{{ row?.name || campaign?.name || 'Кампания' }}</h2>
          <p class="ws-head__meta">
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
          <RouterLink
            class="btn btn--accent"
            :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'campaign', cid: String(campaignId), name: row?.name || campaign?.name || undefined } }"
          >
            Разобрать с ИИ
          </RouterLink>
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
import CampaignStructure from '../../components/CampaignStructure.vue'
import { errorText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  CAMPAIGN_STATES,
  CAMPAIGN_STATUSES,
  CAMPAIGN_TYPES,
  lastDays,
  num,
  pct,
  rub,
  rubExact,
  rubles,
} from '../../utils/format'
import CampaignSettingsForm from './CampaignSettingsForm.vue'
import DuplicateDialog from './DuplicateDialog.vue'
import { useWs } from './context'

const route = useRoute()
const router = useRouter()
const { ws, reload: reloadWs } = useWs()
const campaignId = computed(() => Number(route.params.cid))
const row = ref(null)
const campaign = ref(null)
const loadError = ref('')
const busy = ref(false)
const duplicateOpen = ref(false)
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
    row.value = data.items.find((c) => c.campaign_row_id === campaignId.value) || null
    if (!row.value) loadError.value = 'Кампания не входит в это пространство. Добавьте её в настройках пространства.'
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
    loadError.value = ''
    loadRow()
  },
  { immediate: true },
)
</script>
