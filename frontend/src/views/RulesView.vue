<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Правила</h1>
        <p>
          Автоматизация по всем пространствам: кампании, ключевые фразы и поисковые запросы. Правила конкретного сайта
          удобнее вести в его пространстве → «Действия».
        </p>
      </div>
    </div>
    <RulesManager />
  </AppShell>
</template>

<script setup>
import AppShell from '../components/AppShell.vue'
import RulesManager from '../components/RulesManager.vue'
import { useHead } from '../utils/useHead'

useHead('Правила — AiMarketing', true)
</script>
