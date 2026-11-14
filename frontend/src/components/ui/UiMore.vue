<template>
  <div class="ui-more" :class="{ 'is-open': shown }">
    <p class="ui-more__lead"><slot name="lead">{{ lead }}</slot></p>
    <div v-if="!phone" v-show="open" :id="bodyId" class="ui-more__body"><slot /></div>
    <button
      class="ui-more__toggle"
      type="button"
      :aria-expanded="phone ? undefined : open ? 'true' : 'false'"
      :aria-controls="phone ? undefined : bodyId"
      :aria-haspopup="phone ? 'dialog' : undefined"
      @click="toggle"
    >
      {{ shown ? 'Свернуть' : 'Подробнее' }}<ChevronDown aria-hidden="true" />
    </button>
    <UiSheet v-if="phone" :open="sheetOpen" title="Подробнее" @close="sheetOpen = false">
      <div class="ui-more__sheet">
        <p><slot name="lead">{{ lead }}</slot></p>
        <slot />
      </div>
    </UiSheet>
  </div>
</template>

<script setup>
import { computed, ref, useId } from 'vue'
import { ChevronDown } from '@lucide/vue'
import UiSheet from './UiSheet.vue'
import { useViewport } from '../../composables/useViewport'

/**
 * Длинное пояснение страницы: одна строка сути, под ней «Подробнее» раскрывает остальное (абзацы — слотом).
 * Ставится в описание шапки: <PageHeader><template #description><UiMore …></template></PageHeader>.
 * Раскрытие запоминается для страницы в браузере — по ключу page.
 * На телефоне «Подробнее» открывает шторку снизу: абзацы не сдвигают страницу, и запоминать тут нечего.
 */
const props = defineProps({
  /** Ключ страницы, под которым запоминается раскрытие. */
  page: { type: String, required: true },
  lead: { type: String, default: '' },
})

const KEY = 'aimarketing-more'
const bodyId = useId()
const { phone } = useViewport()

function openPages() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || '[]')
    return new Set(Array.isArray(list) ? list : [])
  } catch {
    return new Set()
  }
}

const open = ref(openPages().has(props.page))
const sheetOpen = ref(false)
// Раскрыто прямо на странице — только на широком экране.
const shown = computed(() => open.value && !phone.value)

function toggle() {
  if (phone.value) {
    sheetOpen.value = true
    return
  }
  open.value = !open.value
  const pages = openPages()
  if (open.value) pages.add(props.page)
  else pages.delete(props.page)
  try {
    localStorage.setItem(KEY, JSON.stringify([...pages]))
  } catch {
    // Без localStorage раскрытие живёт до ухода со страницы.
  }
}
</script>
