<template>
  <button class="btn btn--yandex" :class="{ 'btn--wide': wide }" type="button" :disabled="busy" @click="connect">
    {{ label }}
  </button>
</template>

<script setup>
import { ref } from 'vue'
import { errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'

const props = defineProps({
  purpose: { type: String, default: 'login' },
  label: { type: String, default: 'Продолжить с Яндексом' },
  wide: { type: Boolean, default: true },
})

const busy = ref(false)

async function connect() {
  busy.value = true
  try {
    const data = await webApi.yandexStart(props.purpose)
    if (!data?.url) throw new Error('url')
    window.location.href = data.url
  } catch (err) {
    pushToast(errorText(err), 'error')
    busy.value = false
  }
}
</script>
