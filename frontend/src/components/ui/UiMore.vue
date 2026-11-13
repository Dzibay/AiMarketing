<template>
  <div class="ui-more" :class="{ 'is-open': open }">
    <p class="ui-more__lead"><slot name="lead">{{ lead }}</slot></p>
    <div v-show="open" :id="bodyId" class="ui-more__body"><slot /></div>
    <button
      class="ui-more__toggle"
      type="button"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="bodyId"
      @click="toggle"
    >
      {{ open ? 'Свернуть' : 'Подробнее' }}<ChevronDown aria-hidden="true" />
    </button>
  </div>
</template>

<script setup>
import { ref, useId } from 'vue'
import { ChevronDown } from '@lucide/vue'

/**
 * Длинное пояснение страницы: одна строка сути, под ней «Подробнее» раскрывает остальное (абзацы — слотом).
 * Ставится в описание шапки: <PageHeader><template #description><UiMore …></template></PageHeader>.
 * Раскрытие запоминается для страницы в браузере — по ключу page.
 */
const props = defineProps({
  /** Ключ страницы, под которым запоминается раскрытие. */
  page: { type: String, required: true },
  lead: { type: String, default: '' },
})

const KEY = 'aimarketing-more'
const bodyId = useId()

function openPages() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || '[]')
    return new Set(Array.isArray(list) ? list : [])
  } catch {
    return new Set()
  }
}

const open = ref(openPages().has(props.page))

function toggle() {
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
