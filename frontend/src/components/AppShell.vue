<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink class="topbar__brand" to="/cabinet" aria-label="AiMarketing — обзор кабинета">
        <span class="topbar__logo"><BrandMark :size="34" /></span>
        <span class="topbar__name">
          <strong>Ai<span>Marketing</span></strong>
          <small>{{ contextLabel }}</small>
        </span>
      </RouterLink>

      <nav v-if="activeGroup && activeGroup.pages.length > 1" class="topbar__tabs" :aria-label="activeGroup.label">
        <RouterLink
          v-for="page in activeGroup.pages"
          :key="page.to"
          class="topbar__tab"
          :class="{ active: pageMatches(page, route.path) }"
          :to="page.to"
        >
          {{ page.label }}
          <span v-if="page.count" class="topbar__count">{{ page.count }}</span>
        </RouterLink>
      </nav>
      <span v-else class="topbar__spacer"></span>

      <div class="topbar__tools">
        <WorkspacePicker />
        <LoadsIndicator />
        <RouterLink
          v-if="wsId && activeGroup?.key !== 'chat'"
          class="btn btn--accent topbar__ai"
          :to="`/w/${wsId}/chat`"
          title="Спросить ИИ"
        >
          <Sparkles aria-hidden="true" />
          <span>Спросить ИИ</span>
        </RouterLink>
        <button
          class="icon-btn topbar__theme"
          type="button"
          :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'"
          @click="toggleTheme"
        >
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <ProfileMenu @logout="logout" />
      </div>
    </header>

    <aside class="rail">
      <nav class="rail__group rail__group--nav" :aria-label="wsId ? 'Разделы пространства' : 'Разделы кабинета'">
        <template v-if="wsId">
          <RouterLink class="rail__btn" to="/cabinet">
            <House aria-hidden="true" />
            <span class="rail__tip">Обзор кабинета</span>
          </RouterLink>
          <span class="rail__sep" aria-hidden="true"></span>
        </template>
        <RouterLink
          v-for="group in groups"
          :key="group.key"
          class="rail__btn"
          :class="{ active: group === activeGroup }"
          :to="group.pages[0].to"
          :aria-label="group.label"
        >
          <component :is="group.icon" aria-hidden="true" />
          <span class="rail__tip">{{ group.label }}</span>
        </RouterLink>
        <RouterLink
          class="rail__btn rail__bottom"
          :class="{ active: settingsGroup === activeGroup }"
          :to="settingsGroup.pages[0].to"
          :aria-label="settingsGroup.label"
        >
          <Settings aria-hidden="true" />
          <span class="rail__tip">{{ settingsGroup.label }}</span>
        </RouterLink>
        <!-- Одна кнопка темы под настройками: подсвеченным в колонке остаётся только текущий раздел. -->
        <button class="rail__btn" type="button" :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
          <span class="rail__tip">{{ isDark ? 'Светлая тема' : 'Тёмная тема' }}</span>
        </button>
      </nav>
    </aside>

    <main class="cabinet shell__main">
      <slot />
    </main>

    <nav class="tabbar" aria-label="Разделы">
      <RouterLink
        v-for="group in groups"
        :key="group.key"
        class="tabbar__item"
        :class="{ active: group === activeGroup }"
        :to="group.pages[0].to"
      >
        <component :is="group.icon" aria-hidden="true" />
        <span>{{ group.short || group.label }}</span>
      </RouterLink>
      <button
        class="tabbar__item"
        :class="{ active: moreOpen || settingsGroup === activeGroup }"
        type="button"
        :aria-expanded="moreOpen"
        @click="moreOpen = !moreOpen"
      >
        <Ellipsis aria-hidden="true" />
        <span>Ещё</span>
      </button>
    </nav>

    <template v-if="moreOpen">
      <div class="tabbar-backdrop" @click="moreOpen = false"></div>
      <div class="tabbar-sheet" role="menu">
        <RouterLink
          class="menu-item"
          :class="{ active: settingsGroup === activeGroup }"
          role="menuitem"
          :to="settingsGroup.pages[0].to"
        >
          <Settings aria-hidden="true" />
          {{ settingsGroup.label }}
        </RouterLink>
        <template v-if="wsId">
          <hr />
          <template v-for="group in cabinetGroups" :key="group.key">
            <RouterLink v-for="page in group.pages" :key="page.to" class="menu-item" role="menuitem" :to="page.to">
              <component :is="group.icon" aria-hidden="true" />
              {{ page.label === 'Обзор' ? 'Обзор кабинета' : page.label }}
            </RouterLink>
          </template>
          <RouterLink class="menu-item" role="menuitem" :to="CABINET_SETTINGS.pages[0].to">
            <Settings aria-hidden="true" />
            Настройки кабинета
          </RouterLink>
        </template>
        <hr />
        <button class="menu-item is-danger" type="button" role="menuitem" @click="logout">
          <LogOut aria-hidden="true" />
          Выйти
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Ellipsis, House, LogOut, Moon, Settings, Sparkles, Sun } from '@lucide/vue'
import BrandMark from './BrandMark.vue'
import LoadsIndicator from './LoadsIndicator.vue'
import ProfileMenu from './ProfileMenu.vue'
import WorkspacePicker from './WorkspacePicker.vue'
import { CABINET_NAV, CABINET_SETTINGS, groupMatches, pageMatches, workspaceNav, workspaceSettings } from './shellNav'
import { resetLoads } from '../composables/useLoads'
import { loadProfile, resetProfile } from '../composables/useProfile'
import { clearSession, getSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'
import { loadWorkspaces, resetWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { ApiError, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'

const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { workspaces } = useWorkspaces()

const moreOpen = ref(false)

const wsId = computed(() => (route.params.wsId ? Number(route.params.wsId) : null))
const currentWs = computed(() => workspaces.value.find((w) => w.id === wsId.value) || null)

const cabinetGroups = CABINET_NAV
const groups = computed(() =>
  wsId.value ? workspaceNav(wsId.value, currentWs.value?.campaigns_count) : CABINET_NAV,
)
const settingsGroup = computed(() => (wsId.value ? workspaceSettings(wsId.value) : CABINET_SETTINGS))
const activeGroup = computed(
  () => [...groups.value, settingsGroup.value].find((group) => groupMatches(group, route.path)) || null,
)

const contextLabel = computed(() => {
  if (!wsId.value) return 'Кабинет'
  return currentWs.value?.client_name || 'Пространство'
})

watch(
  () => route.fullPath,
  () => {
    moreOpen.value = false
  },
)

function logout() {
  clearSession()
  resetWorkspaces()
  resetLoads()
  resetProfile()
  router.replace('/login')
}

onMounted(async () => {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    await Promise.all([loadWorkspaces(), loadProfile()])
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      resetWorkspaces()
      resetProfile()
      router.replace('/login')
      return
    }
    pushToast(errorText(err), 'error')
  }
})
</script>
