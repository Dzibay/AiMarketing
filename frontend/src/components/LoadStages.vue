<template>
  <ul class="load-stages">
    <li v-for="s in stages" :key="s.key" class="load-stage" :class="`is-${kind(s)}`" :title="hint(s)">
      <span class="load-stage__icon" aria-hidden="true">
        <span v-if="kind(s) === 'running'" class="spinner"></span>
        <template v-else>{{ ICONS[kind(s)] }}</template>
      </span>
      <span class="load-stage__title">{{ s.title }}</span>
      <small v-if="detail(s)" class="load-stage__detail">{{ detail(s) }}</small>
    </li>
  </ul>
</template>

<script setup>
import { stageErrorText } from '../composables/useLoads'
import { num, timeAgo } from '../utils/format'

/** Этапы загрузки кабинета или счётчика: что уже есть, что грузится, что ждёт. */
const props = defineProps({
  stages: { type: Array, default: () => [] },
  // Идёт задача загрузки: этап «running» без задачи — прерванный, его повторит следующая загрузка.
  active: { type: Boolean, default: false },
})

const ICONS = { ok: '✓', pending: '○', waiting: '○', units: '⏸', error: '!', stopped: '■' }

function kind(s) {
  if (s.status === 'running') return props.active ? 'running' : 'waiting'
  if (s.status === 'error') return s.error === 'units' ? 'units' : 'error'
  if (s.status === 'ok' && s.skipped === 'units') return 'units'
  return s.status
}

function detail(s) {
  switch (kind(s)) {
    case 'running':
      return s.total ? `${num(s.done)}/${num(s.total)}` : 'идёт'
    case 'ok':
      return timeAgo(s.at)
    case 'waiting':
      return 'прервано, повторим'
    case 'units':
      return 'ждём баллы'
    case 'error':
      return 'ошибка'
    case 'stopped':
      return 'остановлено'
    default:
      return props.active ? 'в очереди' : ''
  }
}

function hint(s) {
  const parts = []
  if (s.status === 'error' || s.status === 'stopped') parts.push(stageErrorText(s))
  if (s.skipped === 'units') parts.push('Баллов API Директа мало — проверку изменений отложили до следующего часа.')
  if (s.goals_skipped?.length) {
    parts.push(`Метрика не приняла цели ${s.goals_skipped.join(', ')} — похоже, их удалили из счётчика.`)
  }
  if (s.failed) parts.push(`Не прочитались кампании: ${s.failed} — повторим при следующей загрузке.`)
  if (s.count) parts.push(`Записей: ${num(s.count)}`)
  return parts.filter(Boolean).join(' ')
}
</script>
