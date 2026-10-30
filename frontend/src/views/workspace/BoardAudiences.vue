<template>
  <div class="board-lines board-auds">
    <div class="board-lines__head">
      <span>Аудитории</span>
      <small :class="{ 'tone-bad': onCount > MAX }">{{ onCount }} из {{ MAX }}</small>
    </div>
    <ul class="board-lines__list">
      <li
        v-for="a in items"
        :key="a.text"
        class="board-lines__row board-auds__row"
        :class="{ 'is-pending': a.pending, 'is-off': !board.isOn(a), 'is-own': a.own }"
      >
        <input
          v-if="a.pending"
          type="checkbox"
          :checked="board.isOn(a)"
          :disabled="board.disabled.value"
          :aria-label="a.text"
          @change="board.toggle(a)"
        />
        <span v-else class="board-lines__ok" title="Согласовано" aria-hidden="true">✓</span>
        <div class="board-auds__body">
          <div class="board-auds__name">
            <strong>{{ a.text }}</strong>
            <span class="chip" :class="TYPES[a.type]?.chip" :title="TYPES[a.type]?.hint">
              {{ TYPES[a.type]?.label || 'аудитория' }}
            </span>
            <span v-if="a.lal && a.type !== 'lookalike'" class="chip chip--warning" :title="LAL_HINT">LAL</span>
          </div>
          <small v-if="a.lookalike" class="board-auds__what">
            Похожие на {{ a.lookalike.source }} · похожесть {{ a.lookalike.similarity }} из 5
          </small>
          <small v-else-if="a.value" class="board-auds__what">{{ a.value }}</small>
          <small v-if="a.note" class="board-auds__why">{{ a.note }}</small>
        </div>
        <button
          class="board-x"
          type="button"
          :title="a.pending && !a.added ? 'Снять галочку' : 'Убрать'"
          :aria-label="`Убрать: ${a.text}`"
          :disabled="board.disabled.value"
          @click="board.remove('audience', group, [a])"
        >
          ×
        </button>
      </li>
    </ul>
    <p v-if="reach" class="hint board-auds__reach">{{ reach }}</p>
  </div>
</template>

<script setup>
/**
 * Аудитории группы на доске: ретаргетинг по целям Метрики, готовые условия кабинета (в том числе LAL),
 * интересы и привычки и новый LAL из Яндекс Аудиторий (источник и похожесть). Галочки ждущей таблицы, «убрать»; новые аудитории предлагает ИИ в чате.
 */
import { computed, inject } from 'vue'

const props = defineProps({
  items: { type: Array, required: true },
  group: { type: String, required: true },
  // Фраз в группе с галочкой: от этого зависит, где группа покажется.
  keywords: { type: Number, default: 0 },
})
const board = inject('board')

const MAX = 10
const TYPES = {
  retargeting: {
    label: 'ретаргетинг',
    chip: 'chip--ok',
    hint: 'Люди, которые уже были на сайте и достигли целей Метрики',
  },
  list: { label: 'условие кабинета', chip: 'chip--info', hint: 'Готовое условие подбора аудитории из кабинета Директа' },
  interests: {
    label: 'интересы и привычки',
    chip: '',
    hint: 'Поведение и привычки из справочника Директа — работают только в сетях',
  },
  lookalike: {
    label: 'новый LAL',
    chip: 'chip--warning',
    hint:
      'Похожие на источник (look-alike): сегмент создастся в Яндекс Аудиториях вместе с кампанией и будет готов ' +
      'от двух часов. Похожесть 1–5: больше — ближе к источнику, но меньше охват. Директ сейчас может отказать ' +
      'в нацеливании на LAL — тогда группа создастся без неё.',
  },
}
const LAL_HINT =
  'Похожие на вашу аудиторию (look-alike). Директ сейчас может отказать в нацеливании на LAL — тогда группа создастся без неё.'

const on = computed(() => props.items.filter(board.isOn))
const onCount = computed(() => on.value.length)

const reach = computed(() => {
  if (!on.value.length) return ''
  const narrow = on.value.some((a) => a.type !== 'interests')
  if (!props.keywords) return 'Фраз нет — группа покажется только в сетях, людям из этих аудиторий.'
  const parts = []
  if (narrow) parts.push('на поиске фразы группы покажутся только этим аудиториям')
  parts.push('в сетях — и по фразам, и по аудиториям')
  if (on.value.some((a) => a.type === 'interests')) parts.push('интересы работают только в сетях')
  const text = parts.join('; ')
  return text[0].toUpperCase() + text.slice(1) + '.'
})
</script>
