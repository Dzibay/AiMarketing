import { ref } from 'vue'

/**
 * Боковая панель ИИ (components/AiPanel.vue): открыта или нет. Живёт в оболочке кабинета и не закрывается при
 * переходах. Шире 900 px выбор запоминается; на телефоне панель — шторка на весь экран, её каждый раз открывают сами.
 */
const KEY = 'aimarketing-ai-panel'
const wide = () => typeof window !== 'undefined' && window.matchMedia('(min-width: 901px)').matches

export const aiPanelOpen = ref(wide() && localStorage.getItem(KEY) === 'open')
// Растёт при каждом открытии — панель ставит курсор в поле ввода.
export const aiPanelFocus = ref(0)

function remember() {
  if (wide()) localStorage.setItem(KEY, aiPanelOpen.value ? 'open' : 'closed')
}

export function openAiPanel() {
  aiPanelOpen.value = true
  aiPanelFocus.value++
  remember()
}

export function closeAiPanel() {
  aiPanelOpen.value = false
  remember()
}

export function toggleAiPanel() {
  if (aiPanelOpen.value) closeAiPanel()
  else openAiPanel()
}
