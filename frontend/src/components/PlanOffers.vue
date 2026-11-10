<template>
  <div class="offers" :class="{ 'offers--compact': compact }">
    <article v-for="o in items" :key="o.id" class="offer" :class="{ 'is-current': o.current }">
      <header class="offer__head">
        <h3>{{ o.name }}</h3>
        <span v-if="o.current" class="chip chip--ok">ваш тариф</span>
        <span v-else-if="o.discount_pct" class="chip chip--info">−{{ num(o.discount_pct, 2) }}%</span>
      </header>
      <p class="offer__price">
        <strong>{{ o.price_with_discount ? rubles(o.price_with_discount) : 'бесплатно' }}</strong>
        <s v-if="o.discount_pct && o.price_rub">{{ rubles(o.price_rub) }}</s>
        <small>за {{ o.days }} {{ plural(o.days, 'день', 'дня', 'дней') }}</small>
      </p>
      <ul class="offer__facts">
        <li>
          <Check aria-hidden="true" />
          <span>{{ unitsText(o) }}</span>
        </li>
        <li>
          <Check aria-hidden="true" />
          <span>{{ projectsText(o) }}</span>
        </li>
        <li v-for="(label, key) in features" :key="key" :class="{ 'is-off': o.features?.[key] === false }">
          <Lock v-if="o.features?.[key] === false" aria-hidden="true" />
          <Check v-else aria-hidden="true" />
          <span>{{ label }}</span>
        </li>
      </ul>
      <button
        class="btn btn--wide"
        :class="o.current ? 'btn--ghost' : 'btn--accent'"
        type="button"
        @click="$emit('choose', o)"
      >
        {{ o.current ? 'Продлить' : 'Перейти' }}
      </button>
    </article>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Check, Lock } from '@lucide/vue'
import { num, plural, rubles } from '../utils/format'

const props = defineProps({
  offers: { type: Array, default: () => [] },
  features: { type: Object, default: () => ({}) },
  /** Только тарифы для перехода (без текущего) — окно «Лимит исчерпан». */
  upgradesOnly: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})
defineEmits(['choose'])

const items = computed(() => (props.upgradesOnly ? props.offers.filter((o) => !o.current) : props.offers))

// Единицы пользователю не показываем — только во сколько раз лимит больше текущего.
function unitsText(o) {
  if (o.units_unlimited) return 'ИИ, Вордстат и картинки без лимита'
  if (o.units_factor && o.units_factor !== 1 && !o.current) {
    return `Лимит ИИ ×${num(o.units_factor, 1)} к вашему`
  }
  return 'Лимит ИИ, Вордстата и картинок на период'
}

function projectsText(o) {
  if (o.projects_limit === null || o.projects_limit === undefined) return 'Проекты без ограничений'
  return `До ${o.projects_limit} ${plural(o.projects_limit, 'проекта', 'проектов', 'проектов')}`
}
</script>
