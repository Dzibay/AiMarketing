<template>
  <section class="board-section" :data-section="name" :class="{ 'is-waiting': tables.length }">
    <header class="board-section__head">
      <h4>{{ title }}</h4>
      <span v-for="s in stages" :key="s.key" class="chip" :class="CHIPS[s.status]">
        {{ stages.length > 1 ? `${s.short}: ` : '' }}{{ s.text || STATUS[s.status] || s.status }}
      </span>
      <slot name="tools" />
    </header>
    <p v-for="t in intros" :key="`i${t.id}`" class="board-section__intro">{{ t.args.intro }}</p>

    <slot />

    <div
      v-for="t in tables"
      :key="t.id"
      class="board-decision"
      :class="{ 'is-table': shown[t.id] }"
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
      <textarea
        v-model="comments[t.id]"
        class="ai-input ai-input--area"
        rows="1"
        maxlength="2000"
        :disabled="board.disabled.value"
        placeholder="Что поправить или добавить — необязательно"
        aria-label="Комментарий к этапу"
      />
      <div class="board-decision__buttons">
        <button
          class="btn btn--accent"
          type="button"
          :disabled="board.disabled.value || !chosen(t).length"
          @click="decide(t, true)"
        >
          Согласовать выбранные ({{ chosen(t).length }})
        </button>
        <button class="btn btn--ghost" type="button" :disabled="board.disabled.value" @click="decide(t, false)">
          Отклонить этап
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
/**
 * Раздел доски: заголовок со статусом этапов, пояснение ИИ, содержимое и решение по ждущей таблице —
 * отмеченное галочками на доске, комментарий, «Согласовать» и «Отклонить». Таблица целиком — по кнопке.
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

const comments = reactive({})
const shown = reactive({})

const intros = computed(() => props.tables.filter((t) => t.args?.intro))

function rowsOf(table) {
  return (table.args?.rows || []).map((r) => ({ id: r.n, section: r.section, kind: r.kind, cells: r.cells || [] }))
}

function chosen(table) {
  const off = board.offOf(table)
  return rowsOf(table).map((r) => r.id).filter((n) => !off.includes(n))
}

function decide(table, approve) {
  board.decide(table.id, { approve, rows: approve ? chosen(table) : [], comment: (comments[table.id] || '').trim() })
}
</script>
