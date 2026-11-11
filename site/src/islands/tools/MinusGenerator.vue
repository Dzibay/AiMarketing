<script setup>
// Генератор базовых минус-слов: темы → список без повторов в формате Директа. Всё считается в браузере.
import { computed, ref } from 'vue'
import { useCopy } from './useCopy.js'

const GROUPS = [
  { id: 'free', title: 'Бесплатное', hint: 'бесплатно, скачать, торрент', words: ['бесплатно', 'бесплатный', 'даром', 'халява', 'скачать', 'торрент', 'кряк', 'взлом'] },
  { id: 'job', title: 'Работа и вакансии', hint: 'вакансия, зарплата, резюме', words: ['вакансия', 'работа', 'зарплата', 'резюме', 'требуется', 'подработка', 'устроиться', 'стажировка'] },
  { id: 'study', title: 'Учёба', hint: 'реферат, курсовая, ГДЗ', words: ['реферат', 'курсовая', 'диплом', 'доклад', 'гдз', 'учебник', 'лекция', 'презентация', 'конспект'] },
  { id: 'diy', title: 'Своими руками', hint: 'самостоятельно, чертёж, схема', words: ['своими руками', 'самостоятельно', 'самому', 'чертеж', 'схема', 'инструкция'] },
  { id: 'used', title: 'Б/у и с рук', hint: 'бу, авито, подержанный', words: ['бу', 'подержанный', 'авито', 'юла', 'барахолка'] },
  { id: 'info', title: 'Справочные запросы', hint: 'определение, история, википедия', words: ['определение', 'значение', 'история', 'википедия'] },
  { id: 'reviews', title: 'Жалобы и форумы', hint: 'форум, развод, жалоба', words: ['форум', 'жалоба', 'развод', 'обман', 'мошенники', 'суд'] },
  { id: 'media', title: 'Картинки и видео', hint: 'фото, обои, смотреть онлайн', words: ['фото', 'картинки', 'обои', 'видео', 'смотреть', 'онлайн', 'youtube', 'ютуб'] },
]

const picked = ref(['free', 'job', 'study'])
const own = ref('')
const format = ref('lines')
const out = ref(null)
const { copied, copy } = useCopy()

const norm = (w) => w.trim().toLowerCase().replace(/ё/g, 'е').replace(/^[-!+]+/, '').replace(/\s+/g, ' ')

const words = computed(() => {
  const all = [
    ...GROUPS.filter((g) => picked.value.includes(g.id)).flatMap((g) => g.words),
    ...own.value.split(/[\n,;]+/),
  ]
  return [...new Set(all.map(norm).filter(Boolean))]
})

const text = computed(() =>
  format.value === 'lines' ? words.value.join('\n') : words.value.map((w) => (w.includes(' ') ? `-"${w}"` : `-${w}`)).join(' '),
)
</script>

<template>
  <div class="tool">
    <div class="tool__panel">
      <span class="tool__cap">Темы, которые вам не нужны</span>
      <div class="tool__checks">
        <label v-for="g in GROUPS" :key="g.id" class="tool__check">
          <input v-model="picked" type="checkbox" :value="g.id" />
          <span>{{ g.title }}<small>{{ g.hint }}</small></span>
        </label>
      </div>
      <div class="tool__field">
        <label class="tool__label" for="mg-own">Свои слова</label>
        <textarea id="mg-own" v-model="own" rows="3" placeholder="Через запятую или с новой строки: опт, оптом, дешево" />
        <span class="tool__hint">Не добавляйте слово, если продаёте именно это: «бу» не нужен магазину подержанной техники.</span>
      </div>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <span class="tool__cap">Минус-слова · {{ words.length }}</span>
      <div class="tool__row" role="radiogroup" aria-label="Формат">
        <label class="tool__check"><input v-model="format" type="radio" value="lines" /> По строке — для списка в настройках кампании</label>
        <label class="tool__check"><input v-model="format" type="radio" value="inline" /> Через минус — дописать к фразе</label>
      </div>
      <textarea ref="out" :value="text" readonly rows="10" aria-label="Готовый список" />
      <div class="tool__row">
        <button type="button" class="btn btn--lime" :disabled="!words.length" @click="copy(text, out)">
          {{ copied ? 'Скопировано' : 'Скопировать' }}
        </button>
        <span v-if="format === 'inline' && phrases" class="tool__note">
          Не вошли фразы из нескольких слов ({{ phrases }}): к ключевой фразе дописываются только отдельные слова, фразы — в список кампании.
        </span>
      </div>
    </div>
  </div>
</template>
