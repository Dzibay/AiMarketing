<template>
  <section class="connect-offers">
    <header class="connect-offers__head">
      <h2>Подключите кабинеты Яндекса</h2>
      <p>
        Одна кнопка — переход на страницу Яндекса, как при входе через Google.
        После согласия Метрика и Директ появятся в кабинете.
      </p>
    </header>

    <div class="connect-grid">
      <article class="connect-card" :class="{ 'is-linked': hasMetrika }">
        <div class="connect-card__top">
          <span class="connect-card__badge">Метрика</span>
          <span v-if="hasMetrika" class="chip">подключено · {{ metrikaCount }}</span>
        </div>
        <h3>Яндекс Метрика</h3>
        <p>Счётчики, цели и статистика для связки с рекламой.</p>
        <YandexConnectButton
          v-if="!hasConnection"
          purpose="connect"
          label="Подключить Метрику"
          :wide="true"
        />
        <p v-else-if="hasMetrika" class="connect-card__ok">Счётчики уже в кабинете</p>
        <YandexConnectButton
          v-else
          purpose="connect"
          label="Обновить доступ"
          :wide="true"
        />
      </article>

      <article class="connect-card" :class="{ 'is-linked': hasDirect }">
        <div class="connect-card__top">
          <span class="connect-card__badge connect-card__badge--direct">Директ</span>
          <span v-if="hasDirect" class="chip">подключено · {{ directCount }}</span>
        </div>
        <h3>Яндекс Директ</h3>
        <p>Рекламные кабинеты и кампании для автоматизации.</p>
        <YandexConnectButton
          v-if="!hasConnection"
          purpose="connect"
          label="Подключить Директ"
          :wide="true"
        />
        <p v-else-if="hasDirect" class="connect-card__ok">Кабинеты уже в кабинете</p>
        <YandexConnectButton
          v-else
          purpose="connect"
          label="Обновить доступ"
          :wide="true"
        />
      </article>
    </div>

    <div v-if="!hasConnection" class="connect-offers__one">
      <YandexConnectButton
        purpose="connect"
        label="Подключить Метрику и Директ через Яндекс"
        :wide="false"
      />
      <p>Один раз подтвердите доступ на сайте Яндекса — оба сервиса подключатся вместе.</p>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import YandexConnectButton from './YandexConnectButton.vue'

const props = defineProps({
  connections: { type: Array, default: () => [] },
  metrika: { type: Array, default: () => [] },
  direct: { type: Array, default: () => [] },
})

const hasConnection = computed(() => props.connections.length > 0)
const metrikaCount = computed(() => props.metrika.length)
const directCount = computed(() => props.direct.length)
const hasMetrika = computed(() => metrikaCount.value > 0)
const hasDirect = computed(() => directCount.value > 0)
</script>
