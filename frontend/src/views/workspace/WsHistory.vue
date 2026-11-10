<template>
  <ActionJournal :workspace-id="ws.id" />
</template>

<script setup>
import ActionJournal from '../../components/ActionJournal.vue'
import { useWs } from './context'

const { ws } = useWs()
</script>
