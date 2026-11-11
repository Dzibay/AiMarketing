<template>
  <div class="calc">
    <div class="calc__inputs">
      <div v-for="f in fields" :key="f.key" class="calc__field">
        <div class="calc__label">
          <label :for="`calc-${f.key}`">{{ f.label }}</label>
          <output :for="`calc-${f.key}`">{{ f.format(state[f.key]) }}</output>
        </div>
        <input
          :id="`calc-${f.key}`"
          v-model.number="state[f.key]"
          type="range"
          :min="f.min"
          :max="f.max"
          :step="f.step"
          :style="{ '--fill': fill(f) }"
          @change="used()"
        />
        <small v-if="f.note">{{ f.note }}</small>
      </div>
    </div>

    <div v-if="freelance" class="calc__out dark" aria-live="polite">
      <span class="calc__cap">Освободится в месяц</span>
      <strong class="calc__total">≈ {{ freedHours }} ч</strong>
      <ul class="calc__rows">
        <li><span>Это ваших денег по ставке часа</span><b>{{ rub(freedHours * state.rate) }}</b></li>
        <li><span>Можно взять ещё клиентов</span><b>+{{ moreClients }}</b></li>
      </ul>
      <a class="btn btn--lime btn--lg" href="/register" data-goal="signup_click" data-goal-place="calculator_freelance">
        Подключить кабинеты клиентов
      </a>
      <details class="calc__how">
        <summary>Как считаем</summary>
        <p>
          Клиенты × часы в неделю на клиента × 4,3 — столько уходит на чистку, площадки и отчёты. Толк берёт на себя
          примерно 80% этой рутины. Свободные часы делим на часы одного клиента — получаем, сколько ещё клиентов можно
          вести в том же ритме. Это оценка по вашим допущениям, а не обещание.
        </p>
      </details>
    </div>

    <div v-else class="calc__out dark" aria-live="polite">
      <span class="calc__cap">Можно вернуть в месяц</span>
      <strong class="calc__total">≈ {{ rub(total) }}</strong>
      <ul class="calc__rows">
        <li><span>Бюджет на мусорные запросы и площадки</span><b>{{ rub(waste) }}</b></li>
        <li><span>Ваше время на рутину ({{ hoursMonth }} ч)</span><b>{{ rub(timeCost) }}</b></li>
      </ul>
      <a class="btn btn--lime btn--lg" href="/register" data-goal="signup_click" data-goal-place="calculator">
        Найти утечки в своём кабинете
      </a>
      <details class="calc__how">
        <summary>Как считаем</summary>
        <p>
          Бюджет × доля мусорного трафика — столько уходит на запросы и площадки без заявок. Часы в неделю × 4,3 × ставка
          часа — цена ручной чистки и отчётов; Толк сокращает её примерно на 80%. Это оценка по вашим допущениям, а не
          обещание: точную цифру покажет проверка кабинета.
        </p>
      </details>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { goal } from '../lib/goal.js'

const nf = new Intl.NumberFormat('ru-RU')
const rub = (n) => `${nf.format(Math.round(n / 100) * 100)} ₽`

const props = defineProps({
  // default — бюджет и время владельца; freelance — время директолога на клиентов.
  variant: { type: String, default: 'default' },
})
const freelance = props.variant === 'freelance'

const state = reactive({ budget: 150000, junk: 15, hours: 4, rate: 1000, clients: 8, perClient: 3 })

const FREELANCE_FIELDS = [
  { key: 'clients', label: 'Клиентов в работе', min: 1, max: 40, step: 1, format: (v) => `${v}` },
  {
    key: 'perClient',
    label: 'Часов в неделю на одного клиента',
    min: 1,
    max: 10,
    step: 0.5,
    format: (v) => `${String(v).replace('.', ',')} ч`,
    note: 'Чистка запросов, площадки, ставки, отчёты и ответы «что случилось с заявками?».',
  },
  { key: 'rate', label: 'Сколько стоит ваш час', min: 300, max: 5000, step: 100, format: (v) => rub(v) },
]

const DEFAULT_FIELDS = [
  { key: 'budget', label: 'Бюджет на Директ в месяц', min: 20000, max: 2000000, step: 10000, format: rub },
  {
    key: 'junk',
    label: 'Доля мусорного трафика',
    min: 5,
    max: 40,
    step: 1,
    format: (v) => `${v}%`,
    note: 'Допущение: 15% — типичная доля для кампаний без регулярной чистки. Поставьте свою.',
  },
  { key: 'hours', label: 'Часов в неделю на чистку и отчёты', min: 1, max: 20, step: 1, format: (v) => `${v} ч` },
  { key: 'rate', label: 'Сколько стоит ваш час', min: 300, max: 5000, step: 100, format: rub },
]

const fields = freelance ? FREELANCE_FIELDS : DEFAULT_FIELDS

const fill = (f) => `${((state[f.key] - f.min) / (f.max - f.min)) * 100}%`

const waste = computed(() => (state.budget * state.junk) / 100)
const hoursMonth = computed(() => Math.round(state.hours * 4.3))
const timeCost = computed(() => hoursMonth.value * state.rate * 0.8)
const total = computed(() => waste.value + timeCost.value)
const freedHours = computed(() => Math.round(state.clients * state.perClient * 4.3 * 0.8))
const moreClients = computed(() => Math.floor(freedHours.value / (state.perClient * 4.3)))

let sent = false
function used() {
  if (sent) return
  sent = true
  goal('calc_used', { variant: props.variant })
}
</script>

<style scoped>
.calc {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}
.calc__inputs {
  display: grid;
  gap: 26px;
  padding: clamp(22px, 3vw, 34px);
  border-radius: 28px;
  background: var(--surface);
  border: 1px solid var(--line);
}
.calc__field {
  display: grid;
  gap: 10px;
}
.calc__label {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}
.calc__label output {
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.calc__field small {
  color: var(--muted);
  font-size: 13px;
}
input[type='range'] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 8px;
  border-radius: 99px;
  background: linear-gradient(to right, #141413 var(--fill), var(--surface-3) var(--fill));
  cursor: pointer;
}
input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--lime);
  border: 3px solid #141413;
}
input[type='range']::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--lime);
  border: 3px solid #141413;
}
input[type='range']:focus-visible {
  outline: 3px solid var(--lime);
  outline-offset: 4px;
}

.calc__out {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  align-content: start;
  padding: clamp(22px, 3vw, 34px);
  border-radius: 28px;
  background: #141413;
  color: var(--ink);
}
.calc__cap {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.calc__total {
  font-size: clamp(44px, 6vw, 68px);
  line-height: 1;
  letter-spacing: -0.04em;
  color: var(--lime);
  font-variant-numeric: tabular-nums;
}
.calc__rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.calc__rows li {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
  color: var(--ink-2);
}
.calc__rows b {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.calc__out .btn {
  width: fit-content;
  max-width: 100%;
  white-space: normal;
  text-align: center;
}
.calc__how summary {
  cursor: pointer;
  font-weight: 700;
  color: var(--ink-2);
}
.calc__how p {
  margin-top: 8px;
  color: var(--muted);
  font-size: 14px;
}
@media (max-width: 860px) {
  .calc {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
