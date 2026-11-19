<template>
  <div class="rpick">
    <ul v-if="modelValue.length" class="rpick__chips" aria-label="Выбранные регионы">
      <li v-for="id in modelValue" :key="id" class="rpick__chip" :class="{ 'is-minus': id < 0 }">
        <span class="rpick__name" :title="pathOf(id)">{{ nameOf(id) }}</span>
        <button
          type="button"
          class="rpick__mode"
          :aria-pressed="id < 0"
          :title="id < 0 ? 'Вернуть показы в этом регионе' : 'Не показывать рекламу в этом регионе'"
          @click="toggleMinus(id)"
        >
          кроме
        </button>
        <button type="button" class="rpick__x" :aria-label="`Убрать «${nameOf(id)}»`" @click="remove(id)">×</button>
      </li>
    </ul>
    <p v-else class="rpick__empty">Регионы не выбраны — найдите город или область по названию.</p>

    <div class="rpick__search">
      <input
        ref="inputEl"
        v-model="query"
        type="search"
        role="combobox"
        :aria-expanded="listOpen"
        aria-autocomplete="list"
        :aria-controls="listId"
        :aria-activedescendant="listOpen && results[active] ? `${listId}-${active}` : undefined"
        :placeholder="placeholder"
        aria-label="Найти регион по названию"
        autocomplete="off"
        maxlength="100"
        @input="onInput"
        @keydown="onKey"
        @focus="focused = true"
        @blur="focused = false"
      />
      <ul v-if="listOpen" :id="listId" class="rpick__list" role="listbox" aria-label="Найденные регионы">
        <li v-if="loading && !results.length" class="rpick__info">Ищем…</li>
        <li
          v-for="(r, i) in results"
          :id="`${listId}-${i}`"
          :key="r.id"
          role="option"
          class="rpick__option"
          :class="{ 'is-active': i === active }"
          :aria-selected="i === active"
          @mousedown.prevent="pick(r)"
          @mousemove="active = i"
        >
          <span class="rpick__opt-name">{{ r.name }}</span>
          <small>
            {{ [r.type, r.path].filter(Boolean).join(' · ') }}
            <template v-if="chosen(r.id)"> · уже выбран</template>
          </small>
        </li>
        <li v-if="!loading && !results.length && !error" class="rpick__info">
          Ничего не нашлось — попробуйте другое название
        </li>
        <li v-if="error" class="rpick__info is-bad">{{ error }}</li>
      </ul>
    </div>
    <p v-if="note" class="field-hint">{{ note }}</p>
    <p v-if="onlyMinus" class="field-hint tone-warn">
      Добавьте хотя бы один регион показа — «кроме» работает только вместе с ним, например «Россия, кроме Москвы».
    </p>
    <p v-else class="field-hint">«Кроме» — не показывать рекламу в этом регионе, например «Россия, кроме Москвы».</p>
  </div>
</template>

<script setup>
// Поле «Регионы показа»: поиск по названию города или области, «кроме» — у каждого выбранного региона.
// v-model — id регионов Директа; минус — регион исключён.
import { computed, reactive, ref, watch } from 'vue'
import { errorText, webApi } from '../api/web'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  // Названия уже выбранных регионов: [{ id, name, path }] — из ответа сервера.
  selected: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Город, область или страна' },
})
const emit = defineEmits(['update:modelValue'])

// Основные регионы — чтобы готовые варианты мастера показывались названиями без запроса.
const KNOWN = {
  225: 'Россия',
  1: 'Москва и Московская область',
  213: 'Москва',
  10174: 'Санкт-Петербург и Ленинградская область',
  2: 'Санкт-Петербург',
}
const names = reactive({})
const query = ref('')
const results = ref([])
const loading = ref(false)
const error = ref('')
const note = ref('')
const active = ref(0)
const focused = ref(false)
const inputEl = ref(null)
const listId = `rpick-${Math.random().toString(36).slice(2, 8)}`
let timer = null
let seq = 0

watch(
  () => props.selected,
  (list) => {
    for (const r of list || []) names[Math.abs(Number(r.id))] = { name: r.name, path: r.path || '' }
  },
  { immediate: true },
)

const listOpen = computed(() => focused.value && query.value.trim().length > 0)
const onlyMinus = computed(() => props.modelValue.length > 0 && !props.modelValue.some((id) => id > 0))

function nameOf(id) {
  const key = Math.abs(id)
  return names[key]?.name || KNOWN[key] || `Регион ${key}`
}

