<template>
  <div class="ui-skeleton" :class="`ui-skeleton--${variant}`" role="status" aria-busy="true" :aria-label="label">
    <!-- Плитки с цифрами: подпись, крупное число, строка пояснения. -->
    <template v-if="variant === 'tiles'">
      <div v-for="n in count" :key="n" class="ui-skeleton__tile">
        <span class="ui-skel" style="width: 46%" />
        <span class="ui-skel ui-skeleton__big" :style="{ width: `${38 + ((n * 13) % 24)}%` }" />
        <span class="ui-skel ui-skeleton__sub" style="width: 70%" />
      </div>
    </template>

    <!-- Таблица: строки с названием слева и цифрами справа. -->
    <div v-else-if="variant === 'table'" class="ui-skeleton__card">
      <div v-for="n in count" :key="n" class="ui-skeleton__trow">
        <span class="ui-skel" :style="{ width: `${44 + ((n * 17) % 30)}%` }" />
        <span class="ui-skel" style="width: 64px" />
        <span class="ui-skel" style="width: 56px" />
      </div>
    </div>

    <!-- Текст: несколько строк разной длины, без карточки. -->
    <template v-else-if="variant === 'text'">
      <span v-for="n in count" :key="n" class="ui-skel" :style="{ width: n === count ? '58%' : `${92 - ((n * 9) % 20)}%` }" />
    </template>

    <!-- Список карточек-строк (по умолчанию): заголовок, подпись и кнопка справа. -->
    <div v-else class="ui-skeleton__card">
      <div v-for="n in count" :key="n" class="ui-skeleton__row">
        <div class="ui-skeleton__main">
          <span class="ui-skel ui-skeleton__sub" style="width: 28%" />
          <span class="ui-skel ui-skeleton__title" :style="{ width: `${48 + ((n * 19) % 34)}%` }" />
          <span class="ui-skel ui-skeleton__sub" :style="{ width: `${62 - ((n * 11) % 20)}%` }" />
        </div>
        <span v-if="actions" class="ui-skel ui-skeleton__btn" />
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * Заглушка по форме содержимого вместо «Загружаем…»: пользователь сразу видит, что здесь будет.
 * variant: rows — список строк в карточке, table — таблица, tiles — плитки с цифрами, text — абзац.
 * label — что грузится, для экранного диктора.
 */
defineProps({
  variant: { type: String, default: 'rows' },
  count: { type: Number, default: 3 },
  actions: { type: Boolean, default: false },
  label: { type: String, default: 'Загружаем' },
})
</script>
