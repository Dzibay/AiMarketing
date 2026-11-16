<template>
  <!-- «Как дела»: одна строка человеческим языком и три числа. Строку собирает код на сервере, не ИИ. -->
  <section class="hl" :class="{ 'hl--compact': compact }" aria-label="Как дела">
    <div class="hl__line" :class="`is-${tone}`" aria-live="polite">
      <component :is="ICON[tone]" class="hl__icon" aria-hidden="true" />
      <p class="hl__text">{{ data ? data.headline.text : loadError || 'Считаем, как идут дела…' }}</p>
      <template v-if="link">
        <a v-if="link.external" class="hl__act" :href="link.to" target="_blank" rel="noopener">
          {{ link.label }}<ExternalLink aria-hidden="true" />
        </a>
        <button v-else-if="link.ask" class="hl__act" type="button" @click="askAi(link)">
          {{ link.label }}<ArrowRight aria-hidden="true" />
        </button>
        <RouterLink v-else class="hl__act" :to="link.to">{{ link.label }}<ArrowRight aria-hidden="true" /></RouterLink>
      </template>
    </div>

    <template v-if="!compact">
      <div class="hl__bar">
        <div class="seg hl__seg" role="group" aria-label="Период">
          <button
            v-for="d in PERIODS"
            :key="d"
            type="button"
            :class="{ active: period === d }"
            :aria-pressed="period === d"
            @click="setPeriod(d)"
          >
            {{ d }} дней
          </button>
        </div>
        <RouterLink class="hl__more" :to="wsId ? `/w/${wsId}/analytics` : '/cabinet/analytics'">Все отчёты</RouterLink>
      </div>
      <div class="bigstats hl__stats" :class="{ 'is-loading': loading }">
        <div v-for="t in tiles" :key="t.label" class="bigstat">
          <span>{{ t.label }}<TermHint v-if="t.term" :term="t.term" /></span>
          <b><CountUp :value="t.value" /></b>
          <small v-if="t.goal" :class="t.goal.tone">{{ t.goal.text }}</small>
          <small v-else-if="t.goalLink"><RouterLink :to="t.goalLink">Задать цель</RouterLink></small>
          <small :class="t.tone">{{ t.note }}</small>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ArrowRight, CircleCheck, ExternalLink, LoaderCircle, OctagonAlert, TriangleAlert } from '@lucide/vue'
import TermHint from './TermHint.vue'
import CountUp from './ui/CountUp.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { num, pct, rub } from '../utils/format'

/** Без wsId — по всем проектам (главная), с wsId — по проекту. compact — только строка (экспертный режим). */
const props = defineProps({
  wsId: { type: [Number, String], default: null },
  compact: { type: Boolean, default: false },
})

const PERIODS = [7, 30]
const PERIOD_KEY = 'tolk-headline-period'
const ICON = { ok: CircleCheck, warn: TriangleAlert, bad: OctagonAlert, wait: LoaderCircle }

const router = useRouter()
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const period = ref(Number(localStorage.getItem(PERIOD_KEY)) === 30 ? 30 : 7)

const tone = computed(() => (data.value ? data.value.headline.tone : loadError.value ? 'warn' : 'wait'))
const link = computed(() => data.value?.headline.link || null)

/**
 * «на 12% больше, чем за прошлые 7 дней»; inverse — рост это плохо (цена, доля расходов); neutral — без оценки
 * (расход: меньше тратить — не всегда хорошо). Цвета те же, что у бейджей изменений в плитках (DeltaChip).
 */
function versus(value, more, less, inverse = false, neutral = false) {
  const before = `за прошлые ${data.value.numbers.period} дней`
  if (value == null) return { note: `${before} не с чем сравнить`, tone: '' }
  const v = Math.round(value)
  if (!v) return { note: `как ${before}`, tone: '' }
  const bad = inverse ? v > 0 : v < 0
  return { note: `на ${pct(Math.abs(v), 0)} ${v > 0 ? more : less}, чем ${before}`, tone: neutral || Math.abs(v) < 5 ? '' : bad ? 'is-warn' : 'is-ok' }
}

