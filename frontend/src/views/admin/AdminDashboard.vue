<template>
  <div v-if="!summary" class="panel"><p class="empty">Загружаем…</p></div>
  <template v-else>
    <div class="stats-grid">
      <RouterLink class="stat-card adm-stat" :to="`${ADMIN_BASE}/users`">
        <small>Пользователей</small>
        <strong>{{ num(summary.users_total) }}</strong>
      </RouterLink>
      <RouterLink class="stat-card adm-stat" :to="`${ADMIN_BASE}/payments`">
        <small>Оплаты за месяц</small>
        <strong>{{ rubles(summary.payments?.month_rub || 0) }}</strong>
        <span class="cell-note">
          {{ num(summary.payments?.month_count || 0) }}
          {{ plural(summary.payments?.month_count || 0, 'оплата', 'оплаты', 'оплат') }}<template
            v-if="summary.payments?.no_receipt"> · без чека: {{ num(summary.payments.no_receipt) }}</template>
        </span>
      </RouterLink>
      <RouterLink class="stat-card adm-stat" :to="`${ADMIN_BASE}/settings/plans`">
        <small>Пробный тариф</small>
        <strong :class="summary.trial_plan ? '' : 'tone-warn'">{{ summary.trial_plan || 'нет — без ограничений' }}</strong>
      </RouterLink>
    </div>

    <section class="panel">
      <div class="panel__head">
        <h2>Состояние</h2>
      </div>
      <div class="adm-health">
        <RouterLink v-for="s in health" :key="s.key" class="adm-health__item" :to="s.to">
          <span class="sdot" :class="s.ok ? 'sdot--ok' : 'sdot--warn'"></span>
          <span>{{ s.label }}</span>
          <small :class="s.ok ? 'tone-ok' : 'tone-warn'">{{ s.ok ? s.okText || 'работает' : s.badText || 'не подключено' }}</small>
        </RouterLink>
      </div>
    </section>
  </template>
</template>

<script setup>
import { computed } from 'vue'
import { ADMIN_BASE } from './adminNav'
import { useAdminSummary } from '../../composables/useAdminSummary'
import { num, plural, rubles } from '../../utils/format'

const { summary } = useAdminSummary()
const S = `${ADMIN_BASE}/settings`

const health = computed(() => {
  const s = summary.value || {}
  return [
    { key: 'ai', label: 'ИИ', ok: s.ai_configured, to: `${S}/ai` },
    { key: 'wordstat', label: 'Вордстат', ok: s.wordstat_configured, to: `${S}/wordstat` },
    { key: 'images', label: 'Картинки', ok: s.images_configured, to: `${S}/images` },
    { key: 'payments', label: 'ЮKassa', ok: s.payments_ready, to: `${S}/payments` },
    { key: 'oauth', label: 'Вход через Яндекс', ok: s.yandex_oauth, to: S },
    {
      key: 'email',
      label: 'Подтверждение почты',
      ok: s.email_check,
      okText: 'включено',
      badText: 'выкл.: нет SMTP',
      to: S,
    },
  ]
})
</script>
