<template>
  <RulesManager :workspace="ws" />
  <p class="hint autopilot-note">
    Правила для всех сайтов сразу — в «Автопилоте» при фильтре
    <RouterLink to="/cabinet/autopilot">«Все проекты»</RouterLink>.
  </p>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import RulesManager from '../../components/RulesManager.vue'
import { useWs } from './context'

const { ws } = useWs()
</script>
