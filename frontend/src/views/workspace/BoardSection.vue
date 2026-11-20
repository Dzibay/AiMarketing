<template>
  <section class="board-section" :data-section="name" :class="{ 'is-waiting': tables.length }">
    <header v-if="!bare || $slots.tools" class="board-section__head">
      <h4 v-if="!bare">{{ title }}</h4>
      <span v-for="s in bare ? [] : stages" :key="s.key" class="chip" :class="CHIPS[s.status]">
        {{ stages.length > 1 ? `${s.short}: ` : '' }}{{ s.text || STATUS[s.status] || s.status }}
      </span>
      <slot name="tools" />
    </header>
    <p v-for="t in intros" :key="`i${t.id}`" class="board-section__intro">{{ t.args.intro }}</p>

    <slot />

    <div
      v-for="t in tables"
      :key="t.id"
      class="board-decision is-line"
      :data-action="t.id"
    >
      <div class="board-decision__head">
        <strong>{{ t.args?.title || `Этап «${t.stageLabel}»` }}</strong>
        <small>отмечено {{ chosen(t).length }} из {{ rowsOf(t).length }}</small>
        <button class="ai-check__link" type="button" @click="shown[t.id] = !shown[t.id]">
          {{ shown[t.id] ? 'Скрыть таблицу' : 'Показать таблицей' }}
        </button>
      </div>
      <AiCheckTable
        v-if="shown[t.id]"
        :off="board.offOf(t)"
        :columns="t.args?.columns || []"
        :rows="rowsOf(t)"
        :disabled="board.disabled.value"
        @update:off="board.setOff(t.id, $event)"
      />
    </div>
  </section>
</template>

<script setup>
/**
 * Раздел доски: заголовок со статусом этапов, пояснение ИИ, содержимое и строка ждущей таблицы — сколько
 * отмечено и таблица целиком по кнопке. Решение по таблице — кнопкой «Дальше» шага мастера (CampaignBoard).
 */
import { computed, inject, reactive } from 'vue'
import AiCheckTable from './AiCheckTable.vue'

const props = defineProps({
  name: { type: String, required: true },
  title: { type: String, required: true },
  // [{ key, short, status, text? }] — этапы раздела.
  stages: { type: Array, default: () => [] },
  // Ждущие таблицы этапов раздела (действия propose_items) с stageLabel.
  tables: { type: Array, default: () => [] },
  // Раздел — единственный на шаге мастера: заголовок уже у шага, свой не нужен.
  bare: { type: Boolean, default: false },
})
const board = inject('board')

const STATUS = {
  done: 'согласовано',
  pending: 'ждёт решения',
  todo: 'впереди',
  skipped: 'пропущено',
  rejected: 'на доработке',
  approved: 'создаётся',
  error: 'ошибка',
}
const CHIPS = { done: 'chip--ok', pending: 'chip--warning', rejected: 'chip--bad', error: 'chip--bad' }

const shown = reactive({})

const intros = computed(() => props.tables.filter((t) => t.args?.intro))

function rowsOf(table) {
  return (table.args?.rows || []).map((r) => ({ id: r.n, section: r.section, kind: r.kind, cells: r.cells || [] }))
}

function chosen(table) {
  const off = board.offOf(table)
  return rowsOf(table).map((r) => r.id).filter((n) => !off.includes(n))
}
</script>
