<template>
  <!-- Подсказка у элемента (v-coach): не модальная, страницей можно пользоваться как обычно. -->
  <Teleport to="body">
    <section
      v-if="coach.id"
      ref="box"
      class="ui-coach"
      :class="`is-${side}`"
      role="dialog"
      aria-modal="false"
      aria-labelledby="ui-coach-title"
      :style="style"
    >
      <span class="ui-coach__arrow" :style="{ left: `${arrow}px` }" aria-hidden="true"></span>
      <strong id="ui-coach-title">{{ coach.title }}</strong>
      <p>{{ coach.text }}</p>
      <div class="ui-coach__acts">
        <button class="btn btn--accent btn--sm" type="button" @click="closeCoach()">Понятно</button>
        <button class="btn btn--ghost btn--sm" type="button" @click="closeCoach(true)">Больше не показывать</button>
      </div>
    </section>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { closeCoach, coach, coachNewPage, coachRecheck, hideCoach } from '../../composables/useCoach'
import { useProfile } from '../../composables/useProfile'

/** Хост подсказок у элементов: под элементом, а если снизу мало места — над ним; следует за элементом. */
const GAP = 10
const EDGE = 8

const route = useRoute()
const { user } = useProfile()
const box = ref(null)
const style = ref({})
const side = ref('bottom')
const arrow = ref(20)
let frame = 0

function place() {
  frame = 0
  const el = coach.el
  const pop = box.value
  if (!el || !pop) return
  if (!el.isConnected || !el.getClientRects().length) {
    hideCoach()
    return
  }
  const r = el.getBoundingClientRect()
  const w = pop.offsetWidth
  const h = pop.offsetHeight
  const below = r.bottom + GAP + h <= window.innerHeight - EDGE || r.top - GAP - h < EDGE
  side.value = below ? 'bottom' : 'top'
  const left = Math.min(Math.max(r.left + r.width / 2 - w / 2, EDGE), window.innerWidth - w - EDGE)
  const top = below ? r.bottom + GAP : r.top - GAP - h
  arrow.value = Math.min(Math.max(r.left + r.width / 2 - left, 16), w - 16)
  style.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` }
  frame = window.requestAnimationFrame(place)
}

function onKey(event) {
  if (event.key === 'Escape' && coach.id) closeCoach()
}

watch(
  () => coach.el,
  async (el) => {
    window.cancelAnimationFrame(frame)
    frame = 0
    if (!el) return
    await nextTick()
    place()
  },
)
watch(() => route.path, coachNewPage)
watch(() => !!user.value, coachRecheck)

onMounted(() => {
  window.addEventListener('scroll', coachRecheck, { passive: true, capture: true })
  window.addEventListener('keydown', onKey)
  coachNewPage()
})
onBeforeUnmount(() => {
  window.cancelAnimationFrame(frame)
  window.removeEventListener('scroll', coachRecheck, { capture: true })
  window.removeEventListener('keydown', onKey)
  hideCoach()
})
</script>

<style scoped>
.ui-coach {
  position: fixed;
  z-index: 70;
  display: grid;
  gap: 6px;
  width: min(320px, calc(100vw - 16px));
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.16);
}

.ui-coach strong {
  font-size: 15px;
}

.ui-coach p {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.45;
}

.ui-coach__acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}

.ui-coach__arrow {
  position: absolute;
  width: 12px;
  height: 12px;
  margin-left: -6px;
  border: 1px solid var(--line);
  background: var(--surface);
  transform: rotate(45deg);
}

.is-bottom .ui-coach__arrow {
  top: -7px;
  border-right: 0;
  border-bottom: 0;
}

.is-top .ui-coach__arrow {
  bottom: -7px;
  border-top: 0;
  border-left: 0;
}
</style>
