<template>
  <Teleport v-if="description || $slots.description" defer to="#dhead-desc">
    <slot name="description">{{ description }}</slot>
  </Teleport>
  <Teleport v-if="$slots.actions" defer to="#dhead-acts">
    <slot name="actions" />
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, watchEffect } from 'vue'
import { clearPageTitle, setPageTitle } from '../../composables/usePageTitle'

/**
 * Шапка страницы. Заголовок h1 — один, его пишет оболочка (AppShell) по пункту меню; страница добавляет к нему
 * строку описания и свои действия справа — период, «Обновить», главную кнопку. Вторых крупных заголовков
 * внутри страницы не делаем.
 */
const props = defineProps({
  description: { type: String, default: '' },
  // Свой заголовок вместо названия пункта меню — только там, где он важнее (приветствие на главной).
  title: { type: String, default: '' },
})

const owner = Symbol('page-title')
watchEffect(() => setPageTitle(owner, props.title))
onBeforeUnmount(() => clearPageTitle(owner))
</script>
