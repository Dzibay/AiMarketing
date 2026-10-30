<template>
  <div class="ai-check" :class="{ 'ai-check--readonly': readonly }">
    <div v-if="!readonly" class="ai-check__bar">
      <span>Отмечено {{ onCount }} из {{ rows.length }}</span>
      <button class="ai-check__link" type="button" :disabled="disabled || !off.length" @click="setAll(true)">
        Отметить все
      </button>
      <button class="ai-check__link" type="button" :disabled="disabled || onCount === 0" @click="setAll(false)">
        Снять все
      </button>
      <span v-if="rows.length > 3" class="ai-check__tip">Shift+клик — несколько строк подряд</span>
    </div>

    <div class="ai-check__scroll">
      <table>
        <thead>
          <tr>
            <th v-if="!readonly" class="ai-check__box" scope="col">
              <input
                type="checkbox"
                aria-label="Все строки"
                :checked="onCount === rows.length"
                :indeterminate.prop="onCount > 0 && onCount < rows.length"
                :disabled="disabled"
                @change="setAll($event.target.checked)"
              />
            </th>
            <th v-if="showKind" scope="col">Что</th>
            <th v-for="(c, i) in columns" :key="i" scope="col">{{ c }}</th>
          </tr>
        </thead>
        <tbody v-for="sec in sections" :key="sec.name">
          <tr v-if="sec.name" class="ai-check__section" :class="{ 'is-off': sec.group && isOff(sec.group) }">
            <th :colspan="span" scope="rowgroup">
              <label v-if="!readonly" class="ai-check__section-label">
                <input
                  type="checkbox"
                  :checked="sectionOn(sec)"
                  :indeterminate.prop="sectionSome(sec)"
                  :disabled="disabled"
                  @change="setSection(sec, $event.target.checked)"
                />
                <span>{{ sec.name }}</span>
              </label>
              <span v-else>{{ sec.name }}</span>
              <small>{{ sectionText(sec) }}</small>
            </th>
          </tr>
          <tr
            v-for="row in sec.rows"
            :key="row.id"
            class="ai-check__row"
            :class="{ 'is-off': !isOn(row), 'is-locked': isLocked(row) }"
            @mousedown="$event.shiftKey && $event.preventDefault()"
            @click="onRow(row, $event)"
          >
            <td v-if="!readonly" class="ai-check__box">
              <input
                type="checkbox"
                :checked="isOn(row)"
                :disabled="disabled || isLocked(row)"
                :aria-label="row.cells[0]"
                @click.stop="onBox(row, $event)"
              />
            </td>
            <td v-if="showKind" class="ai-check__kind">{{ KINDS[row.kind] || '' }}</td>
            <td v-for="(c, i) in row.cells" :key="i">
              {{ c }}
              <small v-if="i === 0 && LIMITS[row.kind]" class="ai-check__len" :class="{ 'tone-bad': c.length > LIMITS[row.kind] }">
                {{ c.length }}/{{ LIMITS[row.kind] }}
              </small>
              <span v-if="readonly && i === row.cells.length - 1 && !isOn(row)" class="chip">снято</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
/**
 * Строки с галочками для согласования с ИИ: разделы (обычно группы), «отметить всё», Shift+клик — диапазон.
 * v-model:off — id снятых строк. groups — {раздел: id строки «вся группа»}: снятая группа снимает и свои строки.
 */
import { computed, ref } from 'vue'

const props = defineProps({
  columns: { type: Array, default: () => [] },
  // [{ id, section, kind, cells: [] }]
  rows: { type: Array, required: true },
  off: { type: Array, default: () => [] },
  groups: { type: Object, default: () => ({}) },
  readonly: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:off'])

const KINDS = {
  goal: 'цель',
  direction: 'направление',
  keyword: 'фраза',
  negative: 'минус-фраза',
  title: 'заголовок',
  text: 'текст',
  extra: 'расширение',
  setting: 'настройка',
}
const LIMITS = { title: 56, text: 81 }

const offSet = computed(() => new Set(props.off || []))
const groupIds = computed(() => Object.values(props.groups))
const sections = computed(() => {
  const out = []
  const byName = new Map()
  for (const row of props.rows) {
    const name = row.section || ''
    if (!byName.has(name)) {
      const sec = { name, rows: [], group: props.groups[name] ?? null }
      byName.set(name, sec)
      out.push(sec)
    }
    byName.get(name).rows.push(row)
  }
  return out
})
const flat = computed(() => sections.value.flatMap((s) => s.rows))
// Вид строки нужен, когда в одном разделе смешаны фразы, заголовки и тексты.
const showKind = computed(() =>
  sections.value.some((s) => new Set(s.rows.map((r) => r.kind).filter((k) => KINDS[k])).size > 1),
)
const span = computed(() => props.columns.length + (props.readonly ? 0 : 1) + (showKind.value ? 1 : 0))
const onCount = computed(() => props.rows.filter(isOn).length)
const last = ref(null)

function isOff(id) {
  return offSet.value.has(id)
}

function isLocked(row) {
  const group = props.groups[row.section || '']
  return group != null && isOff(group)
}

function isOn(row) {
  return !isOff(row.id) && !isLocked(row)
}

function sectionOn(sec) {
  return sec.group != null ? !isOff(sec.group) : sec.rows.every(isOn)
}

function sectionSome(sec) {
  if (sec.group != null && isOff(sec.group)) return false
  const on = sec.rows.filter(isOn).length
  return on > 0 && on < sec.rows.length
}

function sectionText(sec) {
  const on = sec.rows.filter(isOn).length
  return on === sec.rows.length ? `${on}` : `${on} из ${sec.rows.length}`
}

function update(ids, on) {
  const next = new Set(offSet.value)
  for (const id of ids) {
    if (on) next.delete(id)
    else next.add(id)
  }
  emit('update:off', [...next])
}

function setAll(on) {
  update([...props.rows.map((r) => r.id), ...groupIds.value], on)
}

function setSection(sec, on) {
  if (sec.group == null) {
    update(sec.rows.map((r) => r.id), on)
    return
  }
  // Возвращая группу, возвращаем и её строки: иначе она вернётся пустой.
  update(on && sec.rows.every((r) => isOff(r.id)) ? [sec.group, ...sec.rows.map((r) => r.id)] : [sec.group], on)
}

/** Shift — все строки между прошлым щелчком и этим получают то же состояние. */
function setRow(row, on, shift) {
  let ids = [row.id]
  const rows = flat.value
  const from = rows.findIndex((r) => r.id === last.value)
  const to = rows.findIndex((r) => r.id === row.id)
  if (shift && from >= 0 && to >= 0) {
    const [a, b] = from < to ? [from, to] : [to, from]
    ids = rows.slice(a, b + 1).filter((r) => !isLocked(r)).map((r) => r.id)
  }
  last.value = row.id
  update(ids, on)
}

function onBox(row, event) {
  setRow(row, event.target.checked, event.shiftKey)
}

function onRow(row, event) {
  if (props.readonly || props.disabled || isLocked(row)) return
  // Выделение текста мышью — не щелчок по строке (Shift не выделяет: mousedown отменён).
  if (!event.shiftKey && window.getSelection?.()?.toString()) return
  setRow(row, !isOn(row), event.shiftKey)
}
</script>
