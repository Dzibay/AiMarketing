<script setup>
// Генератор UTM-меток с динамическими параметрами Директа. Фигурные скобки не кодируются —
// Директ сам подставит значения при переходе.
import { computed, ref } from 'vue'
import { useCopy } from './useCopy.js'

const PARAMS = [
  ['{campaign_id}', 'номер кампании'],
  ['{gbid}', 'номер группы'],
  ['{ad_id}', 'номер объявления'],
  ['{phrase_id}', 'номер фразы'],
  ['{keyword}', 'ключевая фраза'],
  ['{source_type}', 'поиск или сети'],
  ['{source}', 'площадка в сетях'],
  ['{device_type}', 'тип устройства'],
  ['{region_name}', 'регион'],
  ['{position_type}', 'тип блока на поиске'],
]

const url = ref('https://site.ru/')
const tags = ref([
  { key: 'utm_source', value: 'yandex' },
  { key: 'utm_medium', value: 'cpc' },
  { key: 'utm_campaign', value: '{campaign_id}' },
  { key: 'utm_content', value: '{ad_id}' },
  { key: 'utm_term', value: '{keyword}' },
])
const focused = ref(2)
const outFull = ref(null)
const outParams = ref(null)
const full = useCopy()
const params = useCopy()

// Кодируем всё, кроме фигурных скобок с именем параметра внутри.
const enc = (s) => s.split(/(\{[a-z_]+\})/).map((p) => (/^\{[a-z_]+\}$/.test(p) ? p : encodeURIComponent(p))).join('')

const query = computed(() =>
  tags.value
    .filter((t) => t.key.trim() && t.value.trim())
    .map((t) => `${encodeURIComponent(t.key.trim())}=${enc(t.value.trim())}`)
    .join('&'),
)

const result = computed(() => {
  const u = url.value.trim()
  if (!u || !query.value) return u
  const [base, hash] = u.split('#')
  const sep = base.includes('?') ? (base.endsWith('?') || base.endsWith('&') ? '' : '&') : '?'
  return `${base}${sep}${query.value}${hash !== undefined ? '#' + hash : ''}`
})

const badUrl = computed(() => url.value.trim() && !/^https?:\/\/[^\s/]+\.[^\s]+/.test(url.value.trim()))

function insert(p) {
  const t = tags.value[focused.value]
  if (t) t.value = p
}
</script>

<template>
  <div class="tool">
    <div class="tool__panel">
      <div class="tool__field">
        <label class="tool__label" for="utm-url">Ссылка на страницу</label>
        <input id="utm-url" v-model="url" type="url" inputmode="url" placeholder="https://site.ru/" />
        <span v-if="badUrl" class="tool__hint tool__count--over">Похоже, это не адрес сайта: нужен вид https://site.ru/…</span>
      </div>
      <div v-for="(t, i) in tags" :key="i" class="tool__row" style="flex-wrap: nowrap">
        <input v-model="t.key" type="text" :aria-label="'Метка ' + (i + 1)" style="flex: 0 0 40%" />
        <input v-model="t.value" type="text" :aria-label="'Значение ' + t.key" @focus="focused = i" />
      </div>
      <div class="tool__field">
        <span class="tool__label">Параметры Директа <span class="tool__hint">вставятся в выбранное поле</span></span>
        <div class="tool__row">
          <button v-for="p in PARAMS" :key="p[0]" type="button" class="chip" :title="p[1]" @click="insert(p[0])">{{ p[0] }}</button>
        </div>
      </div>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <span class="tool__cap">Ссылка с метками</span>
      <textarea ref="outFull" :value="result" readonly rows="5" spellcheck="false" aria-label="Ссылка с метками" />
      <div class="tool__row">
        <button type="button" class="btn btn--lime" :disabled="!result" @click="full.copy(result, outFull)">
          {{ full.copied.value ? 'Скопировано' : 'Скопировать ссылку' }}
        </button>
      </div>
      <span class="tool__cap">Только метки</span>
      <textarea ref="outParams" :value="query" readonly rows="3" spellcheck="false" aria-label="Только метки" />
      <div class="tool__row">
        <button type="button" class="btn btn--ghost" :disabled="!query" @click="params.copy(query, outParams)">
          {{ params.copied.value ? 'Скопировано' : 'Скопировать метки' }}
        </button>
      </div>
      <p class="tool__note">
        Метки без ссылки вставляются в поле «Параметры URL» в настройках кампании — тогда их не нужно дописывать в каждое
        объявление.
      </p>
    </div>
  </div>
</template>

<style scoped>
.chip {
  cursor: pointer;
  font-family: ui-monospace, monospace;
  font-size: 13px;
}
</style>
