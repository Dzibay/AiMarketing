<template>
  <RulesManager :workspace="ws" />
  <p class="hint autopilot-note">
    Правила для всех проектов сразу — в разделе «Автопилот» при фильтре
    <RouterLink to="/cabinet/autopilot">«Все проекты»</RouterLink>. Что правила уже сделали — в
    <RouterLink :to="`/w/${ws.id}/history`">Истории</RouterLink>.
  </p>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import RulesManager from '../../components/RulesManager.vue'
import { useWs } from './context'

const { ws } = useWs()
</script>
