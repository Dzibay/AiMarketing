<template>
  <RulesManager />
  <p class="hint autopilot-note">
    Правила одного сайта и эксперименты — выберите сайт в переключателе слева.
  </p>
</template>

<script setup>
import RulesManager from '../components/RulesManager.vue'
import { useHead } from '../utils/useHead'

useHead('Автопилот — Толк', true)
</script>
