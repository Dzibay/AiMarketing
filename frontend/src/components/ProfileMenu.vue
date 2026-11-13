<template>
  <div ref="root" class="profile" :class="{ 'is-open': open }">
    <button class="profile__toggle" type="button" :aria-expanded="open" aria-haspopup="menu" @click="open = !open">
      <span class="profile__avatar" aria-hidden="true">{{ initials }}</span>
      <span class="profile__text">
        <strong>{{ displayName }}</strong>
        <small v-if="user?.name && user?.email">{{ user.email }}</small>
      </span>
      <ChevronDown aria-hidden="true" />
    </button>

    <div v-if="open" class="profile__menu" role="menu">
      <p v-if="user">{{ user.name ? `${user.name} · ${user.email}` : user.email }}</p>
      <RouterLink class="menu-item" role="menuitem" to="/cabinet/settings" @click="open = false">
        <Settings aria-hidden="true" />
        Настройки
      </RouterLink>
      <RouterLink class="menu-item" role="menuitem" to="/cabinet/connections" @click="open = false">
        <PlugZap aria-hidden="true" />
        Подключения
      </RouterLink>
      <RouterLink class="menu-item" role="menuitem" to="/cabinet/plan" @click="open = false">
        <CreditCard aria-hidden="true" />
        Тариф и лимит<small v-if="planName" class="menu-item__note">{{ planName }}</small>
      </RouterLink>
      <hr />
      <button
        class="menu-item menu-item--switch"
        type="button"
        role="menuitemcheckbox"
        :aria-checked="expert"
        v-tip="'Добавляет «Автопилот», отчёты по площадкам и объявлениям, списки минус-фраз и папки клиентов'"
        @click="setExpert(!expert)"
      >
        <SlidersHorizontal aria-hidden="true" />
        Экспертный режим
        <span class="menu-switch" :class="{ 'is-on': expert }" aria-hidden="true"></span>
      </button>
      <hr />
      <button class="menu-item is-danger" type="button" role="menuitem" @click="$emit('logout')">
        <LogOut aria-hidden="true" />
        Выйти
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown, CreditCard, LogOut, PlugZap, Settings, SlidersHorizontal } from '@lucide/vue'
import { useExpert } from '../composables/useExpert'
import { useProfile } from '../composables/useProfile'

defineEmits(['logout'])

const { user, subscription } = useProfile()
const { expert, setExpert } = useExpert()
const open = ref(false)

const planName = computed(() => subscription.value?.plan?.name || '')
const root = ref(null)

const displayName = computed(() => user.value?.name || user.value?.email || 'Профиль')

const initials = computed(() => {
  const source = (user.value?.name || user.value?.email || '').trim()
  if (!source) return '·'
  const words = source.split(/[^\p{L}\p{N}]+/u).filter(Boolean)
  if (!words.length) return '·'
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)
  return letters.toUpperCase()
})

function onDocClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}

function onKey(event) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>
