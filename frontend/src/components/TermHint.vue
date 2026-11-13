<template>
  <span class="term-hint" :class="{ 'is-open': shown }">
    <!-- span, а не button: подсказка стоит и внутри <label>, где кнопка перехватила бы связь подписи с полем. -->
    <span
      ref="btn"
      class="term-hint__btn"
      role="button"
      tabindex="0"
      :aria-label="`Что значит «${entry.title}»`"
      :aria-expanded="shown"
      @click.prevent="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
      @pointerenter="onEnter"
      @pointerleave="onLeave"
      @focus="show(false)"
      @blur="hideTip(btn)"
    >?</span>
  </span>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { hideTip, showTip, tip } from '../composables/useTooltip'

/**
 * Подсказка «?» к термину рекламы: наведение, фокус или нажатие (на телефоне) открывают пояснение.
 * Пояснение рисует общий TooltipHost; нажатие закрепляет его до нажатия мимо.
 * term — из словаря ниже; title и text — своё пояснение к месту (например, как считается период).
 */
const props = defineProps({
  term: { type: String, default: '' },
  title: { type: String, default: '' },
  text: { type: String, default: '' },
})

const TERMS = {
  // Колонки таблицы проектов на главной.
  spend_today: {
    title: 'Сегодня',
    text: 'Расход за сегодня по данным Директа. Директ отдаёт его с задержкой, поэтому цифра растёт в течение дня.',
  },
  kpi7: {
    title: 'KPI 7 дн.',
    text: 'CPA или ДРР за последние 7 дней против цели проекта. Цель задаётся в настройках проекта.',
  },
  pace: {
    title: 'Бюджет месяца',
    text: 'Темп: прогноз расхода к концу месяца по полным прошедшим дням — в процентах от месячного бюджета. Больше 100 % — бюджета не хватит. Ниже — сколько уже потрачено.',
  },
  balance: {
    title: 'Баланс',
    text: 'Деньги в кабинетах Директа проекта и на сколько дней их хватит при расходе последней недели.',
  },
  kpi: {
    title: 'KPI',
    text: 'Главная цифра проекта: сервис сравнивает её с целевым значением и предупреждает, если реклама идёт хуже плана. CPA — цена заявки или покупки, ДРР — доля выручки, ушедшая на рекламу. Для заявок обычно берут CPA, для магазина с доходом в Метрике — ДРР.',
  },
  cpa: {
    title: 'Цена заявки (CPA)',
    text: 'Расход, делённый на число конверсий по выбранной цели. Чем ниже, тем дешевле обходится заявка или покупка.',
  },
  drr: {
    title: 'Доля расходов на рекламу (ДРР)',
    text: 'Расход на рекламу в процентах от дохода, который она принесла. 20% — на каждые 100 ₽ выручки ушло 20 ₽ рекламы.',
  },
  leads: {
    title: 'Заявки (конверсии)',
    text: 'Сколько раз посетители из рекламы выполнили цель проекта в Метрике: оставили заявку, позвонили или купили. Цель выбирается в настройках проекта.',
  },
  rsya: {
    title: 'РСЯ',
    text: 'Рекламная сеть Яндекса: показы объявлений на сайтах и в приложениях партнёров, а не в поиске. Обычно дешевле клики, но ниже намерение.',
  },
  minus: {
    title: 'Минус-фраза',
    text: 'Слово или фраза, по которым объявление не показывается. Например, «бесплатно» отсекает тех, кто не собирается платить.',
  },
  epk: {
    title: 'ЕПК',
    text: 'Единая перфоманс-кампания Директа: одна кампания сразу для поиска и сетей, Директ сам распределяет показы по площадкам.',
  },
}

const entry = computed(() =>
  props.text ? { title: props.title, text: props.text } : TERMS[props.term] || { title: props.title || props.term, text: '' },
)
const btn = ref(null)
const shown = computed(() => !!btn.value && tip.el === btn.value)

function show(pinned) {
  if (!btn.value || !entry.value.text) return
  showTip(btn.value, { title: entry.value.title, text: entry.value.text, pinned })
}

function toggle() {
  if (shown.value && tip.pinned) hideTip(btn.value)
  else show(true)
}

function onEnter(event) {
  if (event.pointerType === 'mouse') show(false)
}

function onLeave() {
  if (shown.value && !tip.pinned) hideTip(btn.value)
}

onBeforeUnmount(() => {
  if (btn.value) hideTip(btn.value)
})
</script>

<style scoped>
.term-hint {
  display: inline-flex;
  vertical-align: middle;
  margin-left: 0.3rem;
}

.term-hint__btn {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: var(--surface);
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1;
  cursor: help;
  user-select: none;
}

.term-hint__btn:hover,
.term-hint.is-open .term-hint__btn {
  border-color: var(--accent);
  color: var(--accent);
}

.term-hint__btn:focus-visible {
  outline: 2px solid var(--accent-ring, var(--accent));
  outline-offset: 2px;
}
</style>
