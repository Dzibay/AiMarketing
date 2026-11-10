<template>
  <p v-if="locked" class="notice notice--warn feature-lock">
    <Lock aria-hidden="true" />
    <span>
      <slot>Эта функция не входит в ваш тариф.</slot>
      <RouterLink to="/cabinet/plan">Посмотреть тарифы</RouterLink>
    </span>
  </p>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Lock } from '@lucide/vue'
import { featureLocked, useProfile } from '../composables/useProfile'

/** Раздел виден всегда, но с замком: функция тарифа выключена — объясняем и ведём к тарифам. */
const props = defineProps({ feature: { type: String, required: true } })

const { subscription } = useProfile()
const locked = computed(() => featureLocked(subscription.value, props.feature))
</script>
