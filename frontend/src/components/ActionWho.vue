<template>
  <span v-if="who" class="hwho" :class="`hwho--${source}`">
    <component :is="who.icon" aria-hidden="true" />{{ who.label }}
  </span>
</template>

<script setup>
/**
 * Кто сделал изменение в журнале: «ИИ» или «Правило». «Вы» не пишем — это почти каждая запись.
 * Чип нейтральный в обеих темах: цвет даёт только иконка, поэтому на любом фоне строки он читается одинаково.
 */
import { computed } from 'vue'
import { Sparkles, Zap } from '@lucide/vue'

const props = defineProps({
  source: { type: String, default: '' },
})

const WHO = {
  ai: { label: 'ИИ', icon: Sparkles },
  rule: { label: 'Правило', icon: Zap },
}

const who = computed(() => WHO[props.source] || null)
</script>

<style scoped>
.hwho {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px 1px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
  color: var(--ink-2, var(--ink));
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  white-space: nowrap;
}

.hwho svg {
  width: 12px;
  height: 12px;
  color: var(--muted);
}

.hwho--ai svg {
  color: var(--accent);
}
</style>