const tiles = computed(() => {
  if (!data.value) {
    return ['Потрачено', 'Заявки', 'Цена заявки'].map((label) => ({ label, value: '—', note: '' }))
  }
  const n = data.value.numbers
  const goal = data.value.goal
  const drr = goal?.type === 'drr'
  const third = drr
    ? { label: 'Доля расходов на рекламу', term: 'drr', value: n.drr != null ? pct(n.drr) : '—', ...versus(n.change.drr, 'больше', 'меньше', true) }
    : { label: 'Цена заявки', term: 'cpa', value: n.cpa != null ? rub(n.cpa) : '—', ...versus(n.change.cpa, 'дороже', 'дешевле', true) }
  if (goal) {
    const target = drr ? pct(goal.target) : rub(goal.target * 100)
    third.goal = { text: `цель — ${target}`, tone: goal.status === 'ok' ? 'is-ok' : 'is-warn' }
  } else {
    third.goalLink = data.value.goal_link
  }
  return [
    { label: 'Потрачено', value: rub(n.cost), ...versus(n.change.cost, 'больше', 'меньше', false, true) },
    { label: 'Заявки', term: 'leads', value: num(n.conversions), ...versus(n.change.conversions, 'больше', 'меньше') },
    third,
  ]
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = props.wsId ? await wsApi.headline(props.wsId, period.value) : await webApi.headline(period.value)
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function setPeriod(d) {
  if (period.value === d) return
  period.value = d
  localStorage.setItem(PERIOD_KEY, String(d))
  load()
}

/** «Что с этим делать» — вопрос с готовым текстом в чат; ИИ отвечает только по этому нажатию. */
function askAi(l) {
  putHomeQuestion(l.ask)
  router.push({ path: l.to, query: { ask: 'text' } })
}

watch(() => props.wsId, load)
onMounted(load)
defineExpose({ load })
</script>

<style scoped>
.hl {
  margin: 0 0 22px;
}

.hl__line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 14px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.hl__line.is-ok {
  border-color: color-mix(in srgb, var(--ok) 35%, var(--line));
}

.hl__line.is-warn {
  border-color: color-mix(in srgb, var(--warn) 45%, var(--line));
  background: var(--warn-soft);
}

.hl__line.is-bad {
  border-color: color-mix(in srgb, var(--danger) 45%, var(--line));
  background: var(--danger-soft);
}

.hl__icon {
  flex: none;
  width: 20px;
  height: 20px;
  color: var(--muted);
}

.is-ok .hl__icon {
  color: var(--ok);
}

.is-warn .hl__icon {
  color: var(--warn);
}

.is-bad .hl__icon {
  color: var(--danger);
}

.is-wait .hl__icon {
  animation: hl-spin 1.2s linear infinite;
}

@keyframes hl-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-wait .hl__icon {
    animation: none;
  }
}

.hl__text {
  flex: 1 1 260px;
  min-width: 0;
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  line-height: 1.4;
}

.hl__act {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}

.hl__act svg {
  width: 15px;
  height: 15px;
}

.hl__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0 10px;
}

.hl__seg {
  margin: 0;
}

.hl__more {
  font-size: 13px;
}

.hl__stats.is-loading {
  opacity: 0.55;
}

.hl__stats .bigstat small + small {
  margin-top: 2px;
}

@media (max-width: 640px) {
  .hl {
    margin-bottom: 16px;
  }

  .hl__line {
    padding: 12px 14px;
  }

  .hl__text {
    flex-basis: calc(100% - 32px);
    font-size: 15px;
  }

  .hl__act {
    margin-left: 32px;
  }

  .hl__bar {
    margin: 10px 0 8px;
  }

  /* Телефон: три числа одной карточкой, строками — подпись с пояснением слева, число справа.
     Так строка и все три числа помещаются на первом экране над нижним меню. */
  .hl__stats {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    border: 1px solid var(--line);
    border-radius: var(--r-card);
    background: var(--surface);
  }

  .hl__stats .bigstat {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 12px;
    padding: 10px 14px;
    border: 0;
    border-radius: 0;
    background: none;
  }

  .hl__stats .bigstat + .bigstat {
    border-top: 1px solid var(--line);
  }

  /* .hl впереди — сильнее общего правила «последняя нечётная плитка строкой» из shell.css. */
  .hl .hl__stats .bigstat > span,
  .hl .hl__stats .bigstat small {
    grid-area: auto / 1;
  }

  .hl .hl__stats .bigstat b {
    grid-area: 1 / 2 / 4 / 3;
    margin-top: 0;
    font-size: 20px;
  }
}
</style>
