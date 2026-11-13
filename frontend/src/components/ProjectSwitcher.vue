<template>
  <span class="pswitch">
    <button
      ref="trigger"
      class="dchip dchip--btn pswitch__btn"
      :class="{ 'is-open': open }"
      type="button"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-label="triggerLabel"
      v-tip="'Сменить проект'"
      @click="toggle"
      @keydown.down.prevent="openPanel"
    >
      <template v-if="project">
        <span class="sdot" :class="`sdot--${projectTone(project)}`" aria-hidden="true"></span>
        <span class="dchip__t">{{ niceQuotes(project.name) }}</span>
      </template>
      <template v-else>
        <Layers aria-hidden="true" />
        <span class="dchip__t">Все проекты</span>
      </template>
      <ChevronDown class="pswitch__caret" aria-hidden="true" />
    </button>

    <Teleport to="body">
      <div v-if="open" class="pswitch-scrim" aria-hidden="true"></div>
      <div
        v-if="open"
        ref="panel"
        class="ui-pop pswitch__pop"
        role="dialog"
        aria-label="Выбор проекта"
        :style="style"
        @keydown="onKey"
      >
        <label v-if="searchable" class="pswitch__search">
          <Search aria-hidden="true" />
          <input
            ref="searchEl"
            v-model="query"
            type="search"
            placeholder="Найти проект"
            aria-label="Найти проект"
            autocomplete="off"
            @keydown.down.prevent="focusItem(0)"
            @keydown.enter.prevent="pickFirst"
          />
        </label>

        <div class="pswitch__list">
          <button
            v-if="!needle"
            class="menu-item pswitch__item"
            :class="{ 'is-current': !projectId }"
            type="button"
            tabindex="-1"
            :aria-current="projectId ? undefined : 'true'"
            @click="pick(null)"
          >
            <Layers aria-hidden="true" />
            <span class="menu-item__text">Все проекты</span>
            <Check v-if="!projectId" class="pswitch__check" aria-hidden="true" />
          </button>
          <template v-for="group in groups" :key="group.id">
            <p v-if="group.name" class="pswitch__group">{{ niceQuotes(group.name) }}</p>
            <button
              v-for="ws in group.items"
              :key="ws.id"
              class="menu-item pswitch__item"
              :class="{ 'is-current': ws.id === projectId }"
              type="button"
              tabindex="-1"
              :aria-current="ws.id === projectId ? 'true' : undefined"
              v-tip="[niceQuotes(ws.name), projectHint(ws)].filter(Boolean).join(' — ')"
              @click="pick(ws.id)"
            >
              <span class="sdot" :class="`sdot--${projectTone(ws)}`" v-bind="projectDotA11y(ws)"></span>
              <span class="menu-item__text">{{ niceQuotes(ws.name) }}</span>
              <Lock v-if="ws.locked" class="pswitch__lock" aria-label="не входит в тариф" />
              <Check v-if="ws.id === projectId" class="pswitch__check" aria-hidden="true" />
            </button>
          </template>
          <p v-if="needle && !groups.length" class="pswitch__empty">Проектов с таким названием нет</p>
        </div>

        <div class="pswitch__foot">
          <RouterLink
            v-if="project"
            class="menu-item pswitch__item"
            :to="`/w/${project.id}/settings`"
            tabindex="-1"
            @click="close()"
          >
            <Settings aria-hidden="true" />
            <span class="menu-item__text">Настройки проекта</span>
          </RouterLink>
          <RouterLink class="menu-item pswitch__item" to="/cabinet/connections" tabindex="-1" @click="close()">
            <Plus aria-hidden="true" />
            <span class="menu-item__text">Добавить сайт</span>
          </RouterLink>
        </div>
      </div>
    </Teleport>
  </span>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, ChevronDown, Layers, Lock, Plus, Search, Settings } from '@lucide/vue'
import { useExpert } from '../composables/useExpert'
import { usePopover } from '../composables/usePopover'
import { useProjectFilter } from '../composables/useProjectFilter'
import { niceQuotes } from '../utils/format'
import {
  projectDotA11y,
  projectFolders,
  projectHint,
  projectTone,
  useWorkspaces,
} from '../composables/useWorkspaces'

/**
 * Переключатель проекта в верхней строке: «Все проекты» и список проектов (в экспертном режиме — по папкам
 * клиентов), внизу настройки выбранного проекта и «Добавить сайт». Выбор открывает тот же раздел у другого проекта.
 * Клавиатура: ↓/↑ — по пунктам, Enter — выбрать, Esc — закрыть; набранный текст сразу уходит в поиск.
 * На телефоне панель выезжает снизу во всю ширину.
 */
const SEARCH_FROM = 7

const { projectId, project, selectProject } = useProjectFilter()
const { workspaces, grouped } = useWorkspaces()
const { expert } = useExpert()

const trigger = ref(null)
const panel = ref(null)
const searchEl = ref(null)
const query = ref('')
const { open, style, show, close } = usePopover(trigger, panel, {
  align: 'start',
  follow: true,
  onClose: () => (query.value = ''),
})

const searchable = computed(() => workspaces.value.length >= SEARCH_FROM)
const needle = computed(() => query.value.trim().toLocaleLowerCase('ru'))

const groups = computed(() => {
  const q = needle.value
  const hit = (ws) => !q || `${ws.name} ${ws.site || ''}`.toLocaleLowerCase('ru').includes(q)
  return projectFolders(workspaces.value, grouped.value, expert.value)
    .map((g) => ({ ...g, items: g.items.filter(hit) }))
    .filter((g) => g.items.length)
})

const triggerLabel = computed(() => {
  if (!project.value) return 'Все проекты — сменить проект'
  const status = projectTone(project.value) === 'warn' ? ' (счётчик Метрики недоступен)' : ''
  return `${niceQuotes(project.value.name)}${status} — сменить проект`
})

function items() {
  return [...(panel.value?.querySelectorAll('.pswitch__item') || [])]
}

function focusItem(index) {
  items().at(index)?.focus()
}

async function openPanel() {
  await show()
  await nextTick()
  if (searchable.value && !window.matchMedia('(max-width: 640px)').matches) {
    // На телефоне поле не фокусируем сразу: клавиатура закрыла бы половину списка.
    searchEl.value?.focus()
    return
  }
  const list = items()
  ;(list.find((el) => el.classList.contains('is-current')) || list[0])?.focus()
}

function toggle() {
  if (open.value) close()
  else openPanel()
}

function pick(id) {
  close({ focus: true })
  if (id !== projectId.value) selectProject(id)
}

function pickFirst() {
  const first = groups.value[0]?.items[0]
  if (first) pick(first.id)
}

function onKey(event) {
  const list = items()
  const at = list.indexOf(document.activeElement)
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close({ focus: true })
  } else if (event.key === 'Tab') {
    event.preventDefault()
    close({ focus: true })
  } else if (event.target === searchEl.value) {
    // Стрелка вниз и Enter в поле поиска — свои обработчики у поля.
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    list[(at + 1) % list.length]?.focus()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (at <= 0 && searchEl.value) searchEl.value.focus()
    else list.at(at <= 0 ? -1 : at - 1)?.focus()
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    list.at(event.key === 'Home' ? 0 : -1)?.focus()
  } else if (searchEl.value && event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    // Набор на пункте списка — в поиск: фокус переносится до ввода символа, и символ попадает в поле.
    searchEl.value.focus()
  }
}
</script>
