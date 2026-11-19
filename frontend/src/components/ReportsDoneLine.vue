<template>
  <p v-if="status && status.total" class="reports-line">
    <FileText class="inline-icon" aria-hidden="true" />
    Отчёты за {{ status.month_name }}: готово {{ status.done }} из {{ status.total }}
    <button class="link-btn" type="button" :aria-expanded="open" @click="open = !open">
      {{ open ? 'скрыть' : 'какие' }}
    </button>
  </p>
  <ul v-if="open && status?.total" class="reports-line__list">
    <li v-for="item in status.items" :key="item.workspace_id">
      <RouterLink :to="{ path: `/w/${item.workspace_id}/report`, query: { month: status.month } }">{{ niceQuotes(item.name) }}</RouterLink>
      <span :class="item.done ? 'tone-ok' : 'reports-line__todo'">{{ item.done ? 'готов' : 'не готов' }}</span>
    </li>
  </ul>
</template>

<script setup>
// «Отчёты за октябрь: готово 3 из 9» на общей сводке: готов — распечатали или нажали «Готово» в «Отчёте клиенту».
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { FileText } from '@lucide/vue'
import { reportApi } from '../api/web'
import { niceQuotes } from '../utils/format'

const status = ref(null)
const open = ref(false)

onMounted(async () => {
  try {
    status.value = await reportApi.status()
  } catch {
    status.value = null // строка необязательная — без неё сводка та же
  }
})
</script>

<style scoped>
.reports-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin: 0.5rem 0 1rem;
  color: var(--muted);
  font-size: 0.9rem;
}
.reports-line__list {
  list-style: none;
  margin: -0.5rem 0 1rem;
  padding: 0;
  display: grid;
  gap: 0.25rem;
  font-size: 0.9rem;
}
.reports-line__list li {
  display: flex;
  gap: 0.5rem;
}
.reports-line__todo {
  color: var(--faint);
}
</style>
