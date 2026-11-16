<template>
  <div class="adm-costs">
    <div class="adm-dash__bar adm-costs__bar">
      <AdminPeriod :period="period" :query="query" :steps="tabMeta.chart" @period="setPeriod" @step="onStep" />
      <UiSelect :model-value="kind" :options="KIND_OPTIONS" size="sm" aria-label="Вид расхода" @update:model-value="setKey('kind', $event)" />
      <UiSelect :model-value="source" :options="SOURCE_OPTIONS" size="sm" aria-label="Источник" @update:model-value="setKey('source', $event)" />
    </div>

    <nav class="seg adm-costs__tabs" aria-label="Разделы расходов">
      <button v-for="t in TABS" :key="t.key" type="button" :class="{ active: tab === t.key }" :aria-pressed="tab === t.key" @click="setKey('tab', t.key)">
        {{ t.label }}
      </button>
    </nav>

    <component :is="tabMeta.component" :key="tab" :params="params" @period="period = $event" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminPeriod from './AdminPeriod.vue'
import AiCostsJournal from './AiCostsJournal.vue'
import AiCostsModels from './AiCostsModels.vue'
import AiCostsOverview from './AiCostsOverview.vue'
import AiCostsPeople from './AiCostsPeople.vue'
import AiCostsReliability from './AiCostsReliability.vue'
import AiCostsTasks from './AiCostsTasks.vue'
import { KIND_OPTIONS, SOURCE_OPTIONS } from './aiCosts'
import { useAdminPeriod } from './useAdminPeriod'

/**
 * Расходы на ИИ (docs/admin-plan.md, раздел 6): общий период и фильтры вида и источника, шесть вкладок.
 * Вкладка, вид и источник — в адресе (?tab=…&kind=…&source=…), период — как на дашборде.
 */
const TABS = [
  { key: 'overview', label: 'Обзор', component: AiCostsOverview, chart: true },
  { key: 'models', label: 'Модели', component: AiCostsModels, chart: true },
  { key: 'tasks', label: 'Задачи', component: AiCostsTasks, chart: false },
  { key: 'people', label: 'Люди и тарифы', component: AiCostsPeople, chart: false },
  { key: 'reliability', label: 'Надёжность', component: AiCostsReliability, chart: true },
  { key: 'journal', label: 'Журнал', component: AiCostsJournal, chart: false },
]

const route = useRoute()
const router = useRouter()
const { query, setPeriod, setStep } = useAdminPeriod()
const period = ref(null)

const one = (key) => (Array.isArray(route.query[key]) ? route.query[key][0] : route.query[key]) || ''
const tab = computed(() => (TABS.some((t) => t.key === one('tab')) ? one('tab') : 'overview'))
const tabMeta = computed(() => TABS.find((t) => t.key === tab.value))
const kind = computed(() => (KIND_OPTIONS.some((o) => o.value === one('kind')) ? one('kind') : ''))
const source = computed(() => (SOURCE_OPTIONS.some((o) => o.value === one('source')) ? one('source') : ''))

const params = computed(() => ({ ...query.value, kind: kind.value, source: source.value }))

function setKey(key, value) {
  const next = { ...route.query, [key]: value }
  if (!value || (key === 'tab' && value === 'overview')) delete next[key]
  router.push({ query: next })
}

const autoStep = (days) => (days <= 92 ? 'day' : days <= 400 ? 'week' : 'month')
const onStep = (step) => setStep(step, period.value ? autoStep(period.value.days) : '')
</script>
