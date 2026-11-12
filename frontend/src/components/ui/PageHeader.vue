<template>
  <Teleport v-if="description || $slots.description" defer to="#dhead-desc">
    <slot name="description">{{ description }}</slot>
  </Teleport>
  <Teleport v-if="$slots.actions" defer to="#dhead-acts">
    <slot name="actions" />
  </Teleport>
</template>

<script setup>
/**
 * Шапка страницы. Заголовок h1 — один, его пишет оболочка (AppShell) по пункту меню; страница добавляет к нему
 * строку описания и свои действия справа — период, «Обновить», главную кнопку. Вторых крупных заголовков
 * внутри страницы не делаем.
 */
defineProps({ description: { type: String, default: '' } })
</script>
