<template>
  <span ref="root" class="term-hint" :class="{ 'is-open': open }" @mouseenter="show" @mouseleave="hide">
    <!-- span, а не button: подсказка стоит и внутри <label>, где кнопка перехватила бы связь подписи с полем. -->
    <span
      ref="btn"
      class="term-hint__btn"
      role="button"
      tabindex="0"
      :aria-label="`Что значит «${entry.title}»`"
      :aria-expanded="open"
      :aria-describedby="open ? id : undefined"
      @click.prevent="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
      @focus="show"
      @blur="hide"
    >?</span>
    <!-- В body и position: fixed — таблицы с прокруткой и карточки не обрезают пояснение. -->
    <Teleport to="body">
      <span v-if="open" :id="id" class="term-hint__pop" role="tooltip" :style="popStyle">
        <strong>{{ entry.title }}</strong>
        {{ entry.text }}
      </span>
    </Teleport>
  </span>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'

/**
 * Подсказка «?» к термину рекламы: наведение, фокус или нажатие (на телефоне) открывают пояснение.
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
    title: 'CPA',
    text: 'Цена конверсии: расход, делённый на число конверсий по выбранной цели. Чем ниже, тем дешевле обходится заявка или покупка.',
  },
  drr: {
    title: 'ДРР',
    text: 'Доля рекламных расходов: расход на рекламу в процентах от дохода, который она принесла. 20% — на каждые 100 ₽ выручки ушло 20 ₽ рекламы.',
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

const POP_WIDTH = 280
const GAP = 8

const entry = computed(() =>
  props.text ? { title: props.title, text: props.text } : TERMS[props.term] || { title: props.title || props.term, text: '' },
)
const id = `term-hint-${Math.random().toString(36).slice(2, 9)}`
const open = ref(false)
const pinned = ref(false)
const root = ref(null)
const btn = ref(null)
const popStyle = ref({})

function place() {
  const rect = btn.value?.getBoundingClientRect()
  if (!rect) return
  const vw = window.innerWidth
  const half = Math.min(POP_WIDTH, vw * 0.8) / 2
  const center = Math.min(Math.max(rect.left + rect.width / 2, half + GAP), vw - half - GAP)
  // Снизу не помещается — показываем над значком.
  const below = rect.bottom + 6 + 140 < window.innerHeight
  popStyle.value = below
    ? { left: `${center}px`, top: `${rect.bottom + 6}px` }
    : { left: `${center}px`, bottom: `${window.innerHeight - rect.top + 6}px` }
}

function setOpen(value) {
  if (value && !open.value) {
    place()
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    document.addEventListener('click', onDocClick)
    document.addEventListener('keydown', onKey)
  } else if (!value && open.value) {
    unlisten()
  }
  open.value = value
}

function show() {
  setOpen(true)
}

function hide() {
  setOpen(pinned.value)
}

function close() {
  pinned.value = false
  setOpen(false)
}

function toggle() {
  pinned.value = !pinned.value
  setOpen(pinned.value)
}

function onDocClick(event) {
  if (root.value && !root.value.contains(event.target)) close()
}

function onKey(event) {
  if (event.key === 'Escape') close()
}

function unlisten() {
  window.removeEventListener('scroll', close, true)
  window.removeEventListener('resize', close)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
}

onBeforeUnmount(unlisten)
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

.term-hint__pop {
  position: fixed;
  z-index: 1000;
  transform: translateX(-50%);
  width: max-content;
  max-width: min(280px, 80vw);
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow, 0 8px 24px rgb(0 0 0 / 0.12));
  font-size: 0.8rem;
  font-weight: 400;
  line-height: 1.4;
  text-align: left;
  text-transform: none;
  letter-spacing: normal;
  white-space: normal;
  pointer-events: none;
}

.term-hint__pop strong {
  display: block;
  margin-bottom: 0.2rem;
}
</style>
