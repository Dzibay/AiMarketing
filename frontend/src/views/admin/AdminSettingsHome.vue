<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Что настроено</h2>
      <span v-if="summary" class="chip" :class="missing.length ? 'chip--warning' : 'chip--ok'">
        {{ missing.length ? `не настроено: ${missing.length}` : 'всё подключено' }}
      </span>
    </div>
    <p v-if="!summary" class="empty">Загружаем…</p>
    <p v-else-if="missing.length" class="hint">Не настроено: {{ missing.join(', ') }}.</p>
    <p v-else class="hint">Все сервисы подключены. Ниже — все разделы настроек.</p>
  </section>

  <section v-for="group in SETTINGS" :key="group.title" class="panel">
    <div class="panel__head">
      <h2>{{ group.title }}</h2>
    </div>
    <div class="adm-cards">
      <RouterLink v-for="item in group.items" :key="item.path" class="adm-card" :to="item.to">
        <span class="adm-card__t">
          {{ item.label }}
          <span v-if="item.status && summary" class="sdot" :class="item.status(summary) ? 'sdot--ok' : 'sdot--warn'"></span>
        </span>
        <small>{{ item.hint }}</small>
        <small v-if="item.status && summary" :class="item.status(summary) ? 'tone-ok' : 'tone-warn'">
          {{ item.status(summary) ? 'работает' : 'не настроено' }}
        </small>
      </RouterLink>
    </div>
  </section>

  <section class="panel">
    <div class="panel__head">
      <h2>На сервере</h2>
    </div>
    <p class="hint">Эти настройки задаются переменными окружения сервера — здесь только их состояние.</p>
    <div class="adm-cards">
      <div v-for="item in SERVER_SETTINGS" :key="item.key" class="adm-card adm-card--static">
        <span class="adm-card__t">
          {{ item.label }}
          <span v-if="summary" class="sdot" :class="item.status(summary) ? 'sdot--ok' : 'sdot--warn'"></span>
        </span>
        <small>{{ item.hint }}</small>
        <small v-if="summary" :class="item.status(summary) ? 'tone-ok' : 'tone-warn'">
          {{ item.status(summary) ? 'работает' : 'не настроено' }}
        </small>
      </div>
      <div class="adm-card adm-card--static">
        <span class="adm-card__t">Пробный тариф</span>
        <small>Что получает новый пользователь сразу после регистрации. Меняется в «Тарифах».</small>
        <small v-if="summary" :class="summary.trial_plan ? 'tone-ok' : 'tone-warn'">
          {{ summary.trial_plan || 'нет — без ограничений' }}
        </small>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { SERVER_SETTINGS, SETTINGS, missingSettings } from './adminNav'
import { useAdminSummary } from '../../composables/useAdminSummary'

const { summary } = useAdminSummary()
const missing = computed(() => missingSettings(summary.value))
</script>
