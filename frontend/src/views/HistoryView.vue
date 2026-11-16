<template>
  <ActionJournal />
</template>

<script setup>
import { usePageContext } from '../composables/usePageContext'
import ActionJournal from '../components/ActionJournal.vue'
import { useHead } from '../utils/useHead'

useHead('История — Толк', true)

usePageContext(() => ({ view: 'history' }))
</script>
