<template>
  <div class="ad-preview" :class="{ 'is-pending': pending }">
    <div class="ad-preview__top">
      <span class="ad-preview__favicon" aria-hidden="true">{{ domain.charAt(0).toUpperCase() }}</span>
      <span class="ad-preview__domain">{{ domain }}</span>
      <span class="ad-preview__label">Реклама</span>
    </div>
    <p class="ad-preview__title">{{ title || 'Заголовок появится на этапе объявлений' }}</p>
    <p class="ad-preview__text">{{ text || 'Текст объявления появится на этапе объявлений' }}</p>
    <div v-if="total > 1" class="ad-preview__nav">
      <button type="button" class="ad-preview__arrow" aria-label="Предыдущий вариант" @click="step(-1)">‹</button>
      <span>Вариант {{ index + 1 }} из {{ total }}</span>
      <button type="button" class="ad-preview__arrow" aria-label="Следующий вариант" @click="step(1)">›</button>
      <small v-if="titles.length > 1 && texts.length > 1">
        {{ titles.length }} {{ plural(titles.length, 'заголовок', 'заголовка', 'заголовков') }} ×
        {{ texts.length }} {{ plural(texts.length, 'текст', 'текста', 'текстов') }}
      </small>
    </div>
  </div>
</template>

<script setup>
/**
 * Как объявление увидят в поиске Яндекса. Объявление комбинаторное: Директ сам сочетает заголовки
 * и тексты, поэтому листаем варианты.
 */
import { computed, ref, watch } from 'vue'
import { plural } from '../../utils/format'

const props = defineProps({
  titles: { type: Array, default: () => [] },
  texts: { type: Array, default: () => [] },
  href: { type: String, default: '' },
  // Сайт пространства — когда у объявления нет своей ссылки (её подставит шаблон).
  site: { type: String, default: '' },
  pending: { type: Boolean, default: false },
})

const index = ref(0)
const total = computed(() => Math.max(props.titles.length, 1) * Math.max(props.texts.length, 1))
const title = computed(() => props.titles[index.value % Math.max(props.titles.length, 1)] || '')
const text = computed(
  () => props.texts[Math.floor(index.value / Math.max(props.titles.length, 1)) % Math.max(props.texts.length, 1)] || '',
)
const domain = computed(() => hostOf(props.href) || hostOf(props.site) || 'ваш-сайт.ru')

watch(total, (n) => {
  if (index.value >= n) index.value = 0
})

function hostOf(value) {
  const raw = (value || '').trim()
  if (!raw) return ''
  try {
    return new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

function step(delta) {
  index.value = (index.value + delta + total.value) % total.value
}
</script>
