<template>
  <details v-if="items.length" class="ccard snoozed">
    <summary class="snoozed__sum">
      <ChevronRight class="snoozed__chev" :size="16" aria-hidden="true" />
      Скрыто: {{ items.length }}
    </summary>
    <div v-for="h in items" :key="h.id" class="prow">
      <div class="prow__main">
        <b>{{ h.title }}</b>
        <p>{{ lineOf(h) }}</p>
      </div>
      <div class="prow__acts">
        <button class="btn btn--ghost btn--sm" type="button" :disabled="busy !== null" @click="unhide(h)">
          <Eye :size="14" aria-hidden="true" />{{ busy === h.id ? 'Возвращаем…' : 'Показать снова' }}
        </button>
      </div>
    </div>
  </details>
</template>

<script setup>
import { ref } from 'vue'
import { ChevronRight, Eye } from '@lucide/vue'
import { errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { niceQuotes } from '../utils/format'

/**
 * Свёрнутая строка «Скрыто: N» под сигналами: что скрыто, до каких пор, и «Показать снова».
 * items — строки hidden из сводки (уже отобранные под этот список).
 */
defineProps({
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])

const busy = ref(null)

function lineOf(h) {
  return [niceQuotes(h.place), h.note].filter(Boolean).join(' · ')
}

async function unhide(h) {
  busy.value = h.id
  try {
    await webApi.unsnoozeProblem(h.id)
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
    emit('changed')
  } finally {
    busy.value = null
  }
}
</script>

<style scoped>
.snoozed {
  margin-top: 12px;
}
.snoozed__sum {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 18px;
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  list-style: none;
  cursor: pointer;
}
.snoozed__sum::-webkit-details-marker {
  display: none;
}
.snoozed__chev {
  transition: transform 0.15s ease;
}
.snoozed[open] .snoozed__chev {
  transform: rotate(90deg);
}
.snoozed__sum + .prow {
  border-top: 1px solid var(--line);
}
.snoozed .prow__main {
  opacity: 0.8;
}
.snoozed .prow__acts .btn {
  gap: 6px;
}
</style>
