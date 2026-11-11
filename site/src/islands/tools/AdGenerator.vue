<script setup>
// Генератор объявлений по шаблонам: товар + где + преимущества → заголовки и тексты в лимитах Директа.
// Без ИИ: только перестановки ваших слов. ИИ-версия на данных кабинета — в самом Толке.
import { computed, ref } from 'vue'
import { useCopy } from './useCopy.js'

const TITLE = 56
const TITLE2 = 30
const TEXT = 81

const product = ref('Ремонт квартир')
const where = ref('в Казани')
const advs = ref('Смета за 1 день\nГарантия 3 года\nФиксированная цена\nСвоя бригада')
const cta = ref('Вызовите замерщика')
const out = ref(null)
const { copied, copy } = useCopy()

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)
const low = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s)
const clean = (s) => s.trim().replace(/\s+/g, ' ').replace(/[.!]+$/, '')

const list = computed(() => advs.value.split('\n').map(clean).filter(Boolean).slice(0, 6))

function uniq(arr, limit) {
  const seen = new Set()
  return arr
    .map((s) => cap(s.replace(/\s+/g, ' ').trim()))
    .filter((s) => s && s.length <= limit && !seen.has(s) && seen.add(s))
}

const titles = computed(() => {
  const p = clean(product.value)
  const w = clean(where.value)
  const a = list.value
  if (!p) return []
  return uniq(
    [
      w && `${p} ${w}`,
      ...a.map((x) => `${p} — ${low(x)}`),
      ...a.map((x) => w && `${p} ${w} — ${low(x)}`),
      ...a.map((x) => `${x}: ${low(p)}`),
    ].filter(Boolean),
    TITLE,
  ).slice(0, 7)
})

const titles2 = computed(() => uniq([...list.value, clean(cta.value)].filter(Boolean), TITLE2).slice(0, 5))

const texts = computed(() => {
  const p = clean(product.value)
  const w = clean(where.value)
  const a = list.value
  const c = clean(cta.value)
  const pairs = a.flatMap((x, i) => a.slice(i + 1).map((y) => [x, y]))
  return uniq(
    [
      ...pairs.map(([x, y]) => `${x}. ${y}. ${c}!`),
      ...pairs.map(([x, y]) => `${x}, ${low(y)}. ${c}!`),
      ...a.map((x) => p && `${p}${w ? ' ' + w : ''}: ${low(x)}. ${c}!`),
      ...a.map((x) => `${x}. ${c}!`),
    ].filter(Boolean),
    TEXT,
  ).slice(0, 6)
})

const asText = computed(() =>
  [
    'Заголовки:',
    ...titles.value,
    '',
    'Дополнительные заголовки:',
    ...titles2.value,
    '',
    'Тексты:',
    ...texts.value,
  ].join('\n'),
)
</script>

<template>
  <div class="tool">
    <div class="tool__panel">
      <span class="tool__cap">Ваши слова</span>
      <div class="tool__field">
        <label class="tool__label" for="ag-p">Товар или услуга</label>
        <input id="ag-p" v-model="product" type="text" placeholder="Ремонт квартир" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="ag-w">Где <span class="tool__hint">как в тексте: «в Казани»</span></label>
        <input id="ag-w" v-model="where" type="text" placeholder="в Казани" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="ag-a">Преимущества <span class="tool__hint">по одному в строке, до 6</span></label>
        <textarea id="ag-a" v-model="advs" rows="5" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="ag-c">Призыв</label>
        <input id="ag-c" v-model="cta" type="text" placeholder="Вызовите замерщика" />
      </div>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <span class="tool__cap">Заголовки · до {{ TITLE }} символов</span>
      <ul class="tool__list">
        <li v-for="t in titles" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
        <li v-if="!titles.length"><span class="tool__note">Впишите товар или услугу</span></li>
      </ul>
      <span class="tool__cap">Дополнительный заголовок · до {{ TITLE2 }}</span>
      <ul class="tool__list">
        <li v-for="t in titles2" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
      </ul>
      <span class="tool__cap">Тексты · до {{ TEXT }}</span>
      <ul class="tool__list">
        <li v-for="t in texts" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
      </ul>
      <textarea ref="out" :value="asText" readonly class="sr-only" tabindex="-1" aria-hidden="true" />
      <div class="tool__row">
        <button type="button" class="btn btn--lime" :disabled="!titles.length" @click="copy(asText, out)">
          {{ copied ? 'Скопировано' : 'Скопировать всё' }}
        </button>
        <span class="tool__note">Варианты длиннее лимита не показываются.</span>
      </div>
    </div>
  </div>
</template>
