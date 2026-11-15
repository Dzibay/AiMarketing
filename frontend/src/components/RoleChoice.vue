<template>
  <!-- Две большие кнопки «Кто вы?»: мастер первого входа, карточка на главной и «Настройки → Интерфейс». -->
  <div class="role-choice" role="group" aria-label="Кто вы?">
    <button
      v-for="(r, key) in ROLES"
      :key="key"
      class="role-choice__opt"
      :class="{ 'is-on': chosen && role === key }"
      type="button"
      :aria-pressed="chosen && role === key"
      :disabled="busy"
      @click="pick(key)"
    >
      <component :is="ICONS[key]" class="role-choice__ico" aria-hidden="true" />
      <b>{{ r.title }}</b>
      <span>{{ r.text }}</span>
    </button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { BriefcaseBusiness, ChartNoAxesCombined } from '@lucide/vue'
import { ROLES, useRole } from '../composables/useRole'

const emit = defineEmits(['chosen'])

const ICONS = { owner: BriefcaseBusiness, pro: ChartNoAxesCombined }
const { role, chosen, setRole } = useRole()
const busy = ref(false)

async function pick(key) {
  busy.value = true
  try {
    await setRole(key)
    emit('chosen', key)
  } catch {
    // Сообщение уже показал setRole.
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.role-choice {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.role-choice__opt {
  display: grid;
  align-content: start;
  gap: 6px;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.role-choice__opt:hover:not(:disabled) {
  border-color: var(--ink);
}

.role-choice__opt.is-on {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}

.role-choice__opt:disabled {
  cursor: progress;
  opacity: 0.7;
}

.role-choice__ico {
  width: 22px;
  height: 22px;
  color: var(--muted);
}

.role-choice__opt b {
  font-size: 16px;
}

.role-choice__opt span {
  color: var(--muted);
  font-size: 14px;
  line-height: 1.45;
}

@media (max-width: 640px) {
  .role-choice {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
