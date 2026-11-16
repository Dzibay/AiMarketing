<template>
  <aside
    class="aip"
    :class="`aip--${mode}`"
    :role="mode === 'dock' ? 'complementary' : 'dialog'"
    aria-label="ИИ-помощник"
    @keydown.esc="onEsc"
  >
    <!-- Свой чат у каждого проекта и у «Все проекты»: сменили проект — панель показывает его чат. -->
    <PanelChat v-if="!projectId || project" :key="project ? project.id : 'general'" :project="project" @close="closeAiPanel" />
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { closeAiPanel } from '../composables/useAiPanel'
import { useProjectFilter } from '../composables/useProjectFilter'
import { useWorkspaces } from '../composables/useWorkspaces'
import PanelChat from './chat/PanelChat.vue'

/**
 * Боковая панель ИИ: живёт в оболочке кабинета (AppShell) и не закрывается при переходах между страницами.
 * mode: dock — от 1280 px, колонкой справа, страница сужается; over — уже, поверх страницы; full — телефон, на весь экран.
 */
const props = defineProps({
  mode: { type: String, default: 'dock' },
})

const { projectId } = useProjectFilter()
const { workspaces } = useWorkspaces()
const project = computed(() => (projectId.value ? workspaces.value.find((w) => w.id === projectId.value) || null : null))

function onEsc(event) {
  // В колонке справа Esc панель не закрывает: она часть экрана, а не окно поверх.
  if (props.mode === 'dock' || event.defaultPrevented) return
  closeAiPanel()
}
</script>
