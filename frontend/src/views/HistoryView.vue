<template>
  <ActionJournal />
</template>

<script setup>
import ActionJournal from '../components/ActionJournal.vue'
import { useHead } from '../utils/useHead'

useHead('История — Толк', true)
</script>
