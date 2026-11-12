<template>
  <RulesManager :workspace="ws" />
</template>

<script setup>
import RulesManager from '../../components/RulesManager.vue'
import { useWs } from './context'

const { ws } = useWs()
</script>
