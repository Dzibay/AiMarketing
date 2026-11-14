<template>
  <div class="ltools" :class="{ 'ltools--phone': phone }" role="search" :aria-label="label || undefined">
    <slot name="search" />
    <template v-if="phone">
      <button class="btn btn--ghost ltools__open" type="button" aria-haspopup="dialog" @click="open = true">
        <SlidersHorizontal aria-hidden="true" />
        Фильтры
        <span v-if="active" class="ltools__n" :aria-label="`включено: ${active}`">{{ active }}</span>
      </button>
      <div
        v-if="$slots.tabs"
        ref="tabs"
        class="ltools__tabs"
        :class="{ 'has-before': strip.before, 'has-after': strip.after }"
      >
        <slot name="tabs" />
      </div>
      <UiSheet :open="open" :title="title" @close="open = false">
        <div class="ltools__sheet">
          <slot name="phone" />
          <slot />
        </div>
        <template #footer>
          <button v-if="active" class="btn btn--ghost" type="button" @click="$emit('reset')">Сбросить</button>
          <button class="btn btn--accent" type="button" @click="open = false">Готово</button>
        </template>
      </UiSheet>
    </template>
    <template v-else>
      <slot name="tabs" />
      <slot />
    </template>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { SlidersHorizontal } from '@lucide/vue'
import { revealInStrip, useScrollStrip } from '../../composables/useScrollStrip'
import { useViewport } from '../../composables/useViewport'
import UiSheet from './UiSheet.vue'

/**
 * Строка инструментов над списком или таблицей (.ltools): поиск, сегменты, фильтры.
 * На широком экране всё стоит в ряд, как и раньше. На телефоне в ряд — только поиск (слот search) и кнопка
 * «Фильтры»; сегменты-вкладки с цифрами (слот tabs) идут лентой под ними, остальное (слот по умолчанию)
 * открывается шторкой. Слот phone — то, чему на телефоне больше негде стоять: сортировка вместо заголовков
 * таблицы, период страницы.
 * active — сколько фильтров из шторки включено: число на кнопке и повод показать «Сбросить» (событие reset).
 */
defineProps({
  active: { type: Number, default: 0 },
  title: { type: String, default: 'Фильтры' },
  label: { type: String, default: '' },
})
defineEmits(['reset'])

const { phone } = useViewport()
const open = ref(false)
const tabs = ref(null)
// Лента сегментов: край, за которым есть ещё, затеняется.
const { strip } = useScrollStrip(tabs)

// Выбранный сегмент мог оказаться за краем ленты (страницу открыли сразу на «Архиве») — показываем его.
watch(tabs, (el) => revealInStrip(el, el?.querySelector('.active')), { flush: 'post' })

// Экран повернули или расширили — шторки больше нет, фильтры снова в ряд.
watch(phone, (now) => {
  if (!now) open.value = false
})
</script>
