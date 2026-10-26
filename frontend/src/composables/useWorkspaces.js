import { computed, ref } from 'vue'
import { webApi, wsApi } from '../api/web'

const LAST_KEY = 'aimarketing-last-workspace'

const workspaces = ref([])
const clients = ref([])
const loaded = ref(false)
const loading = ref(false)
let pending = null

/** Список пространств и папок-клиентов — общий для шапки, обзора и настроек. */
export async function loadWorkspaces(force = false) {
  if (loaded.value && !force) return workspaces.value
  if (pending) return pending
  loading.value = true
  pending = Promise.all([wsApi.list(), webApi.clients()])
    .then(([ws, cl]) => {
      workspaces.value = ws.items || []
      clients.value = cl.items || []
      loaded.value = true
      return workspaces.value
    })
    .finally(() => {
      loading.value = false
      pending = null
    })
  return pending
}

/** Обновить одно пространство в списке без перезагрузки (после PATCH). */
export function patchWorkspaceInList(ws) {
  if (!ws?.id) return
  const idx = workspaces.value.findIndex((w) => w.id === ws.id)
  if (idx === -1) workspaces.value = [...workspaces.value, ws]
  else workspaces.value = workspaces.value.map((w) => (w.id === ws.id ? { ...w, ...ws } : w))
}

export function resetWorkspaces() {
  workspaces.value = []
  clients.value = []
  loaded.value = false
}

export function rememberWorkspace(id) {
  if (id) localStorage.setItem(LAST_KEY, String(id))
}

export function lastWorkspaceId() {
  const id = Number(localStorage.getItem(LAST_KEY) || 0)
  return workspaces.value.some((w) => w.id === id) ? id : workspaces.value[0]?.id || null
}

/** Пространства по папкам: сначала папки клиентов по алфавиту, в конце «Без клиента». */
const grouped = computed(() => {
  const byClient = new Map()
  for (const ws of workspaces.value) {
    const key = ws.client_id || 0
    if (!byClient.has(key)) byClient.set(key, [])
    byClient.get(key).push(ws)
  }
  const groups = clients.value
    .filter((c) => byClient.has(c.id))
    .map((c) => ({ id: c.id, name: c.name, items: byClient.get(c.id) }))
    .sort((a, b) => a.name.localeCompare(b.name, 'ru'))
  if (byClient.has(0)) groups.push({ id: 0, name: 'Без клиента', items: byClient.get(0) })
  return groups
})

export function useWorkspaces() {
  return {
    workspaces,
    clients,
    grouped,
    loaded,
    loading,
    loadWorkspaces,
    patchWorkspaceInList,
    rememberWorkspace,
    lastWorkspaceId,
  }
}
