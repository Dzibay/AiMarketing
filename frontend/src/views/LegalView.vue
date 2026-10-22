<template>
  <div class="site">
    <SiteHeader solid />
    <main class="legal">
      <div class="wrap">
        <article v-if="doc">
          <h1>{{ doc.heading }}</h1>
          <p class="meta">{{ doc.meta }}</p>
          <p v-for="(paragraph, index) in doc.paragraphs" :key="index">{{ paragraph }}</p>
        </article>
        <p v-else class="empty">Загружаем документ…</p>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { fetchLegalPage } from '../api/site'
import { useHead } from '../utils/useHead'

const props = defineProps({
  page: { type: String, required: true },
})

const doc = ref(null)

async function load() {
  doc.value = null
  try {
    doc.value = await fetchLegalPage(props.page)
    useHead(doc.value.title || 'Документ — AiMarketing', true)
  } catch {
    doc.value = {
      heading: 'Документ недоступен',
      meta: '',
      paragraphs: ['Не удалось загрузить страницу. Попробуйте позже.'],
      title: 'Документ — AiMarketing',
    }
    useHead(doc.value.title, true)
  }
}

watch(() => props.page, load)
onMounted(load)
</script>
