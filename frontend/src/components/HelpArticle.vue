<template>
  <div class="help-art" @click="onClick">
    <template v-if="article.slug === 'changelog'">
      <article v-for="e in CHANGELOG" :id="`n-${e.id}`" :key="e.id" class="help-news">
        <small>{{ formatDay(e.date) }}</small>
        <h3>{{ e.title }}</h3>
        <div class="md" v-html="renderMarkdown(e.body)" />
      </article>
    </template>
    <template v-else>
      <div class="md" v-html="renderMarkdown(article.body)" />
      <dl v-if="article.slug === 'glossary'" class="help-terms">
        <template v-for="t in GLOSSARY" :key="t.key">
          <dt>{{ t.title }}</dt>
          <dd>{{ t.text }}</dd>
        </template>
      </dl>
    </template>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { CHANGELOG, GLOSSARY } from '../utils/help'
import { formatDay } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'

/**
 * Текст статьи справки. Ссылки на экраны кабинета (data-route) открываются переходом роутера, без перезагрузки;
 * событие navigate — чтобы боковая панель закрылась. «Словарь» дописывает термины, «Что нового» — записи с датами.
 */
defineProps({ article: { type: Object, required: true } })
const emit = defineEmits(['navigate'])
const router = useRouter()

function onClick(event) {
  const link = event.target instanceof Element ? event.target.closest('a[data-route]') : null
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button) return
  event.preventDefault()
  emit('navigate')
  router.push(link.getAttribute('href'))
}
</script>
