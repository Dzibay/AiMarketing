<template>
  <div class="form-grid">
    <div class="field">
      <label>Стратегия на поиске</label>
      <select :value="model.search" @change="setSearch($event.target.value)">
        <option v-for="s in opts.search_strategies" :key="s.id" :value="s.id">{{ s.label }}</option>
      </select>
    </div>
    <div class="field">
      <label>В сетях (РСЯ)</label>
      <select :value="model.network" @change="setNetwork($event.target.value)">
        <option v-for="n in networkChoices" :key="n.id" :value="n.id">{{ n.label }}</option>
      </select>
    </div>
    <div v-for="p in params" :key="p.key" class="field">
      <label>
        {{ p.label }}<span v-if="p.required" aria-hidden="true"> *</span>
      </label>
      <select v-if="p.key === 'goal_id'" :value="model.goal_id ?? ''" :required="p.required" @change="set('goal_id', $event.target.value ? Number($event.target.value) : null)">
        <option value="">Выберите цель</option>
        <option v-for="g in goals" :key="g.goal_id" :value="g.goal_id">{{ g.name }} ({{ g.goal_id }})</option>
      </select>
      <input
        v-else
        type="number"
        min="0"
        step="any"
        :required="p.required"
        :value="model[p.key] ?? ''"
        :placeholder="p.key === 'weekly_budget_rub' ? `от ${options.min_weekly_budget_rub || 300}` : ''"
        @input="set(p.key, $event.target.value === '' ? null : Number($event.target.value))"
      />
    </div>
    <p class="field-hint field--wide">
      <template v-if="manual && unified">
        Ручные ставки ЕПК: ставки на ключевых фразах, показы только на поиске. Расход можно ограничить недельным бюджетом.
      </template>
      <template v-else-if="manual">
        Ручные ставки: ставки задаются на ключевых фразах, можно ограничить дневной бюджет.
      </template>
      <template v-else-if="params.length">
        Автостратегия: Директ сам управляет ставками в пределах недельного бюджета. Дневной бюджет не используется.
      </template>
      <template v-if="needsGoal && !goals.length">
        У счётчика нет загруженных целей — обновите цели в настройках пространства.
      </template>
    </p>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
  options: { type: Object, required: true },
  goals: { type: Array, default: () => [] },
  defaultGoalId: { type: Number, default: null },
  // В ЕПК свой набор: ручные ставки только на поиске, с недельным бюджетом.
  campaignType: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const model = computed(() => props.modelValue)
const opts = computed(() => props.options.by_type?.[props.campaignType] || props.options)
const unified = computed(() => props.campaignType === 'UNIFIED_CAMPAIGN')
function networksFor(search) {
  const by = opts.value.network_by_search || {}
  if (search === 'HIGHEST_POSITION') return by.HIGHEST_POSITION || []
  if (search === 'SERVING_OFF') return by.SERVING_OFF || []
  return by.AUTO || []
}

function paramsFor(search, network) {
  if (search === 'SERVING_OFF') return networksFor(search).find((n) => n.id === network)?.params || []
  return (opts.value.search_strategies || []).find((s) => s.id === search)?.params || []
}

const networkChoices = computed(() => networksFor(model.value.search))
// Параметры автостратегии: у поиска, а если поиск выключен — у сетей.
const params = computed(() => paramsFor(model.value.search, model.value.network))
const manual = computed(
  () => model.value.search === 'HIGHEST_POSITION' || (model.value.search === 'SERVING_OFF' && model.value.network === 'MAXIMUM_COVERAGE'),
)
const needsGoal = computed(() => params.value.some((p) => p.key === 'goal_id'))

function emitWith(patch) {
  const next = { ...model.value, ...patch }
  const keys = new Set(['search', 'network', ...paramsFor(next.search, next.network).map((p) => p.key)])
  for (const key of Object.keys(next)) if (!keys.has(key)) delete next[key]
  if (keys.has('goal_id') && next.goal_id == null && props.defaultGoalId) next.goal_id = props.defaultGoalId
  emit('update:modelValue', next)
}

function set(key, value) {
  emitWith({ [key]: value })
}

function setSearch(search) {
  const list = networksFor(search)
  const keep = list.some((n) => n.id === model.value.network)
  emitWith({ search, network: keep ? model.value.network : list[0]?.id || '' })
}

function setNetwork(network) {
  emitWith({ network })
}

// Сменили тип кампании — стратегия могла стать недоступной (ручные ставки в сетях в ЕПК).
watch(
  () => props.campaignType,
  () => {
    const searches = opts.value.search_strategies || []
    const search = searches.some((s) => s.id === model.value.search) ? model.value.search : searches[0]?.id || ''
    setSearch(search)
  },
)
</script>
