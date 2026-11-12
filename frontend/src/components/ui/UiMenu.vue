<template>
  <span class="ui-menu">
    <button
      ref="trigger"
      class="btn ui-menu__btn"
      :class="{
        'btn--sm': size === 'sm',
        'btn--ghost': !primary,
        'btn--accent': primary,
        'ui-menu__btn--text': $slots.trigger,
        'is-open': open,
      }"
      type="button"
      :aria-label="label"
      :title="label"
      aria-haspopup="menu"
      :aria-expanded="open"
      :disabled="disabled"
      @click="toggleMenu"
      @keydown.down.prevent="openAndFocus(0)"
      @keydown.up.prevent="openAndFocus(-1)"
    >
      <slot name="trigger"><Ellipsis aria-hidden="true" /></slot>
    </button>
    <Teleport to="body">
      <div
        v-if="open"
        ref="panel"
        class="ui-pop ui-menu__list"
        role="menu"
        :aria-label="label"
        :style="style"
        @keydown="onKey"
        @click="onClick"
      >
        <slot :close="close" />
      </div>
    </Teleport>
  </span>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { Ellipsis } from '@lucide/vue'
import { usePopover } from '../../composables/usePopover'

/**
 * Меню вторичных действий «⋯». Пункты — UiMenuItem в слоте; опасные ставятся последними и сами открывают
 * подтверждение. Клавиатура: ↓/↑ открывают и ходят по пунктам, Esc закрывает и возвращает фокус на кнопку.
 */
const props = defineProps({
  label: { type: String, default: 'Ещё действия' },
  size: { type: String, default: 'md' },
  align: { type: String, default: 'end' },
  disabled: { type: Boolean, default: false },
  // Главная кнопка страницы с текстом в слоте trigger («Новая кампания ▾»), а не «⋯».
  primary: { type: Boolean, default: false },
})

const trigger = ref(null)
const panel = ref(null)
const { open, style, show, close } = usePopover(trigger, panel, { align: props.align })

function items() {
  return [...(panel.value?.querySelectorAll('[role="menuitem"]:not(:disabled)') || [])]
}

async function openAndFocus(index) {
  await show()
  await nextTick()
  const list = items()
  list.at(index)?.focus()
}

function toggleMenu() {
  if (open.value) close()
  else openAndFocus(0)
}

function onKey(event) {
  const list = items()
  const at = list.indexOf(document.activeElement)
  if (event.key === 'Escape') {
    event.preventDefault()
    // Не даём Esc закрыть заодно и окно, внутри которого открыто меню.
    event.stopPropagation()
    close({ focus: true })
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    list[(at + 1) % list.length]?.focus()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    list.at(at <= 0 ? -1 : at - 1)?.focus()
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    list.at(event.key === 'Home' ? 0 : -1)?.focus()
  } else if (event.key === 'Tab') {
    close()
  }
}

// Выбранный пункт закрывает меню (его собственный обработчик уже отработал).
function onClick(event) {
  const item = event.target.closest('[role="menuitem"]')
  if (item && !item.disabled) close()
}

defineExpose({ close })
</script>
