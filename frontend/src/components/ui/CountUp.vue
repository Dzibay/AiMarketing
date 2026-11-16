<template>
  <!-- Диктор читает итоговое число один раз, а не каждый кадр счёта. -->
  <span class="count-up">
    <span class="count-up__sr">{{ value }}</span>
    <span aria-hidden="true">{{ shown }}</span>
  </span>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

/**
 * Число в плитке досчитывается до нового значения: «0 ₽» → «12 340 ₽» за 0,6 с. На вход — уже готовая строка
 * (rub, num, pct): из неё берётся первое число, остальное (₽, %, «≈») остаётся как есть. В конце всегда ровно
 * исходная строка. Строка без числа («—») и prefers-reduced-motion — сразу как есть.
 */
const props = defineProps({ value: { type: String, required: true } })

const DURATION = 600
// Число с разрядами через пробел (обычный, неразрывный или узкий) и дробью через запятую или точку.
const NUMBER = /(\d{1,3}(?:[   ]\d{3})+|\d+)(?:([.,])(\d+))?/

const shown = ref(props.value)
let frame = 0
let guard = 0

function parse(text) {
  const m = NUMBER.exec(text)
  if (!m) return null
  const sep = /[   ]/.exec(m[1])?.[0] || ''
  const decimals = m[3] ? m[3].length : 0
  const number = Number(`${m[1].replace(/\D/g, '')}${decimals ? `.${m[3]}` : ''}`)
  return { head: text.slice(0, m.index), tail: text.slice(m.index + m[0].length), number, sep, dot: m[2] || ',', decimals }
}

function format(p, n) {
  const [int, frac] = n.toFixed(p.decimals).split('.')
  const grouped = p.sep ? int.replace(/\B(?=(\d{3})+(?!\d))/g, p.sep) : int
  return `${p.head}${grouped}${frac ? p.dot + frac : ''}${p.tail}`
}

function reduced() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

watch(
  () => props.value,
  (next, prev) => {
    cancelAnimationFrame(frame)
    clearTimeout(guard)
    const to = parse(next)
    if (!to || reduced()) {
      shown.value = next
      return
    }
    // Считаем от прошлого числа, если оно было; первый показ и «—» → число — от нуля.
    const from = (prev && parse(prev)?.number) || 0
    if (from === to.number) {
      shown.value = next
      return
    }
    const start = performance.now()
    const step = (now) => {
      const t = Math.min(1, (now - start) / DURATION)
      const eased = 1 - (1 - t) ** 3
      shown.value = t < 1 ? format(to, from + (to.number - from) * eased) : next
      if (t < 1) frame = requestAnimationFrame(step)
    }
    shown.value = format(to, from)
    frame = requestAnimationFrame(step)
    // Во вкладке в фоне кадры не идут: итог всё равно встаёт вовремя, а не «0 ₽» до возврата на вкладку.
    guard = setTimeout(() => {
      cancelAnimationFrame(frame)
      shown.value = next
    }, DURATION + 100)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  clearTimeout(guard)
})
</script>

<style scoped>
.count-up {
  font-variant-numeric: tabular-nums;
}
.count-up__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
