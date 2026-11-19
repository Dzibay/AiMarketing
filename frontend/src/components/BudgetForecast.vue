<template>
  <h2 class="section-h">
    <span class="hico" aria-hidden="true"><PiggyBank /></span>Бюджет на месяц
  </h2>
  <div class="ccard bf">
    <template v-if="budget && !editing">
      <p class="bf__lead" :class="tone">{{ lead }}</p>
      <div
        class="bf__bar"
        role="img"
        :aria-label="`Потрачено ${rub(budget.spent_kopecks)} из ${rub(budget.budget_kopecks)}${forecastAria}`"
      >
        <span class="bf__fact" :class="tone" :style="{ width: pct(budget.spent_kopecks) }"></span>
        <span
          v-if="forecastWidth"
          class="bf__forecast"
          :style="{ left: pct(budget.spent_kopecks), width: forecastWidth }"
        ></span>
        <span class="bf__plan" :style="{ left: pct(budget.budget_kopecks) }"></span>
        <span class="bf__today" :style="{ left: pct(todayPlan) }"></span>
      </div>
      <div class="bf__legend">
        <span><i class="bf__dot bf__dot--fact" :class="tone"></i>Потрачено {{ rub(budget.spent_kopecks) }}</span>
        <span v-if="budget.forecast_kopecks != null"><i class="bf__dot bf__dot--forecast"></i>Прогноз {{ approx(budget.forecast_kopecks) }}</span>
        <span><i class="bf__dot bf__dot--plan"></i>План {{ rub(budget.budget_kopecks) }}</span>
        <span><i class="bf__dot bf__dot--today"></i>Сегодня {{ budget.day }}-е из {{ budget.days_in_month }}</span>
      </div>
      <p class="bf__note">{{ note }}</p>
      <div class="bf__acts">
        <template v-if="needsHelp">
          <button class="btn btn--accent btn--sm" type="button" @click="askAi">
            <Sparkles aria-hidden="true" />Попросить ИИ уложиться в бюджет
          </button>
        </template>
        <button class="btn btn--ghost btn--sm" type="button" @click="startEdit">Изменить бюджет</button>
        <small v-if="needsHelp" class="bf__hint">ИИ возьмёт часть лимита, только когда вы нажмёте</small>
      </div>
    </template>

    <form v-else class="bf__form" @submit.prevent="save">
      <p class="bf__label">{{ budget ? 'Новый бюджет месяца' : 'Задайте бюджет месяца' }}</p>
      <p v-if="!budget" class="bf__note">Покажем, уложитесь ли вы в него, и подскажем заранее, если деньги уходят быстрее.</p>
      <div class="bf__row">
        <UiNumber v-model="amount" suffix="₽" placeholder="например, 80 000" aria-label="Бюджет месяца" />
        <button class="btn btn--accent btn--sm" type="submit" :disabled="saving || !canSave">
          {{ saving ? 'Сохраняем…' : 'Сохранить' }}
        </button>
        <button v-if="budget" class="btn btn--ghost btn--sm" type="button" :disabled="saving" @click="editing = false">
          Отмена
        </button>
      </div>
      <small v-if="budget" class="bf__hint">Оставьте поле пустым, чтобы убрать бюджет.</small>
    </form>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { PiggyBank, Sparkles } from '@lucide/vue'
import UiNumber from './ui/UiNumber.vue'
import { errorText, wsApi } from '../api/web'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { pushToast } from '../composables/useToasts'
import { rub } from '../utils/format'

/**
 * Блок «Бюджет на месяц» на главной проекта в обычном режиме: полоса «план — факт — прогноз» с отметкой,
 * сколько по плану должно уйти к сегодняшнему дню. Прогноз сервер считает с учётом дней недели (выходные
 * обычно дешевле), а если истории меньше четырёх недель — поровну по дням.
 * Бюджета нет — поле «Задайте бюджет месяца» прямо здесь; сохраняется в настройки проекта.
 */
const props = defineProps({
  wsId: { type: Number, required: true },
  /** row.budget сводки проекта или null. */
  budget: { type: Object, default: null },
})
const emit = defineEmits(['saved'])

const router = useRouter()
const editing = ref(false)
const saving = ref(false)
const amount = ref(null)

/** Прогноз — не точная сумма: до тысяч рублей, у маленьких сумм — до сотен. */
function rounded(kopecks) {
  const step = kopecks >= 1_000_000 ? 100_000 : 10_000
  return Math.round(kopecks / step) * step
}
function approx(kopecks) {
  return rub(rounded(kopecks))
}

/** Перерасход — по той же округлённой сумме, что в тексте: «около 100 000 ₽ из 100 000 ₽» перерасходом не называем. */
const over = computed(() => {
  const b = props.budget
  return !!b && b.forecast_kopecks != null && rounded(b.forecast_kopecks) > b.budget_kopecks
})
const needsHelp = computed(() => props.budget?.status === 'exhausted' || props.budget?.status === 'over' || over.value)
const tone = computed(() => {
  const s = props.budget?.status
  if (s === 'exhausted') return 'tone-bad'
  if (s === 'over') return 'tone-warn'
  return ''
})

const lead = computed(() => {
  const b = props.budget
  const of = `${rub(b.budget_kopecks)}`
  if (b.status === 'exhausted') return `Бюджет месяца исчерпан: потрачено ${rub(b.spent_kopecks)} из ${of}`
  if (b.forecast_kopecks == null) return `Потрачено ${rub(b.spent_kopecks)} из ${of}`
  if (over.value) return `При таком темпе уйдёт около ${approx(b.forecast_kopecks)} из ${of}`
  return `Уложитесь: при таком темпе уйдёт около ${approx(b.forecast_kopecks)} из ${of}`
})