function pathOf(id) {
  return names[Math.abs(id)]?.path || ''
}

function chosen(id) {
  return props.modelValue.some((v) => Math.abs(v) === id)
}

function onInput() {
  clearTimeout(timer)
  const q = query.value.trim()
  error.value = ''
  if (!q) {
    results.value = []
    loading.value = false
    return
  }
  loading.value = true
  timer = setTimeout(() => search(q), 250)
}

async function search(q) {
  const mine = ++seq
  try {
    const data = await webApi.searchRegions(q)
    if (mine !== seq) return
    results.value = data.regions || []
    note.value = data.note || ''
    active.value = 0
  } catch (err) {
    if (mine !== seq) return
    results.value = []
    error.value = errorText(err)
  } finally {
    if (mine === seq) loading.value = false
  }
}

function pick(region) {
  names[region.id] = { name: region.name, path: region.path || '' }
  if (!chosen(region.id)) emit('update:modelValue', [...props.modelValue, region.id])
  query.value = ''
  results.value = []
  inputEl.value?.focus()
}

function toggleMinus(id) {
  emit(
    'update:modelValue',
    props.modelValue.map((v) => (v === id ? -v : v)),
  )
}

function remove(id) {
  emit(
    'update:modelValue',
    props.modelValue.filter((v) => v !== id),
  )
}

function onKey(event) {
  if (!listOpen.value) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!results.value.length) return
    const step = event.key === 'ArrowDown' ? 1 : -1
    active.value = (active.value + step + results.value.length) % results.value.length
  } else if (event.key === 'Enter') {
    // Enter выбирает подсвеченный регион и не отправляет форму вокруг поля.
    event.preventDefault()
    const region = results.value[active.value]
    if (region) pick(region)
  } else if (event.key === 'Escape' && query.value) {
    event.stopPropagation()
    query.value = ''
    results.value = []
  }
}
</script>

<style scoped>
.rpick {
  display: grid;
  gap: 0.55rem;
}

.rpick__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.rpick__chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.22rem 0.3rem 0.22rem 0.7rem;
  border: 1px solid var(--line-2);
  border-radius: var(--pill);
  background: var(--surface);
  font-size: 0.86rem;
  max-width: 100%;
}

.rpick__chip.is-minus {
  background: var(--surface-2);
  border-style: dashed;
}

.rpick__chip.is-minus .rpick__name {
  color: var(--muted);
  text-decoration: line-through;
}

.rpick__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rpick__mode,
.rpick__x {
  border: 0;
  border-radius: var(--pill);
  background: transparent;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}

.rpick__mode {
  padding: 0.1rem 0.5rem;
  font-size: 0.76rem;
  border: 1px solid var(--line);
}

.rpick__mode[aria-pressed='true'] {
  background: var(--lime);
  border-color: var(--lime);
  color: var(--on-lime);
  font-weight: 600;
}

.rpick__x {
  width: 1.5rem;
  height: 1.5rem;
  font-size: 1.05rem;
  line-height: 1;
}

.rpick__mode:hover,
.rpick__x:hover {
  color: var(--ink);
  background: var(--accent-soft);
}

.rpick__mode[aria-pressed='true']:hover {
  background: var(--lime);
}

.rpick__mode:focus-visible,
.rpick__x:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.rpick__empty {
  margin: 0;
  color: var(--muted);
  font-size: 0.86rem;
}

.rpick__search {
  position: relative;
}

.rpick__search input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line-2);
  border-radius: var(--radius);
  padding: 0.7rem 0.9rem;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
}

.rpick__search input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-soft);
}

.rpick__list {
  position: absolute;
  z-index: 30;
  top: calc(100% + 0.3rem);
  left: 0;
  right: 0;
  max-height: 16rem;
  overflow-y: auto;
  margin: 0;
  padding: 0.3rem;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}

.rpick__option {
  display: grid;
  gap: 0.1rem;
  padding: 0.5rem 0.65rem;
  border-radius: var(--r-sm);
  cursor: pointer;
}

.rpick__option.is-active {
  background: var(--accent-soft);
}

.rpick__opt-name {
  font-weight: 560;
}

.rpick__option small {
  color: var(--muted);
  font-size: 0.78rem;
}

.rpick__info {
  padding: 0.5rem 0.65rem;
  color: var(--muted);
  font-size: 0.86rem;
}

.rpick__info.is-bad {
  color: var(--danger);
}

.rpick .field-hint {
  margin: 0;
}
</style>
