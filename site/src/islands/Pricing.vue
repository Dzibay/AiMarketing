<template>
  <div class="pr">
    <div v-if="hasTerms" class="pr__terms" role="radiogroup" aria-label="Срок оплаты">
      <button
        v-for="t in TERMS"
        :key="t.months"
        type="button"
        role="radio"
        :aria-checked="term === t.months"
        :class="{ 'is-on': term === t.months }"
        @click="term = t.months"
      >
        {{ t.label }}<span v-if="maxDiscount(t.months)" class="pr__save">−{{ maxDiscount(t.months) }}%</span>
      </button>
    </div>

    <div class="pr__grid" :class="{ 'pr__grid--few': cards.length < 3 }">
      <article v-if="catalog.trial" class="pr__card">
        <span class="chip">Пробный период</span>
        <h3>Бесплатно</h3>
        <p class="pr__price"><strong>0 ₽</strong><span>{{ days(catalog.trial.days) }}</span></p>
        <ul>
                    <li>{{ projects(catalog.trial.projects_limit) }}</li>
          <li>Без карты</li>
        </ul>
        <a class="btn" href="/register" data-goal="signup_click" data-goal-place="pricing_trial">Начать бесплатно</a>
      </article>

      <article v-for="p in cards" :key="p.id" class="pr__card" :class="{ 'pr__card--hot dark': p.id === hotId }">
        <span class="chip" :class="{ 'chip--lime': p.id === hotId }">{{ p.id === hotId ? 'Выбирают чаще' : 'Тариф' }}</span>
        <h3>{{ p.name }}</h3>
        <p class="pr__price">
          <strong>{{ rub(monthly(p)) }}</strong><span>в месяц</span>
          <s v-if="discount(p)">{{ rub(p.price_rub) }}</s>
        </p>
        <p v-if="term > 1" class="pr__bill">{{ rub(monthly(p) * term) }} за {{ term }} мес.</p>
        <ul>
          <li>{{ units(p) }}</li>
          <li>{{ projects(p.projects_limit) }}</li>
          <li v-for="f in featureList(p)" :key="f">{{ catalog.features[f] || f }}</li>
        </ul>
        <a
          class="btn"
          :class="{ 'btn--lime': p.id === hotId }"
          href="/register"
          data-goal="signup_click"
          :data-goal-place="`pricing_${p.id}`"
        >
          Начать с пробного
        </a>
      </article>

      <article v-if="!cards.length && !catalog.trial" class="pr__card pr__card--empty">
        <h3>Тарифы обновляются</h3>
        <p>Начните с бесплатного пробного периода — актуальные цены увидите в кабинете.</p>
        <a class="btn btn--lime" href="/register" data-goal="signup_click" data-goal-place="pricing_empty">Попробовать бесплатно</a>
      </article>
    </div>

    <p class="pr__note">
      Объём ИИ — сколько вопросов, разборов и генераций входит в месяц. Мы не считаем токены: старший тариф просто даёт
      в несколько раз больше, чем базовый.
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({
  // Каталог, полученный при сборке (может быть пустым, если API было недоступно).
  initial: { type: Object, default: () => ({ items: [], features: {}, trial: null }) },
})

const TERMS = [
  { months: 1, label: 'Месяц' },
  { months: 3, label: '3 месяца' },
  { months: 12, label: 'Год' },
]

const catalog = ref(props.initial)
const term = ref(1)

const cards = computed(() => catalog.value.items || [])
const hotId = computed(() => (cards.value.length >= 2 ? cards.value[1].id : null))
const hasTerms = computed(() => cards.value.some((p) => TERMS.some((t) => discount(p, t.months))))

const nf = new Intl.NumberFormat('ru-RU')
const rub = (n) => `${nf.format(Math.round(n))} ₽`

function discount(p, months = term.value) {
  return Number(p.term_discounts?.[String(months)] || 0)
}
function maxDiscount(months) {
  return Math.round(Math.max(0, ...cards.value.map((p) => discount(p, months))))
}
const featureList = (p) =>
  Array.isArray(p.features) ? p.features : Object.keys(p.features || {}).filter((k) => p.features[k])
const monthly = (p) => p.price_rub * (1 - discount(p) / 100)

function plural(n, one, few, many) {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}
const days = (n) => `${n} ${plural(n, 'день', 'дня', 'дней')}`
const projects = (n) => (n ? `До ${n} ${plural(n, 'проекта', 'проектов', 'проектов')}` : 'Проекты без ограничений')
function units(p) {
  if (p.units_unlimited) return 'Объём ИИ без ограничений'
  if (!p.units_factor || p.units_factor === 1) return 'Базовый объём ИИ'
  return `Объём ИИ ×${String(p.units_factor).replace('.', ',')}`
}

onMounted(async () => {
  try {
    const r = await fetch('/api/site/plans', { headers: { Accept: 'application/json' } })
    if (r.ok) {
      const data = await r.json()
      if (data && Array.isArray(data.items)) catalog.value = data
    }
  } catch {
    // Остаёмся на каталоге со сборки.
  }
})
</script>

<style scoped>
.pr {
  display: grid;
  gap: 22px;
}
.pr__terms {
  display: inline-flex;
  justify-self: center;
  gap: 4px;
  padding: 5px;
  border-radius: var(--pill);
  background: var(--surface-3);
}
.pr__terms button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border: 0;
  border-radius: var(--pill);
  background: transparent;
  font: inherit;
  font-weight: 700;
  color: var(--ink-2);
  cursor: pointer;
}
.pr__terms button.is-on {
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow);
}
.pr__save {
  font-size: 12px;
  padding: 1px 7px;
  border-radius: var(--pill);
  background: var(--lime);
  color: #141413;
}
.pr__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}
.pr__grid--few {
  max-width: 780px;
  margin-inline: auto;
  width: 100%;
}
.pr__card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 26px;
  border-radius: 26px;
  background: var(--surface);
  border: 1px solid var(--line);
}
.pr__card--hot {
  background: #141413;
  color: var(--ink);
  box-shadow: var(--shadow-pop);
}
.pr__card--empty {
  text-align: center;
  align-items: center;
}
.pr__card .chip {
  width: fit-content;
}
.pr__card h3 {
  font-size: 26px;
}
.pr__price {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px 8px;
}
.pr__price strong {
  font-size: 38px;
  letter-spacing: -0.035em;
  font-variant-numeric: tabular-nums;
}
.pr__price span,
.pr__price s,
.pr__bill {
  color: var(--muted);
  font-size: 14px;
}
.pr__card ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
  align-content: start;
  flex: 1;
}
.pr__card li {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 8px;
  font-size: 15px;
  color: var(--ink-2);
}
.pr__card li::before {
  content: '✓';
  font-weight: 800;
  color: var(--ok);
}
.pr__card--hot li::before {
  color: var(--lime);
}
.pr__card .btn {
  justify-content: center;
  width: 100%;
  padding-inline: 14px;
  font-size: 15px;
  margin-top: 6px;
}
.pr__note {
  max-width: 720px;
  justify-self: center;
  text-align: center;
  color: var(--muted);
  font-size: 14px;
}
</style>
