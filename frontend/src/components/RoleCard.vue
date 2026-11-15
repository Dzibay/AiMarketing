<template>
  <!-- «Как вам удобнее?» — один раз на главной у тех, кто не выбрал роль в мастере. Не модальное окно:
       выбрали или нажали «Потом» — карточка больше не появляется (выбор есть и в «Настройках → Интерфейс»). -->
  <section v-if="show" class="panel role-card" aria-labelledby="role-card-title">
    <div class="role-card__head">
      <h2 id="role-card-title">Как вам удобнее?</h2>
      <button class="btn btn--ghost btn--sm" type="button" @click="later">Потом</button>
    </div>
    <p class="muted">От ответа зависят режим, быстрые вопросы и то, как ИИ объясняет. Поменять можно в настройках.</p>
    <RoleChoice @chosen="done = true" />
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import RoleChoice from './RoleChoice.vue'
import { saveUi, useProfile } from '../composables/useProfile'
import { useRole } from '../composables/useRole'

const HINT = 'role-card'

const { user } = useProfile()
const { chosen } = useRole()
const done = ref(false)

const show = computed(
  () => !!user.value && !done.value && !chosen.value && !(user.value.ui?.hints_seen || []).includes(HINT),
)

function later() {
  done.value = true
  saveUi({ hints_seen: [HINT] }).catch(() => {})
}
</script>

<style scoped>
.role-card {
  display: grid;
  gap: 10px;
}

.role-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.role-card h2 {
  margin: 0;
}

.role-card p {
  margin: 0 0 4px;
}
</style>
