<script setup>
// Кросс-минусовка: если все слова фразы A есть в более длинной фразе B, к A дописываются лишние слова B с минусом.
// Слова сравниваются как написаны (регистр и ё/е не важны), словоформы не сводятся.
import { computed, ref } from 'vue'
import { useCopy } from './useCopy.js'

const input = ref('купить диван\nкупить диван угловой\nкупить диван угловой недорого\nдиван кровать\nкупить диван кровать')
const out = ref(null)
const { copied, copy } = useCopy()

const norm = (w) => w.toLowerCase().replace(/ё/g, 'е').replace(/^[!+]+/, '')

function parse(line) {
  // Свои минус-слова и операторы [] "" не трогаем: сравниваем только «плюс»-слова.
  const own = line.trim().replace(/["[\]]/g, '')
  const parts = own.split(/\s+/).filter(Boolean)
  const plus = parts.filter((w) => !w.startsWith('-'))
  const minus = parts.filter((w) => w.startsWith('-')).map((w) => norm(w.slice(1)))
  return { src: line.trim(), plus, set: new Set(plus.map(norm)), minus }
}

const rows = computed(() => {
  const phrases = input.value.split('\n').map(parse).filter((p) => p.plus.length)
  return phrases.map((a) => {
    const add = new Set()
    for (const b of phrases) {
      if (b === a || b.set.size <= a.set.size) continue
      if (![...a.set].every((w) => b.set.has(w))) continue
      for (const w of b.set) if (!a.set.has(w) && !a.minus.includes(w)) add.add(w)
    }
    const extra = [...add].map((w) => `-${w}`)
    return { phrase: [a.src, ...extra].join(' '), added: extra.length, words: a.plus.length }
  })
})

const text = computed(() => rows.value.map((r) => r.phrase).join('\n'))
const changed = computed(() => rows.value.filter((r) => r.added).length)
const tooLong = computed(() => rows.value.filter((r) => r.words > 7).length)
</script>

<template>
  <div class="tool">
    <div class="tool__panel">
      <div class="tool__field">
        <label class="tool__label" for="cm-in">Ключевые фразы <span class="tool__hint">по одной в строке</span></label>
        <textarea id="cm-in" v-model="input" rows="12" spellcheck="false" />
        <span class="tool__hint">Лучше вставлять фразы одной группы или одной кампании: между разными темами минусовать нечего.</span>
      </div>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <span class="tool__cap">Результат · изменено {{ changed }} из {{ rows.length }}</span>
      <textarea ref="out" :value="text" readonly rows="12" spellcheck="false" aria-label="Фразы с минус-словами" />
      <p v-if="tooLong" class="tool__note">
        {{ tooLong }} фраз(ы) длиннее 7 слов — Директ такие не примет, сократите.
      </p>
      <div class="tool__row">
        <button type="button" class="btn btn--lime" :disabled="!rows.length" @click="copy(text, out)">
          {{ copied ? 'Скопировано' : 'Скопировать' }}
        </button>
      </div>
    </div>
  </div>
</template>
