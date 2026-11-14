<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="panel"
      class="ui-pop is-sheet ui-sheet"
      role="dialog"
      aria-modal="true"
      :aria-label="title || undefined"
      tabindex="-1"
    >
      <p v-if="title" class="ui-sheet__title">{{ title }}</p>
      <div class="ui-sheet__body">
        <slot />
      </div>
      <div v-if="$slots.footer" class="ui-sheet__foot">
        <slot name="footer" />
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { sheetLayer } from '../../composables/useSheet'

/**
 * Шторка снизу — панель телефона вместо маленького окна у края экрана: «Ещё» в нижней панели, меню профиля,
 * «Подробнее» в шапке страницы. Закрывается жестом вниз, нажатием на затемнение и Esc; пока открыта, фокус
 * остаётся внутри, а после закрытия возвращается туда, где был. Содержимое прокручивается, заголовок и строка
 * кнопок (слот footer) стоят на месте.
 *
 * Меню «⋯», списки и даты становятся шторкой сами (usePopover) — этот компонент им не нужен.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const panel = ref(null)
const layer = sheetLayer(() => emit('close'))
let opener = null

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      layer.detach()
      // Страница снова доступна — фокус на кнопку, которая открыла шторку.
      if (opener?.isConnected) opener.focus({ preventScroll: true })
      opener = null
      return
    }
    opener = document.activeElement
    await nextTick()
    if (!props.open || !panel.value) return
    layer.attach(panel.value)
    panel.value.focus({ preventScroll: true })
  },
  { immediate: true },
)

onBeforeUnmount(() => layer.detach())
</script>
