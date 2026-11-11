<script setup>
// Генератор объявлений по шаблонам: товар + где + преимущества → заголовки и тексты в лимитах Директа.
// Шаблоны — перестановки ваших слов в браузере; «Подобрать с ИИ» — варианты от модели (useAi, лимиты в админке).
// ИИ-версия на данных кабинета — в самом Толке.
import { computed, ref } from 'vue'
import { useAi } from './useAi.js'
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
const ai = useAi('ads')
const mode = ref('tpl')

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

// Что показываем справа: шаблоны или последний ответ ИИ.
const shown = computed(() =>
  mode.value === 'ai' && ai.result.value
    ? ai.result.value
    : { titles: titles.value, titles2: titles2.value, texts: texts.value },
)

const asText = computed(() =>
  [
    'Заголовки:',
    ...shown.value.titles,
    '',
    'Дополнительные заголовки:',
    ...shown.value.titles2,
    '',
    'Тексты:',
    ...shown.value.texts,
  ].join('\n'),
)

async function runAi() {
  const data = await ai.run({ product: product.value, where: where.value, pros: list.value.join('; '), cta: cta.value })
  if (data) mode.value = 'ai'
}
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
      <div v-if="ai.available.value" class="tool__row">
        <button type="button" class="btn btn--ghost" :disabled="ai.busy.value || !clean(product)" @click="runAi">
          {{ ai.busy.value ? 'Подбираем…' : 'Подобрать с ИИ' }}
        </button>
        <span class="tool__note">Перепишет ваши слова по-своему — только по фактам из полей.</span>
      </div>
      <p v-if="ai.error.value" class="tool__note" role="alert">{{ ai.error.value }}</p>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <div v-if="ai.result.value" class="tool__row" role="radiogroup" aria-label="Варианты">
        <label class="tool__check"><input v-model="mode" type="radio" value="tpl" /> Шаблоны</label>
        <label class="tool__check"><input v-model="mode" type="radio" value="ai" /> От ИИ</label>
      </div>
      <span class="tool__cap">Заголовки · до {{ TITLE }} символов</span>
      <ul class="tool__list">
        <li v-for="t in shown.titles" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
        <li v-if="!shown.titles.length"><span class="tool__note">Впишите товар или услугу</span></li>
      </ul>
      <span class="tool__cap">Дополнительный заголовок · до {{ TITLE2 }}</span>
      <ul class="tool__list">
        <li v-for="t in shown.titles2" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
      </ul>
      <span class="tool__cap">Тексты · до {{ TEXT }}</span>
      <ul class="tool__list">
        <li v-for="t in shown.texts" :key="t"><span>{{ t }}</span><span class="tool__count">{{ t.length }}</span></li>
      </ul>
      <textarea ref="out" :value="asText" readonly class="sr-only" tabindex="-1" aria-hidden="true" />
      <div class="tool__row">
        <button type="button" class="btn btn--lime" :disabled="!shown.titles.length" @click="copy(asText, out)">
          {{ copied ? 'Скопировано' : 'Скопировать всё' }}
        </button>
        <span class="tool__note">Варианты длиннее лимита не показываются.</span>
      </div>
    </div>
  </div>
</template>
