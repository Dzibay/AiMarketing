<template>
  <p class="page-lead">
    Правила для всех проектов сразу или для выбранного: кампании, ключевые фразы и поисковые запросы. Правила одного
    сайта и эксперименты удобнее вести, выбрав проект в переключателе слева.
  </p>
  <RulesManager />
</template>

<script setup>
import RulesManager from '../components/RulesManager.vue'
import { useHead } from '../utils/useHead'

useHead('Автопилот — AiMarketing', true)
</script>
