<template>
  <div class="adm-settings">
    <nav class="adm-settings__nav" aria-label="Разделы настроек">
      <RouterLink class="srow" :class="{ active: route.path === base }" :to="base">
        <span class="srow__t">Обзор</span>
        <span v-if="missing.length" class="sdot sdot--warn" v-tip.right="`Не настроено: ${missing.join(', ')}`"></span>
      </RouterLink>
      <template v-for="group in SETTINGS" :key="group.title">
        <h4 class="adm-side__h">{{ group.title }}</h4>
        <RouterLink
          v-for="item in group.items"
          :key="item.path"
          class="srow"
          :class="{ active: route.path === item.to }"
          :to="item.to"
        >
          <span class="srow__t">{{ item.label }}</span>
          <span
            v-if="item.status && summary"
            class="sdot"
            :class="item.status(summary) ? 'sdot--ok' : 'sdot--warn'"
            v-tip.right="item.status(summary) ? 'Работает' : 'Не настроено'"
          ></span>
        </RouterLink>
      </template>
    </nav>

    <div class="adm-settings__page">
      <RouterView :key="route.path" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { SETTINGS, SETTINGS_ITEM, missingSettings } from './adminNav'
import { useAdminSummary } from '../../composables/useAdminSummary'

const route = useRoute()
const { summary } = useAdminSummary()
const base = SETTINGS_ITEM.to
const missing = computed(() => missingSettings(summary.value))
</script>
