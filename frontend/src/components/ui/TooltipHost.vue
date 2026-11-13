<template>
  <div
    v-show="tip.el"
    :id="TIP_ID"
    ref="box"
    class="ui-tip"
    :class="[`is-${side}`, { 'is-rich': tip.title }]"
    role="tooltip"
    :style="style"
  >
    <strong v-if="tip.title">{{ tip.title }}</strong>
    {{ tip.text }}
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { TIP_ID, hideTip, tip } from '../../composables/useTooltip'

/**
 * Общая подсказка для v-tip и TermHint: над элементом, а если сверху мало места — под ним
 * (у v-tip.right — справа). Не шире 280 px и не выходит за края окна. Закрывается прокруткой, сменой размера окна
 * и Esc; закреплённая нажатием — ещё и нажатием мимо.
 */
const GAP = 8
const EDGE = 8

const box = ref(null)
const style = ref({})
const side = ref('top')

function place() {
  const el = tip.el
  const pop = box.value
  if (!el || !pop) return
  if (!el.isConnected) {
    hideTip(el)
    return
  }
  const r = el.getBoundingClientRect()
  const vw = document.documentElement.clientWidth
  const vh = window.innerHeight
  const w = pop.offsetWidth
  const h = pop.offsetHeight
  let at = tip.side
  let left
  let top
  if (at === 'right') {
    left = r.right + GAP
    top = r.top + r.height / 2 - h / 2
    if (left + w > vw - EDGE) at = 'top'
  }
  if (at === 'top') {
    left = r.left + r.width / 2 - w / 2
    top = r.top - GAP - h
    if (top < EDGE) {
      at = 'bottom'
      top = r.bottom + GAP
    }
  }
  side.value = at
  style.value = {
    left: `${Math.round(Math.max(EDGE, Math.min(left, vw - w - EDGE)))}px`,
    top: `${Math.round(Math.max(EDGE, Math.min(top, vh - h - EDGE)))}px`,
  }
}

const close = () => hideTip()

function onKey(event) {
  if (event.key !== 'Escape') return
  // Esc закрывает только подсказку: диалог или меню под ней остаются открытыми.
  event.stopPropagation()
  hideTip()
}

function onPointerDown(event) {
  if (tip.pinned && !tip.el?.contains(event.target)) hideTip()
}

function listen(on) {
  const method = on ? 'addEventListener' : 'removeEventListener'
  window[method]('scroll', close, true)
  window[method]('resize', close)
  window[method]('keydown', onKey, true)
  document[method]('pointerdown', onPointerDown, true)
}

watch(
  () => [tip.el, tip.text, tip.title, tip.side],
  async ([el], [prev]) => {
    if (!el) {
      listen(false)
      return
    }
    if (!prev) listen(true)
    // Сначала невидимо: размер известен только после отрисовки.
    style.value = { visibility: 'hidden', left: '0px', top: '0px' }
    await nextTick()
    place()
  },
)

onBeforeUnmount(() => listen(false))
</script>
