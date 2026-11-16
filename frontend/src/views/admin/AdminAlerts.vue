<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Уведомления в Telegram</h2>
      <span v-if="data" class="chip" :class="data.configured ? 'chip--ok' : 'chip--warning'">
        {{ data.configured ? 'группа подключена' : 'группа не настроена' }}
      </span>
    </div>
    <p class="hint">
      Уведомления приходят в Telegram-группу поддержки, в две темы: «Оплаты» и «Сбои». Темы создаются сами при первом
      сообщении. Одно и то же предупреждение о сбое повторяется не чаще раза в час. Тестовые платежи не присылаются.
    </p>
    <p v-if="data && !data.configured" class="hint tone-warn">
      {{ data.bot ? 'Бот есть, но группа поддержки не указана' : 'Бот Telegram не подключён' }} — задайте
      TELEGRAM_BOT_TOKEN и TELEGRAM_SUPPORT_CHAT_ID на сервере. До этого уведомления никуда не уходят.
    </p>

    <p v-if="error && !data" class="empty">{{ error }}</p>
    <p v-else-if="!data" class="empty">Загружаем…</p>
    <ul v-else class="adm-alerts">
      <li v-for="k in data.kinds" :key="k.key">
        <UiSwitch :model-value="k.enabled" :disabled="busy === k.key" @update:model-value="toggle(k, $event)">
          <b>{{ k.label }}</b>
        </UiSwitch>
        <small class="cell-note">Тема «{{ k.topic }}». {{ k.hint }}</small>
        <button
          class="btn btn--ghost btn--xs"
          type="button"
          :disabled="!data.configured || busy === `test-${k.key}`"
          v-tip="data.configured ? 'Прислать пример в группу' : 'Сначала подключите группу поддержки'"
          @click="test(k)"
        >
          Проверить
        </button>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import { useAdminLoad } from './useAdminLoad'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'

/** Настройки → Уведомления (docs/admin-plan.md, этап 7): какие оповещения слать в Telegram, тестовая отправка. */
const { data, error } = useAdminLoad(adminApi.alerts)
const busy = ref('')

async function toggle(k, on) {
  busy.value = k.key
  try {
    data.value = await adminApi.saveAlerts({ [k.key]: on })
    pushToast(on ? `«${k.label}» — включено` : `«${k.label}» — выключено`)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
  }
}

async function test(k) {
  busy.value = `test-${k.key}`
  try {
    const res = await adminApi.testAlert(k.key)
    pushToast(res.ok ? 'Пример отправлен — проверьте группу' : 'Telegram не принял сообщение', res.ok ? undefined : 'error')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.adm-alerts {
  display: grid;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.adm-alerts li {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 4px 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.adm-alerts li:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.adm-alerts small {
  grid-column: 1;
}

.adm-alerts button {
  grid-column: 2;
  grid-row: 1 / span 2;
}
</style>
