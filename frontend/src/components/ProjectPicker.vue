<template>
  <div ref="root" class="project-picker" :class="{ 'is-open': open, 'is-compact': compact }">
    <button
      class="project-picker__toggle"
      type="button"
      :aria-expanded="open"
      aria-haspopup="listbox"
      :title="compact ? currentLabel : undefined"
      @click="toggle"
    >
      <span class="project-picker__icon" aria-hidden="true"><component :is="project ? FolderOpen : Layers" /></span>
      <span class="project-picker__text">
        <small>{{ project ? project.client_name || 'Проект' : 'Фильтр' }}</small>
        <strong>{{ currentLabel }}</strong>
      </span>
      <ChevronsUpDown class="project-picker__caret" aria-hidden="true" />
    </button>

    <div v-if="open" class="project-picker__menu">
      <input
        ref="searchInput"
        v-model="search"
        class="project-picker__search"
        type="search"
        placeholder="Проект, сайт или клиент"
        @keydown.enter.prevent="pickFirst"
      />
      <div class="project-picker__groups" role="listbox">
        <button
          v-if="!search"
          type="button"
          role="option"
          class="project-picker__item project-picker__item--all"
          :class="{ 'is-current': !projectId }"
          :aria-selected="!projectId"
          @click="pick(null)"
        >
          <Layers aria-hidden="true" />
          <span><strong>Все проекты</strong></span>
          <Check v-if="!projectId" class="project-picker__check" aria-hidden="true" />
        </button>
        <div v-for="group in filtered" :key="group.id" class="project-picker__group">
          <div class="project-picker__group-name">{{ group.name }}</div>
          <button
            v-for="ws in group.items"
            :key="ws.id"
            type="button"
            role="option"
            class="project-picker__item"
            :class="{ 'is-current': ws.id === projectId, 'is-locked': ws.locked }"
            :aria-selected="ws.id === projectId"
            @click="pick(ws.id)"
          >
            <span>
              <strong>{{ ws.name }}</strong>
              <small>{{ ws.site || campaignsText(ws.campaigns_count) }}</small>
            </span>
            <span v-if="ws.locked" class="project-picker__lock" title="Проект не входит в тариф">
              <Lock aria-hidden="true" />
            </span>
            <span v-else-if="!ws.counter_ok" class="chip chip--warning" title="Счётчик недоступен или выключен">!</span>
            <Check v-else-if="ws.id === projectId" class="project-picker__check" aria-hidden="true" />
          </button>
        </div>
        <p v-if="search && !filtered.length" class="empty project-picker__empty">Ничего не найдено.</p>
        <p v-else-if="!workspaces.length" class="empty project-picker__empty">
          Подключите Метрику — для каждого счётчика появится проект.
        </p>
      </div>
      <div class="project-picker__footer">
        <RouterLink to="/cabinet/connections" @click="open = false">Подключения</RouterLink>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, ChevronsUpDown, FolderOpen, Layers, Lock } from '@lucide/vue'
import { useProjectFilter } from '../composables/useProjectFilter'
import { useWorkspaces } from '../composables/useWorkspaces'
import { plural } from '../utils/format'

/** Переключатель проекта вверху меню — фильтр разделов: «Все проекты» или один проект. */
defineProps({ compact: { type: Boolean, default: false } })

const { workspaces, grouped } = useWorkspaces()
const { projectId, project, selectProject } = useProjectFilter()

const root = ref(null)
const searchInput = ref(null)
const open = ref(false)
const search = ref('')

const currentLabel = computed(() => project.value?.name || 'Все проекты')

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
  return `${v} ${plural(v, 'кампания', 'кампании', 'кампаний')}`
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
  selectProject(id)
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
