<template>
  <div class="qwords" :class="{ 'qwords--sm': small }">
    <div class="qwords__list" role="group" :aria-label="`Слова запроса «${query}»`">
      <button
        v-for="(w, i) in words"
        :key="`${i}:${w.norm}`"
        class="qwords__w"
        :class="{ 'is-on': picked[i] }"
        type="button"
        :aria-pressed="picked[i]"
        :disabled="disabled || locked"
        @click="emit('update:modelValue', toggleWord(query, modelValue, i))"
      >
        {{ w.text }}
      </button>
    </div>
    <p v-if="locked" class="qwords__hint">Во фразе есть скобки или кавычки — меняйте её в поле.</p>
    <p v-else-if="hint && !picked.some(Boolean)" class="qwords__hint">Нажмите слова, которые нужно отсечь</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { phraseLocked, pickedWords, queryWords, toggleWord } from '../utils/queryWords'

/**
 * Запрос словами-«таблетками»: клик по слову добавляет его в минус-фразу или убирает из неё.
 * Фраза собирается в порядке слов запроса; поле фразы рядом можно править руками — таблетки это подхватят.
 */
const props = defineProps({
  query: { type: String, default: '' },
  /** Минус-фраза. */
  modelValue: { type: String, default: '' },
  /** Подсказка, пока ни одно слово не выбрано. */
  hint: { type: Boolean, default: true },
  /** Компактно — для строки таблицы. */
  small: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const words = computed(() => queryWords(props.query))
const picked = computed(() => pickedWords(props.query, props.modelValue))
// [ ] и кавычки действуют на несколько слов сразу — такую фразу кликом не пересобрать, слова только показываем.
const locked = computed(() => phraseLocked(props.modelValue))
</script>

<style scoped>
.qwords__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.qwords__w {
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: none;
  color: var(--ink-2);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.qwords__w:hover:not(:disabled) {
  border-color: var(--ink);
}
.qwords__w.is-on {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--bg);
}
.qwords__w:disabled {
  cursor: default;
  opacity: 0.6;
}
.qwords__hint {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.qwords--sm .qwords__list {
  gap: 4px;
}
.qwords--sm .qwords__w {
  min-height: 26px;
  padding: 0 9px;
  font-size: 12px;
}
.qwords--sm .qwords__hint {
  margin-top: 4px;
  font-size: 12px;
}
</style>
