<template>
  <div class="ribbon" :class="{ 'ribbon--bleed': bleed, 'has-before': strip.before, 'has-after': strip.after }">
    <div ref="track" class="ribbon__track" v-bind="$attrs"><slot /></div>
    <!-- Точки — только подсказка «листается»: диктору и клавиатуре лента доступна и без них. -->
    <div v-if="strip.pages > 1" class="ribbon__dots" aria-hidden="true">
      <i v-for="n in strip.pages" :key="n" :class="{ 'is-on': n - 1 === strip.page }"></i>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { revealInStrip, useScrollStrip } from '../../composables/useScrollStrip'

/**
 * Ряд плиток, который на телефоне становится лентой: две плитки целиком и край третьей, остальные листаются
 * вбок, под лентой — точки. На широком экране обёртки как будто нет: плитки стоят сеткой, как задано классом.
 * Класс и атрибуты уходят на сам ряд: <UiRibbon class="ktiles ktiles--six">…плитки…</UiRibbon>.
 * bleed — лента на странице, а не в карточке: уходит под край экрана. Без него скрытый край затеняется.
 */
defineOptions({ inheritAttrs: false })
defineProps({
  bleed: { type: Boolean, default: false },
})

const track = ref(null)
const { strip } = useScrollStrip(track)

// Выбранная плитка (метрика графика) может стоять за краем — показываем её сразу.
onMounted(() => revealInStrip(track.value, track.value?.querySelector('[aria-pressed="true"]'), 0))
</script>
