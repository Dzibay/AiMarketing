<template>
  <span class="ui-select ui-date" :class="{ 'is-open': open, 'is-block': block }">
    <button
      :id="id || undefined"
      ref="trigger"
      class="ui-select__btn"
      type="button"
      data-field-control
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-label="ariaLabel ? `${ariaLabel}: ${text}` : undefined"
      :disabled="disabled"
      @click="toggle"
      @keydown.down.prevent="openPanel"
    >
      <CalendarDays class="ui-date__icon" aria-hidden="true" />
      <span class="ui-select__value" :class="{ 'is-placeholder': !modelValue }">{{ text }}</span>
    </button>

    <Teleport to="body">
      <div v-if="open && sheet" class="ui-scrim" aria-hidden="true"></div>
      <div
        v-if="open"
        ref="panel"
        class="ui-pop ui-date__pop"
        :class="{ 'is-sheet': sheet }"
        role="dialog"
        :aria-label="ariaLabel || 'Выбор даты'"
        :style="sheet ? null : style"
        @keydown.esc.stop.prevent="close({ focus: true })"
        @focusout="onFocusOut"
      >
        <UiCalendar ref="cal" :start="modelValue || ''" :end="modelValue || ''" :min="min" :max="max" @pick="pick" />
        <div v-if="clearable" class="ui-date__foot">
          <button
            class="btn btn--ghost btn--sm ui-date__clear"
            :class="{ 'is-current': !modelValue }"
            type="button"
            :aria-pressed="!modelValue"
            @click="pick('')"
          >
            {{ clearLabel }}
          </button>
        </div>
      </div>
    </Teleport>
  </span>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { CalendarDays } from '@lucide/vue'
import { usePopover } from '../../composables/usePopover'
import { formatDay } from '../../utils/format'
import UiCalendar from './UiCalendar.vue'

/**
 * Одна дата вместо input type=date: кнопка «13 ноября 2026», по нажатию — календарь на месяц
 * (на телефоне — панель снизу). v-model — «ГГГГ-ММ-ДД» или '' (дата не задана).
 * clearable — пункт clearLabel внизу («Без даты окончания», «Бессрочно»), он сбрасывает дату.
 * Внутри .field подпись связывается с кнопкой через v-field, как у UiSelect.
 */
const props = defineProps({
  modelValue: { type: String, default: '' },
  min: { type: String, default: '' },
  max: { type: String, default: '' },
  placeholder: { type: String, default: 'Выберите дату' },
  clearable: { type: Boolean, default: false },
  clearLabel: { type: String, default: 'Без даты' },
  ariaLabel: { type: String, default: '' },
  id: { type: String, default: '' },
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])

const trigger = ref(null)
const panel = ref(null)
const cal = ref(null)
const sheet = ref(false)
const { open, style, show, close } = usePopover(trigger, panel, { align: 'start' })

const text = computed(() => {
  if (props.modelValue) return formatDay(props.modelValue)
  return props.clearable ? props.clearLabel : props.placeholder
})

async function openPanel() {
  sheet.value = window.matchMedia('(max-width: 640px)').matches
  await show()
  await nextTick()
  cal.value?.focus()
}

function toggle() {
  if (open.value) close()
  else openPanel()
}

function pick(iso) {
  close({ focus: true })
  if (iso === (props.modelValue || '')) return
  emit('update:modelValue', iso)
  emit('change', iso)
}

function onFocusOut(event) {
  const to = event.relatedTarget
  if (to && !panel.value?.contains(to) && to !== trigger.value) close()
}
</script>
