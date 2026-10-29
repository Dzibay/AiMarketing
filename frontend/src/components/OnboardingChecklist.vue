<template>
  <section v-if="state?.visible" class="panel onboarding" :class="{ 'is-compact': !open }">
    <div class="onboarding__head">
      <h2>С чего начать</h2>
      <div
        class="onboarding__bar"
        role="progressbar"
        :aria-valuenow="requiredDone"
        aria-valuemin="0"
        :aria-valuemax="required.length"
        aria-label="Пройдено шагов"
      >
        <span :style="{ width: `${progressPct}%` }"></span>
      </div>
      <span class="onboarding__count">{{ requiredDone }} из {{ required.length }}</span>
      <span v-if="!open && nextStep" class="onboarding__next">Дальше: {{ nextStep.title }}</span>
      <div class="row-actions">
        <RouterLink v-if="!open && nextStep?.to" class="btn btn--accent" :to="nextStep.to">Продолжить</RouterLink>
        <button class="btn btn--ghost" type="button" :aria-expanded="open" @click="setOpen(!open)">
          {{ open ? 'Свернуть' : 'Все шаги' }}
        </button>
      </div>
      <button
        class="icon-btn onboarding__close"
        type="button"
        aria-label="Скрыть чек-лист"
        title="Скрыть"
        :disabled="busy"
        @click="setDismissed(true)"
      >
        <X aria-hidden="true" />
      </button>
    </div>
    <p v-if="open" class="hint">Шаги отмечаются сами по данным кабинета.</p>
    <ol v-if="open" class="checklist">
      <li
        v-for="(step, i) in state.steps"
        :key="step.id"
        :class="[`is-${step.status}`, { done: step.done }]"
      >
        <span class="checklist__mark" aria-hidden="true">
          <Check v-if="step.done" />
          <LoaderCircle v-else-if="step.status === 'progress'" class="onboarding__spin" />
          <CircleAlert v-else-if="step.status === 'error'" />
          <template v-else>{{ i + 1 }}</template>
        </span>
        <div>
          <strong>
            {{ step.title }}
            <span v-if="step.optional" class="chip">по желанию</span>
          </strong>
          <small>{{ step.hint }}</small>
          <small v-if="!step.done && step.detail" class="onboarding__detail" :class="DETAIL_TONES[step.status]">
            {{ step.detail }}
          </small>
        </div>
        <RouterLink v-if="!step.done && step.to && step.status !== 'blocked'" class="btn btn--ghost" :to="step.to">
          {{ step.status === 'progress' || step.status === 'error' ? 'Посмотреть' : 'Открыть' }}
        </RouterLink>
      </li>
    </ol>
  </section>
  <p v-else-if="justDismissed" class="sync-status onboarding__undo" role="status">
    Чек-лист первых шагов скрыт.
    <button class="btn btn--ghost" type="button" :disabled="busy" @click="setDismissed(false)">Вернуть</button>
  </p>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, CircleAlert, LoaderCircle, X } from '@lucide/vue'
import { errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'

// Пока идёт первая загрузка кабинета — перечитываем чек-лист, чтобы шаг отметился сам.
const POLL_MS = 15000
const DETAIL_TONES = { error: 'tone-bad', progress: 'tone-warn' }
const OPEN_KEY = 'aimarketing-onboarding-open'

const emit = defineEmits(['advanced'])

const state = ref(null)
const busy = ref(false)
const justDismissed = ref(false)
// Выбор «Все шаги / Свернуть» запоминаем; пока его нет — список открыт только до первого сделанного шага.
const openChoice = ref(localStorage.getItem(OPEN_KEY))
let timer = 0

const required = computed(() => (state.value?.steps || []).filter((s) => !s.optional))
const requiredDone = computed(() => required.value.filter((s) => s.done).length)
const progressPct = computed(() =>
  required.value.length ? Math.round((requiredDone.value / required.value.length) * 100) : 0,
)
const open = computed(() => (openChoice.value == null ? requiredDone.value === 0 : openChoice.value === '1'))
const nextStep = computed(() => {
  const todo = (state.value?.steps || []).filter((s) => !s.done && s.status !== 'blocked')
  return todo.find((s) => !s.optional) || todo[0] || null
})

function setOpen(value) {
  openChoice.value = value ? '1' : '0'
  localStorage.setItem(OPEN_KEY, openChoice.value)
}

function schedule() {
  window.clearTimeout(timer)
  if (state.value?.visible && state.value.steps.some((s) => s.status === 'progress')) {
    timer = window.setTimeout(poll, POLL_MS)
  }
}

async function load() {
  try {
    state.value = await webApi.onboarding()
  } catch {
    // Чек-лист — подсказка: без него обзор работает, поэтому молча не показываем.
    state.value = null
  }
  schedule()
}

async function poll() {
  const before = state.value?.done ?? 0
  await load()
  if ((state.value?.done ?? 0) > before) emit('advanced')
}

async function setDismissed(dismissed) {
  busy.value = true
  try {
    state.value = dismissed ? await webApi.dismissOnboarding() : await webApi.restoreOnboarding()
    justDismissed.value = dismissed
    schedule()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<style scoped>
/* Свёрнутый чек-лист — одна строка: прогресс, следующий шаг и кнопка «Продолжить». */
.onboarding__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem 1rem;
}

.onboarding__head h2 {
  margin: 0;
}

.onboarding__head .row-actions {
  margin-left: auto;
}

.onboarding__close {
  width: 38px;
  height: 38px;
  margin-left: -0.4rem;
}

.onboarding:not(.is-compact) .onboarding__head {
  margin-bottom: 0.35rem;
}

.onboarding.is-compact {
  padding-block: 0.85rem;
}

.onboarding__count {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.onboarding__next {
  color: var(--muted);
  font-size: 0.92rem;
}

.onboarding__bar {
  flex: 0 1 160px;
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}

.onboarding__bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.4s ease;
}

.checklist li.is-blocked {
  opacity: 0.7;
}

.checklist__mark svg {
  width: 16px;
  height: 16px;
}

.checklist li.is-error .checklist__mark {
  color: var(--danger);
  background: var(--danger-soft);
}

.checklist li strong .chip {
  margin-left: 0.35rem;
  vertical-align: middle;
}

.onboarding__undo {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}

.onboarding__detail {
  display: block;
  margin-top: 0.15rem;
}

.onboarding__spin {
  animation: onboarding-spin 1.1s linear infinite;
}

@keyframes onboarding-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .onboarding__spin {
    animation: none;
  }
}

@media (max-width: 640px) {
  /* На телефоне: заголовок, счётчик и крестик — первой строкой, полоса и «Дальше» — под ними. */
  .onboarding__head {
    gap: 0.55rem 0.75rem;
  }

  .onboarding__count {
    order: 1;
  }

  .onboarding__close {
    order: 2;
    margin-left: auto;
  }

  .onboarding__bar {
    order: 3;
    flex-basis: 100%;
  }

  .onboarding__next {
    order: 4;
    flex-basis: 100%;
  }

  .onboarding__head .row-actions {
    order: 5;
    margin-left: 0;
  }

  .checklist li {
    flex-wrap: wrap;
  }

  .checklist li > .btn {
    margin-left: calc(28px + 0.75rem);
  }
}
</style>
