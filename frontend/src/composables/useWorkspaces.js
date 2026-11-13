import { computed, ref } from 'vue'
import { webApi, wsApi } from '../api/web'

const LAST_KEY = 'aimarketing-last-workspace'

const workspaces = ref([])
const clients = ref([])
const loaded = ref(false)
const loading = ref(false)
let pending = null

/** Список проектов и папок-клиентов — общий для шапки, обзора и настроек. */
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

/** Обновить одно проект в списке без перезагрузки (после PATCH). */
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

/** Проекта по папкам: сначала папки клиентов по алфавиту, в конце «Без клиента». */
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

/** Состояние проекта для точки в меню и переключателе: off — не входит в тариф, warn — счётчик Метрики недоступен. */
export function projectTone(ws) {
  if (ws.locked) return 'off'
  return ws.counter_ok === false ? 'warn' : 'ok'
}

/** Статус не только цветом: «проблемная» точка — кольцо (.sdot--warn) и с текстом для экранного диктора. */
export function projectDotA11y(ws) {
  return projectTone(ws) === 'warn' ? { role: 'img', 'aria-label': 'счётчик Метрики недоступен' } : { 'aria-hidden': 'true' }
}

/** Подсказка к проекту: что не так, а если всё в порядке — адрес сайта. */
export function projectHint(ws) {
  if (ws.locked) return 'Проект не входит в тариф'
  if (ws.counter_ok === false) return 'Счётчик Метрики недоступен или выключен'
  return ws.site || ''
}

/**
 * Проекты для меню и переключателя. Обычный режим — один плоский список. Экспертный — папки клиентов, в которых два
 * проекта и больше; клиент с одним проектом и проекты без клиента идут выше папок без заголовка.
 */
export function projectFolders(list, groups, expert) {
  const loose = (items) => ({ id: 'loose', name: '', items })
  const folders = expert ? groups.filter((g) => g.id && g.items.length > 1) : []
  if (!folders.length) return [loose(list)]
  const inFolder = new Set(folders.flatMap((g) => g.items.map((w) => w.id)))
  const rest = list.filter((w) => !inFolder.has(w.id))
  return [...(rest.length ? [loose(rest)] : []), ...folders]
}

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