const note = computed(() => {
  const b = props.budget
  const parts = []
  if (b.status === 'exhausted') parts.push('Дальше расход идёт сверх плана')
  else if (over.value) parts.push(`Чтобы уложиться, тратьте до ${rub(b.daily_allowed_kopecks)} в день`)
  else if (b.status === 'under') parts.push(`Можно тратить до ${rub(b.daily_allowed_kopecks)} в день`)
  if (b.forecast_kopecks == null) parts.push('Прогноз появится завтра, после первого полного дня')
  else if (b.forecast_method === 'weekdays') parts.push('Прогноз учитывает, как расход меняется по дням недели')
  else parts.push('Прогноз поровну по дням: истории расхода пока меньше четырёх недель')
  return parts.join('. ') + '.'
})

/** Шкала полосы — по большему из плана, факта и прогноза, чтобы перерасход было видно за отметкой плана. */
const scale = computed(() => {
  const b = props.budget
  return Math.max(b.budget_kopecks, b.spent_kopecks, b.forecast_kopecks || 0) || 1
})
function pct(kopecks) {
  return `${Math.min(100, Math.max(0, (kopecks / scale.value) * 100)).toFixed(2)}%`
}
/** Сколько по плану должно уйти к концу сегодняшнего дня. */
const todayPlan = computed(() => (props.budget.budget_kopecks * props.budget.day) / props.budget.days_in_month)
const forecastWidth = computed(() => {
  const b = props.budget
  if (b.forecast_kopecks == null || b.forecast_kopecks <= b.spent_kopecks) return ''
  return `${(((b.forecast_kopecks - b.spent_kopecks) / scale.value) * 100).toFixed(2)}%`
})
const forecastAria = computed(() =>
  props.budget.forecast_kopecks != null ? `, прогноз ${approx(props.budget.forecast_kopecks)}` : '',
)

function askAi() {
  const b = props.budget
  const forecast = b.forecast_kopecks != null ? `, при таком темпе к концу месяца уйдёт около ${approx(b.forecast_kopecks)}` : ''
  putHomeQuestion(
    `Бюджет проекта на месяц — ${rub(b.budget_kopecks)}. С начала месяца потрачено ${rub(b.spent_kopecks)}${forecast}. ` +
      'Помоги уложиться в бюджет до конца месяца: где урезать расход, чтобы потерять как можно меньше заявок?',
  )
  router.push({ path: `/w/${props.wsId}/chat`, query: { ask: 'text' } })
}

function startEdit() {
  amount.value = props.budget ? Math.round(props.budget.budget_kopecks / 100) : null
  editing.value = true
}

const canSave = computed(() => (props.budget ? true : Number(amount.value) > 0))

async function save() {
  const value = amount.value === '' || amount.value == null ? null : Number(amount.value)
  if (value != null && !(value > 0)) return
  saving.value = true
  try {
    const updated = await wsApi.update(props.wsId, { monthly_budget_rub: value })
    editing.value = false
    pushToast(value == null ? 'Бюджет месяца убран' : 'Бюджет месяца сохранён')
    emit('saved', updated)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.bf {
  padding: 16px 18px;
}
.bf__lead {
  margin: 0 0 12px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 700;
}
/* Полоса: факт — заливка, прогноз — пунктир после факта, план — вертикальная черта, «сегодня» — засечка. */
.bf__bar {
  position: relative;
  height: 12px;
  margin: 6px 0 10px;
  border-radius: var(--pill);
  background: var(--surface-3);
}
.bf__fact {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: var(--pill);
  background: var(--lime);
}
.bf__fact.tone-warn {
  background: var(--c-amber);
}
.bf__fact.tone-bad {
  background: var(--danger);
}
.bf__forecast {
  position: absolute;
  top: 0;
  bottom: 0;
  border: 2px dashed var(--ink-2);
  border-left: 0;
  border-radius: 0 var(--pill) var(--pill) 0;
  opacity: 0.45;
}
.bf__plan {
  position: absolute;
  top: -5px;
  bottom: -5px;
  width: 2px;
  margin-left: -1px;
  background: var(--ink);
}
.bf__today {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 0;
  margin-left: -1px;
  border-left: 2px dotted var(--muted);
}
.bf__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  color: var(--muted);
  font-size: 13px;
}
.bf__legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.bf__dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.bf__dot--fact {
  background: var(--lime);
}
.bf__dot--fact.tone-warn {
  background: var(--c-amber);
}
.bf__dot--fact.tone-bad {
  background: var(--danger);
}
.bf__dot--forecast {
  border: 2px dashed var(--ink-2);
  opacity: 0.6;
}
.bf__dot--plan {
  width: 2px;
  border-radius: 0;
  background: var(--ink);
}
.bf__dot--today {
  width: 0;
  border-left: 2px dotted var(--muted);
  border-radius: 0;
}
.bf__note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.bf__acts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin-top: 12px;
}
.bf__hint {
  color: var(--faint);
  font-size: 12px;
}
.bf__form .bf__note {
  margin: 0 0 10px;
}
.bf__label {
  margin: 0 0 4px;
  color: var(--ink);
  font-weight: 700;
}
.bf__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.bf__row :deep(.ui-num) {
  width: 180px;
}
.bf__form > .bf__hint {
  display: block;
  margin-top: 8px;
}
</style>
