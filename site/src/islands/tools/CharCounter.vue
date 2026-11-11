<script setup>
// Счётчик символов объявления: до 7 заголовков по 56, дополнительный заголовок 30, до 3 текстов по 81.
// Считаем все символы, включая пробелы и знаки, — так же, как проверяет Толк перед отправкой в Директ.
import { computed, ref } from 'vue'

const LIMITS = { title: 56, title2: 30, text: 81 }

const titles = ref(['Ремонт квартир в Казани под ключ', 'Ремонт квартир — смета за 1 день', ''])
const title2 = ref('Гарантия 3 года')
const texts = ref(['Своя бригада, фиксированная цена в договоре. Вызовите замерщика бесплатно!'])

const add = (list, max) => list.value.length < max && list.value.push('')
const drop = (list, i) => list.value.length > 1 && list.value.splice(i, 1)

const len = (s) => [...s].length
const over = computed(
  () =>
    titles.value.filter((t) => len(t) > LIMITS.title).length +
    (len(title2.value) > LIMITS.title2 ? 1 : 0) +
    texts.value.filter((t) => len(t) > LIMITS.text).length,
)
</script>

<template>
  <div class="tool tool--stack">
    <div class="tool__panel">
      <span class="tool__cap">Заголовки · до 7, каждый до {{ LIMITS.title }} символов</span>
      <div v-for="(t, i) in titles" :key="'t' + i" class="tool__field">
        <label class="tool__label" :for="'cc-t' + i">
          Заголовок {{ i + 1 }}
          <span class="tool__count" :class="{ 'tool__count--over': len(t) > LIMITS.title }">{{ len(t) }} / {{ LIMITS.title }}</span>
        </label>
        <div class="tool__row" style="flex-wrap: nowrap">
          <input :id="'cc-t' + i" v-model="titles[i]" type="text" />
          <button v-if="titles.length > 1" type="button" class="btn btn--ghost btn--sm" :aria-label="'Убрать заголовок ' + (i + 1)" @click="drop(titles, i)">×</button>
        </div>
      </div>
      <div><button v-if="titles.length < 7" type="button" class="btn btn--ghost btn--sm" @click="add(titles, 7)">+ Заголовок</button></div>

      <div class="tool__field">
        <label class="tool__label" for="cc-t2">
          Дополнительный заголовок
          <span class="tool__count" :class="{ 'tool__count--over': len(title2) > LIMITS.title2 }">{{ len(title2) }} / {{ LIMITS.title2 }}</span>
        </label>
        <input id="cc-t2" v-model="title2" type="text" />
      </div>

      <span class="tool__cap">Тексты · до 3, каждый до {{ LIMITS.text }} символов</span>
      <div v-for="(t, i) in texts" :key="'x' + i" class="tool__field">
        <label class="tool__label" :for="'cc-x' + i">
          Текст {{ i + 1 }}
          <span class="tool__count" :class="{ 'tool__count--over': len(t) > LIMITS.text }">{{ len(t) }} / {{ LIMITS.text }}</span>
        </label>
        <div class="tool__row" style="flex-wrap: nowrap">
          <textarea :id="'cc-x' + i" v-model="texts[i]" rows="2" style="min-height: 0" />
          <button v-if="texts.length > 1" type="button" class="btn btn--ghost btn--sm" :aria-label="'Убрать текст ' + (i + 1)" @click="drop(texts, i)">×</button>
        </div>
      </div>
      <div><button v-if="texts.length < 3" type="button" class="btn btn--ghost btn--sm" @click="add(texts, 3)">+ Текст</button></div>

      <p class="tool__note" aria-live="polite">
        {{ over ? `Длиннее лимита: ${over} — сократите.` : 'Все строки в пределах лимитов.' }}
      </p>
    </div>
  </div>
</template>
