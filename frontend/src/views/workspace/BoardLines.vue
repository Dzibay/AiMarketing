<template>
  <div class="board-lines">
    <div class="board-lines__head">
      <span>{{ label }}</span>
      <small :class="{ 'tone-bad': onCount > max }">{{ onCount }} из {{ max }}</small>
    </div>
    <ul class="board-lines__list">
      <li
        v-for="item in items"
        :key="item.text"
        class="board-lines__row"
        :class="{ 'is-pending': item.pending, 'is-off': !board.isOn(item), 'is-own': item.own }"
      >
        <input
          v-if="item.pending"
          type="checkbox"
          :checked="board.isOn(item)"
          :disabled="board.disabled.value"
          :aria-label="item.text"
          @change="board.toggle(item)"
        />
        <span v-else class="board-lines__ok" title="Согласовано" aria-hidden="true">✓</span>
        <form v-if="editing === item.text" class="board-lines__form" @submit.prevent="save(item)">
          <input
            ref="field"
            v-model="value"
            class="ai-input"
            :maxlength="limit + 20"
            :aria-label="`${label}: новый текст`"
            @keydown.esc.prevent="editing = null"
          />
          <small class="board-lines__len" :class="{ 'tone-bad': value.trim().length > limit }">
            {{ value.trim().length }}/{{ limit }}
          </small>
          <button class="btn btn--accent btn--xs" type="submit" :disabled="!canSave(item)">Сохранить</button>
          <button class="btn btn--ghost btn--xs" type="button" @click="editing = null">Отмена</button>
        </form>
        <template v-else>
          <button
            class="board-lines__text"
            type="button"
            title="Изменить"
            :disabled="board.disabled.value"
            @click="start(item)"
          >
            {{ item.text }}
          </button>
          <small class="board-lines__len" :class="{ 'tone-bad': item.text.length > limit }">
            {{ item.text.length }}/{{ limit }}
          </small>
          <button
            class="board-x"
            type="button"
            :title="item.pending && !item.added ? 'Снять галочку' : 'Убрать'"
            :aria-label="`Убрать: ${item.text}`"
            :disabled="board.disabled.value"
            @click="board.remove(kind, group, [item])"
          >
            ×
          </button>
        </template>
      </li>
    </ul>
    <form v-if="onCount < max" class="board-lines__add" @submit.prevent="add">
      <input
        v-model="fresh"
        class="ai-input"
        :maxlength="limit + 20"
        :placeholder="kind === 'title' ? '+ заголовок' : '+ текст'"
        :aria-label="kind === 'title' ? 'Новый заголовок' : 'Новый текст'"
        :disabled="board.disabled.value"
      />
      <small v-if="fresh.trim()" class="board-lines__len" :class="{ 'tone-bad': fresh.trim().length > limit }">
        {{ fresh.trim().length }}/{{ limit }}
      </small>
      <button v-if="fresh.trim()" class="btn btn--xs" type="submit" :disabled="!canAdd">Добавить</button>
    </form>
  </div>
</template>

<script setup>
/** Заголовки или тексты группы на доске: правка по щелчку со счётчиком знаков, «×» и новая строка. */
import { computed, inject, nextTick, ref } from 'vue'

const props = defineProps({
  items: { type: Array, required: true },
  kind: { type: String, required: true },
  group: { type: String, required: true },
})
const board = inject('board')

const LIMITS = { title: 56, text: 81 }
const MAX = { title: 7, text: 3 }

const limit = computed(() => LIMITS[props.kind])
const max = computed(() => MAX[props.kind])
const label = computed(() => (props.kind === 'title' ? 'Заголовки' : 'Тексты'))
const onCount = computed(() => props.items.filter(board.isOn).length)

const editing = ref(null)
const value = ref('')
const fresh = ref('')
const field = ref(null)

const canAdd = computed(() => {
  const text = fresh.value.trim()
  return !!text && text.length <= limit.value && !board.disabled.value
})

function canSave(item) {
  const text = value.value.trim()
  return !!text && text !== item.text && text.length <= limit.value && !board.disabled.value
}

async function start(item) {
  editing.value = item.text
  value.value = item.text
  await nextTick()
  field.value?.[0]?.focus()
}

async function save(item) {
  if (!canSave(item)) return
  const ok = await board.edit([{ op: 'edit', kind: props.kind, group: props.group, text: item.text, to: value.value.trim() }])
  if (ok) editing.value = null
}

async function add() {
  if (!canAdd.value) return
  const ok = await board.edit([{ op: 'add', kind: props.kind, group: props.group, text: fresh.value.trim() }])
  if (ok) fresh.value = ''
}
</script>
