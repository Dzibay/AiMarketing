<template>
  <section v-if="plan && plan.allowed && plan.items.length" class="ccard protect-all">
    <div class="prow">
      <div class="prow__main">
        <b><ShieldCheck class="inline-icon" aria-hidden="true" /> Защита бюджета</b>
        <p>
          Без защиты {{ plan.items.length }} {{ plural(plan.items.length, 'проект', 'проекта', 'проектов') }}: сервис
          не сообщит, если деньги уходят без заявок.
        </p>
      </div>
      <div class="prow__acts">
        <button class="btn btn--accent" type="button" @click="open = true">Включить у всех клиентов</button>
      </div>
    </div>

    <Modal :open="open" title="Включить защиту бюджета" wide :locked="busy" @close="open = false">
      <p class="hint">
        В каждом проекте ниже включатся проверки. Остановку кампаний сервис сам не включает — её можно включить в
        настройках проекта.
      </p>
      <ul class="protect-all__list">
        <li v-for="w in plan.items" :key="w.workspace_id">
          <UiCheckbox v-model="picked[w.workspace_id]" class="check-row">
            <strong>{{ niceQuotes(w.name) }}</strong>
            <small v-for="c in w.checks" :key="c">{{ c }}</small>
            <small v-if="w.target_cpa_rub == null" class="tone-warn">
              Цена заявки не задана — порог в рублях. Задайте её в настройках проекта, и порог подстроится.
            </small>
          </UiCheckbox>
        </li>
      </ul>
      <p v-if="error" class="form-error">{{ error }}</p>
      <template #footer>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="open = false">Отмена</button>
        <button class="btn btn--accent" type="button" :disabled="busy || !chosen.length" @click="apply">
          {{ busy ? 'Включаем…' : `Включить в ${chosen.length} ${plural(chosen.length, 'проекте', 'проектах', 'проектах')}` }}
        </button>
      </template>
    </Modal>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ShieldCheck } from '@lucide/vue'
import { errorText, protectApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { niceQuotes, plural } from '../utils/format'
import Modal from './ui/Modal.vue'
import UiCheckbox from './ui/UiCheckbox.vue'

/**
 * «Включить у всех клиентов» на главной: проекты без «Защиты бюджета», что в каждом включится, и подтверждение.
 * Пропадает, когда защищены все проекты (или автопилота нет на тарифе).
 */
const plan = ref(null)
const open = ref(false)
const busy = ref(false)
const error = ref('')
const picked = reactive({})

const chosen = computed(() => (plan.value?.items || []).filter((w) => picked[w.workspace_id]).map((w) => w.workspace_id))

async function load() {
  try {
    plan.value = await protectApi.plan()
    for (const w of plan.value.items) if (picked[w.workspace_id] === undefined) picked[w.workspace_id] = true
  } catch {
    plan.value = null
  }
}

async function apply() {
  busy.value = true
  error.value = ''
  try {
    const res = await protectApi.applyAll(chosen.value)
    open.value = false
    pushToast(`Защита бюджета включена: ${res.projects} ${plural(res.projects, 'проект', 'проекта', 'проектов')}`, 'ok')
    await load()
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.protect-all {
  margin-top: 16px;
}
.protect-all__list {
  display: grid;
  gap: 12px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
</style>
