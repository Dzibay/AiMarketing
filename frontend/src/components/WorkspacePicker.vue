<template>
  <div ref="root" class="ws-picker" :class="{ 'is-open': open }">
    <button class="ws-picker__toggle" type="button" :aria-expanded="open" @click="toggle">
      <span class="ws-picker__label">Счётчик</span>
      <span class="ws-picker__current">
        <template v-if="current">
          {{ current.name }}
          <small>№ {{ current.counter_id }}</small>
        </template>
        <template v-else>{{ workspaces.length ? 'Выберите пространство' : 'Нет счётчиков' }}</template>
      </span>
      <span class="ws-picker__caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="open" class="ws-picker__menu">
      <input
        ref="searchInput"
        v-model="search"
        class="ws-picker__search"
        type="search"
        placeholder="Сайт, счётчик или клиент"
        @keydown.enter.prevent="pickFirst"
      />
      <div class="ws-picker__groups">
        <div v-for="group in filtered" :key="group.id" class="ws-picker__group">
          <div class="ws-picker__group-name">{{ group.name }}</div>
          <button
            v-for="ws in group.items"
            :key="ws.id"
            type="button"
            class="ws-picker__item"
            :class="{ 'is-current': ws.id === currentId }"
            @click="pick(ws.id)"
          >
            <span>
              <strong>{{ ws.name }}</strong>
              <small>№ {{ ws.counter_id }} · {{ campaignsText(ws.campaigns_count) }}</small>
            </span>
            <span v-if="!ws.counter_ok" class="chip chip--warning" title="Счётчик недоступен или выключен">!</span>
          </button>
        </div>
        <p v-if="!filtered.length" class="empty ws-picker__empty">
          {{ workspaces.length ? 'Ничего не найдено.' : 'Подключите Метрику — для каждого счётчика появится пространство.' }}
        </p>
      </div>
      <div class="ws-picker__footer">
        <RouterLink to="/cabinet" @click="open = false">Все пространства</RouterLink>
        <RouterLink to="/cabinet/accounts" @click="open = false">Подключить аккаунты</RouterLink>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useWorkspaces } from '../composables/useWorkspaces'

const route = useRoute()
const router = useRouter()
const { workspaces, grouped } = useWorkspaces()

const root = ref(null)
const searchInput = ref(null)
const open = ref(false)
const search = ref('')

const currentId = computed(() => Number(route.params.wsId || 0) || null)
const current = computed(() => workspaces.value.find((w) => w.id === currentId.value) || null)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return grouped.value
  return grouped.value
    .map((g) => ({
      ...g,
      items: g.items.filter((ws) =>
        [ws.name, ws.site, String(ws.counter_id), ws.counter_name, g.name].some((v) =>
          String(v || '').toLowerCase().includes(q),
        ),
      ),
    }))
    .filter((g) => g.items.length)
})

function campaignsText(n) {
  const v = Number(n || 0)
  const mod10 = v % 10
  const mod100 = v % 100
  let word = 'кампаний'
  if (mod10 === 1 && mod100 !== 11) word = 'кампания'
  else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) word = 'кампании'
  return `${v} ${word}`
}

async function toggle() {
  open.value = !open.value
  if (open.value) {
    search.value = ''
    await nextTick()
    searchInput.value?.focus()
  }
}

function pick(id) {
  open.value = false
  // На той же вкладке другого пространства, если она не привязана к конкретной кампании.
  const tab = route.path.startsWith('/w/') ? route.path.split('/')[3] || 'summary' : 'summary'
  const safeTab = ['summary', 'analytics', 'campaigns', 'actions', 'chat', 'settings'].includes(tab) ? tab : 'summary'
  router.push(`/w/${id}/${safeTab}`)
}

function pickFirst() {
  const first = filtered.value[0]?.items[0]
  if (first) pick(first.id)
}

function onDocClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}

function onKey(event) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocClick)
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  window.removeEventListener('keydown', onKey)
})
</script>
