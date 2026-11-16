<template>
  <ActionJournal :workspace-id="ws.id" />
</template>

<script setup>
import { usePageContext } from '../../composables/usePageContext'
import ActionJournal from '../../components/ActionJournal.vue'
import { useWs } from './context'

const { ws } = useWs()

usePageContext(() => ({ view: 'history' }))
</script>
