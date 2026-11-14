<template>
  <span
    class="ui-select"
    :class="[`ui-select--${size}`, { 'is-open': open, 'is-block': block, 'is-invalid': invalid }]"
  >
    <button
      :id="id || undefined"
      ref="trigger"
      class="ui-select__btn"
      type="button"
      data-field-control
      :aria-label="ariaLabel || undefined"
      :aria-labelledby="ariaLabelledby || undefined"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-required="required || undefined"
      :aria-invalid="invalid || undefined"
      :disabled="disabled"
      @click="toggleList"
      @keydown="onTriggerKey"
    >
      <span class="ui-select__value" :class="{ 'is-placeholder': !current }">{{ current?.label ?? placeholder }}</span>
      <ChevronDown class="ui-select__chev" aria-hidden="true" />
    </button>
    <!-- Для обязательного списка: невидимое поле под кнопкой не даёт отправить форму без выбора,
         браузер показывает своё сообщение у кнопки, как у остальных полей. -->
    <input
      v-if="required"
      class="ui-select__native"
      tabindex="-1"
      aria-hidden="true"
      autocomplete="off"
      required
      :value="empty ? '' : '1'"
      :disabled="disabled"
      @invalid="invalid = true"
      @keydown="onNativeKey"
    />
    <Teleport to="body">
      <div v-if="open" ref="panel" class="ui-pop ui-select__pop" :class="{ 'is-sheet': sheet }" :style="style">
        <p v-if="sheet && sheetTitle" class="ui-sheet__title" aria-hidden="true">{{ sheetTitle }}</p>
        <label v-if="withSearch" class="ui-select__search">
          <Search aria-hidden="true" />
          <input
            ref="searchEl"
            v-model="query"
            type="search"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded="true"
            :aria-controls="listId"
            :aria-activedescendant="activeId"
            :aria-label="searchLabel"
            placeholder="Найти"
            autocomplete="off"
            @keydown="onListKey"
          />
        </label>
        <ul
          :id="listId"
          ref="listEl"
          class="ui-select__list"
          role="listbox"
          :tabindex="withSearch ? undefined : -1"
          :aria-label="ariaLabel || undefined"
          :aria-labelledby="ariaLabel ? undefined : ariaLabelledby || id || undefined"
          :aria-activedescendant="withSearch ? undefined : activeId"
          @keydown="onListKey"
        >
          <li
            v-for="sec in sections"
            :key="sec.key"
            class="ui-select__sec"
            :role="sec.group ? 'group' : 'none'"
            :aria-labelledby="sec.group ? `${listId}-g${sec.key}` : undefined"
          >
            <div v-if="sec.group" :id="`${listId}-g${sec.key}`" class="ui-select__group">{{ sec.group }}</div>
            <ul class="ui-select__sub" role="none">
              <li
                v-for="{ o, i, pos } in sec.items"
                :id="`${listId}-${i}`"
                :key="i"
                class="ui-select__opt"
                :class="{
                  'is-active': pos === active,
                  'is-selected': same(o.value, modelValue),
                  'is-disabled': o.disabled,
                }"
                role="option"
                :aria-selected="same(o.value, modelValue)"
                :aria-disabled="o.disabled || undefined"
                @mousemove="o.disabled || (active = pos)"
                @click="pick(o)"
              >
                <span class="ui-select__opt-text">
                  {{ o.label }}
                  <small v-if="o.hint">{{ o.hint }}</small>
                </span>
                <Check v-if="same(o.value, modelValue)" class="ui-select__tick" aria-hidden="true" />
              </li>
            </ul>
          </li>
        </ul>
        <p v-if="!view.length" class="ui-select__empty">Ничего не нашлось</p>
      </div>
    </Teleport>
  </span>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Check, ChevronDown, Search } from '@lucide/vue'
import { usePopover } from '../../composables/usePopover'

/**
 * Выпадающий список вместо нативного select: тот же вид в любой теме и браузере, подсказка у пункта.
 * options — [{ value, label, hint?, disabled?, group? }]. Пункты с одинаковым group подряд идут под общим
 * заголовком (вместо optgroup). Внутри .field подпись связывается с кнопкой через v-field.
 * В длинном списке (больше SEARCH_FROM пунктов) сверху поле поиска по названию, подсказке и группе;
 * searchable включает или выключает его явно.
 * required — без выбора (null или '') форма не отправится, а рамка станет красной.
 * Клавиатура: ↓/↑ открывают и выбирают, Enter — выбрать, Esc — закрыть; без поиска пробел тоже выбирает,
 * а первая буква переводит к пункту, с поиском набранный текст уходит в поле.
 * На телефоне список открывается шторкой снизу, сверху — подпись поля.
 */
const SEARCH_FROM = 11

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: null },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Выберите' },
  ariaLabel: { type: String, default: '' },
  ariaLabelledby: { type: String, default: '' },
  id: { type: String, default: '' },
  size: { type: String, default: 'md' },
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  required: { type: Boolean, default: false },
  searchable: { type: Boolean, default: undefined },
})
const emit = defineEmits(['update:modelValue', 'change'])

const listId = `ui-select-${Math.random().toString(36).slice(2, 10)}`
const trigger = ref(null)
const panel = ref(null)
const listEl = ref(null)
const searchEl = ref(null)
const query = ref('')
const active = ref(-1)
const invalid = ref(false)
const sheetTitle = ref('')
const { open, style, sheet, show, close } = usePopover(trigger, panel, {
  align: 'start',
  matchWidth: true,
  onClose: () => (query.value = ''),
})

