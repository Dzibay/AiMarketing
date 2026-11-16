<template>
  <!-- Вопрос про кампании — в панели ИИ: отмеченные галочкой уходят с вопросом в чипе «Про: …» (usePageContext). -->
  <div class="cask">
    <button class="cask-open" type="button" @click="openAiPanel">
      <Sparkles :size="17" aria-hidden="true" />
      <span>{{ text }}</span>
      <ArrowRight :size="16" class="cask-open__go" aria-hidden="true" />
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ArrowRight, Sparkles } from '@lucide/vue'
import { openAiPanel } from '../composables/useAiPanel'
import { plural } from '../utils/format'

/**
 * «Спросить про выбранные» под списком кампаний: открывает панель ИИ. Что выбрано, панель знает от страницы
 * (usePageContext) — здесь только подпись кнопки.
 */
const props = defineProps({
  selected: { type: Set, required: true },
})

const text = computed(() => {
  const n = props.selected.size
  return n ? `Спросить про ${n} ${plural(n, 'выбранную', 'выбранные', 'выбранных')}` : 'Спросить ИИ про кампании'
})
</script>

<style scoped>
.cask {
  position: sticky;
  bottom: calc(16px + var(--bbar-h));
  z-index: 5;
  display: flex;
  justify-content: center;
  margin-top: 20px;
  pointer-events: none;
}
.cask-open {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 0 6px 0 16px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  box-shadow: var(--shadow);
  color: var(--ink);
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  pointer-events: auto;
}
.cask-open:hover {
  border-color: var(--faint);
}
.cask-open__go {
  box-sizing: content-box;
  flex: none;
  padding: 9px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--bg);
}
@media (max-width: 640px) {
  .cask-open {
    width: 100%;
    border-radius: 18px;
  }
  .cask-open > span {
    flex: 1;
    text-align: left;
  }
}
</style>
