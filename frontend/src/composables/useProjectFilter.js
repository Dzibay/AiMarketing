import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { SECTIONS, sectionTo } from '../components/shellNav'
import { useWorkspaces } from './useWorkspaces'

const KEY = 'aimarketing-project-filter'

// Выбранный проект — фильтр всех разделов. На странице проекта (/w/:id/…) он берётся из адреса, на странице
// «по всем проектам» — сброшен; страницы вне проектов (подключения, тариф) его не трогают.
const stored = ref(Number(localStorage.getItem(KEY) || 0) || null)

function store(id) {
  stored.value = id || null
  if (id) localStorage.setItem(KEY, String(id))
  else localStorage.removeItem(KEY)
}

export function resetProjectFilter() {
  store(null)
}

/** Подвкладки проекта, у которых нет своей версии «по всем проектам»: куда вести при «Все проекты». */
const ALL_FALLBACK = { slices: '/cabinet/analytics', 'ad-stats': '/cabinet/analytics', experiments: '/cabinet/autopilot' }
/** Подвкладки, которые можно открыть у другого проекта как есть (без id кампании или чата). */
const KEEP_TABS = new Set(['analytics', 'slices', 'ad-stats', 'experiments', 'queries', 'autopilot', 'history'])

export function useProjectFilter() {
  const route = useRoute()
  const router = useRouter()
  const { workspaces } = useWorkspaces()

  const projectId = computed(() => {
    if (route.params.wsId) return Number(route.params.wsId)
    if (route.meta.scope === 'all') return null
    const id = stored.value
    // Проект удалили, отправили в архив или он из чужой сессии — фильтр «Все проекты».
    if (id && workspaces.value.length && !workspaces.value.some((w) => w.id === id)) return null
    return id
  })
  const project = computed(() => workspaces.value.find((w) => w.id === projectId.value) || null)
  const section = computed(() => SECTIONS.find((s) => s.key === route.meta.section) || null)

  watch(
    () => [route.params.wsId, route.meta.scope],
    () => {
      if (route.params.wsId) store(Number(route.params.wsId))
      else if (route.meta.scope === 'all') store(null)
    },
    { immediate: true },
  )

  /** Выбор в переключателе: тот же раздел по выбранному проекту или по всем; вне разделов — только фильтр. */
  function selectProject(id) {
    const next = id || null
    if (route.meta.scope === 'neutral' || !section.value) {
      store(next)
      return
    }
    const tab = route.params.wsId ? route.path.split('/')[3] || 'summary' : ''
    let to
    if (next) {
      to = tab && KEEP_TABS.has(tab) ? `/w/${next}/${tab}` : sectionTo(section.value, next)
    } else {
      to = ALL_FALLBACK[tab] || sectionTo(section.value, null) || '/cabinet'
    }
    store(next)
    if (to !== route.path) router.push(to)
  }

  return { projectId, project, section, selectProject }
}