const same = (a, b) => a === b || (a == null && b == null)
const current = computed(() => props.options.find((o) => same(o.value, props.modelValue)) || null)
const empty = computed(() => props.modelValue == null || props.modelValue === '')
const withSearch = computed(() => props.searchable ?? props.options.length >= SEARCH_FROM)
const searchLabel = computed(() => (props.ariaLabel ? `Найти: ${props.ariaLabel}` : 'Найти в списке'))

watch(empty, (isEmpty) => {
  if (!isEmpty) invalid.value = false
})

const needle = computed(() => query.value.trim().toLocaleLowerCase('ru'))

// Видимые пункты: i — номер в options (для id), pos — место в видимом списке (для клавиатуры).
const view = computed(() => {
  const q = needle.value
  const all = props.options.map((o, i) => ({ o, i }))
  const list = q
    ? all.filter(({ o }) => `${o.label} ${o.hint || ''} ${o.group || ''}`.toLocaleLowerCase('ru').includes(q))
    : all
  return list.map((item, pos) => ({ ...item, pos }))
})

// Подряд идущие пункты одной группы — одна секция с заголовком.
const sections = computed(() => {
  const out = []
  for (const item of view.value) {
    const group = item.o.group || ''
    const last = out.at(-1)
    if (last && last.group === group) last.items.push(item)
    else out.push({ key: out.length, group, items: [item] })
  }
  return out
})

const activeId = computed(() => {
  const item = view.value[active.value]
  return item ? `${listId}-${item.i}` : undefined
})

watch(needle, () => {
  active.value = enabledFrom(0, 1)
  nextTick(scrollToActive)
})

function enabledFrom(start, step) {
  const items = view.value
  const n = items.length
  for (let k = 0, i = start; k < n; k += 1, i = (i + step + n) % n) {
    if (!items[i]?.o.disabled) return i
  }
  return -1
}

function scrollToActive() {
  if (!activeId.value) return
  panel.value?.querySelector(`#${activeId.value}`)?.scrollIntoView({ block: 'nearest' })
}

// Заголовок шторки — подпись поля: aria-label, иначе текст подписи, с которой кнопку связал v-field.
function fieldTitle() {
  if (props.ariaLabel) return props.ariaLabel
  const label = trigger.value?.labels?.[0] || document.getElementById(props.ariaLabelledby.split(' ')[0] || ' ')
  return (label?.textContent || '').replace(/\s+/g, ' ').trim()
}

async function openList() {
  const at = view.value.findIndex(({ o }) => same(o.value, props.modelValue))
  active.value = at >= 0 ? at : enabledFrom(0, 1)
  sheetTitle.value = fieldTitle()
  await show()
  await nextTick()
  // На телефоне поиск сразу не фокусируем: экранная клавиатура закрыла бы половину списка.
  const typing = withSearch.value && !window.matchMedia('(pointer: coarse)').matches
  ;(typing ? searchEl.value : listEl.value)?.focus()
  scrollToActive()
}

function toggleList() {
  if (open.value) close()
  else openList()
}

function pick(o) {
  if (!o || o.disabled) return
  close({ focus: true })
  if (same(o.value, props.modelValue)) return
  emit('update:modelValue', o.value)
  emit('change', o.value)
}

function move(step) {
  const n = view.value.length
  if (!n) return
  active.value = enabledFrom((active.value + step + n) % n, step)
  scrollToActive()
}

// Переход к пункту по первой букве (в списке без поиска).
function jump(letter) {
  const items = view.value
  const n = items.length
  const l = letter.toLocaleLowerCase('ru')
  for (let k = 1; k <= n; k += 1) {
    const pos = (active.value + k) % n
    const o = items[pos].o
    if (!o.disabled && String(o.label).toLocaleLowerCase('ru').startsWith(l)) {
      active.value = pos
      scrollToActive()
      return
    }
  }
}

function onTriggerKey(event) {
  if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
    event.preventDefault()
    openList()
  }
}

// Браузер ставит фокус на невидимое поле, когда форму отправили без выбора; клавиатура работает как у кнопки.
function onNativeKey(event) {
  if (event.key === 'Tab') return
  trigger.value?.focus()
  onTriggerKey(event)
}

function onListKey(event) {
  const key = event.key
  const inSearch = event.target === searchEl.value
  if (key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close({ focus: true })
  } else if (key === 'ArrowDown' || key === 'ArrowUp') {
    event.preventDefault()
    move(key === 'ArrowDown' ? 1 : -1)
  } else if (key === 'Enter') {
    event.preventDefault()
    pick(view.value[active.value]?.o)
  } else if (key === 'Tab') {
    close()
  } else if (inSearch) {
    // Остальные клавиши — набор текста в поле поиска.
  } else if (key === 'Home' || key === 'End') {
    event.preventDefault()
    active.value = key === 'Home' ? enabledFrom(0, 1) : enabledFrom(view.value.length - 1, -1)
    scrollToActive()
  } else if (key === ' ') {
    event.preventDefault()
    pick(view.value[active.value]?.o)
  } else if (key.length === 1 && /\S/.test(key)) {
    jump(key)
  }
}
</script>
