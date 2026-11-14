<template>
  <div ref="root" class="profile" :class="{ 'is-open': open }">
    <button
      class="profile__toggle"
      type="button"
      :aria-expanded="open"
      :aria-haspopup="phone ? 'dialog' : 'menu'"
      @click="open = !open"
    >
      <span class="profile__avatar" aria-hidden="true">{{ initials }}</span>
      <span class="profile__text">
        <strong>{{ displayName }}</strong>
        <small v-if="user?.name && user?.email">{{ user.email }}</small>
      </span>
      <ChevronDown aria-hidden="true" />
    </button>

    <!-- На телефоне меню — шторка снизу, на широком экране — окно под аватаром. -->
    <component
      :is="phone ? UiSheet : 'div'"
      v-if="phone || open"
      v-bind="phone ? { open, title: 'Профиль' } : { class: 'profile__menu' }"
      @close="open = false"
    >
      <div class="profile__items" role="menu">
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
        <button class="menu-item is-danger" type="button" role="menuitem" @click="signOut">
          <LogOut aria-hidden="true" />
          Выйти
        </button>
      </div>
    </component>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown, CreditCard, LogOut, PlugZap, Settings, SlidersHorizontal } from '@lucide/vue'
import UiSheet from './ui/UiSheet.vue'
import { useExpert } from '../composables/useExpert'
import { useProfile } from '../composables/useProfile'
import { useViewport } from '../composables/useViewport'

const emit = defineEmits(['logout'])

const { user, subscription } = useProfile()
const { expert, setExpert } = useExpert()
const { phone } = useViewport()
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

function signOut() {
  open.value = false
  emit('logout')
}

function onDocClick(event) {
  // Шторка закрывается сама (затемнение, жест, Esc) и лежит вне root — клик внутри неё не «мимо».
  if (phone.value) return
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
