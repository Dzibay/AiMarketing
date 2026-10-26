<template>
  <button
    class="btn"
    :class="[variant === 'yandex' ? 'btn--yandex' : `btn--${variant}`, { 'btn--wide': wide }]"
    type="button"
    :disabled="busy"
    @click="connect"
  >
    {{ label }}
  </button>
</template>

<script setup>
import { ref } from 'vue'
import { errorText, webApi } from '../api/web'
import { setOAuthReturn } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'

const props = defineProps({
  purpose: { type: String, default: 'login' },
  // "" — Метрика и Директ, "metrika" / "direct" — одна роль, "sandbox" — песочница Директа.
  mode: { type: String, default: '' },
  label: { type: String, default: 'Продолжить с Яндексом' },
  wide: { type: Boolean, default: true },
  variant: { type: String, default: 'yandex' },
  // Куда вернуться после подключения (по умолчанию — обзор кабинета).
  returnTo: { type: String, default: '' },
})

const busy = ref(false)

async function connect() {
  busy.value = true
  try {
    const data = await webApi.yandexStart(props.purpose, props.mode)
    if (!data?.url) throw new Error('url')
    setOAuthReturn(props.returnTo)
    window.location.href = data.url
  } catch (err) {
    pushToast(errorText(err), 'error')
    busy.value = false
  }
}
</script>
