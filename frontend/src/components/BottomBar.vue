<template>
  <nav v-show="shown" class="bbar" aria-label="Разделы">
    <RouterLink class="bbar__item" :class="{ active: section === 'summary' }" :to="to.summary">
      <House aria-hidden="true" />
      <span>Главная</span>
    </RouterLink>
    <RouterLink class="bbar__item" :class="{ active: section === 'campaigns' }" :to="to.campaigns">
      <Megaphone aria-hidden="true" />
      <span>Кампании</span>
    </RouterLink>
    <RouterLink v-if="accessOk" class="bbar__item bbar__ai" :to="chatTo" aria-label="Новый чат с ИИ">
      <span class="bbar__orb" aria-hidden="true"><Sparkles /></span>
      <span aria-hidden="true">ИИ</span>
    </RouterLink>
    <RouterLink
      v-if="accessOk"
      class="bbar__item"
      :class="{ active: section === 'pending' }"
      to="/cabinet/pending"
      :aria-label="pendingCount ? `Решения, ждут: ${pendingCount}` : 'Решения'"
    >
      <span class="bbar__ico" aria-hidden="true">
        <ListChecks />
        <b v-if="pendingCount" class="bbar__n">{{ pendingCount > 99 ? '99+' : pendingCount }}</b>
      </span>
      <span aria-hidden="true">Решения</span>
    </RouterLink>
    <button
      class="bbar__item"
      :class="{ active: moreActive }"
      type="button"
      aria-haspopup="dialog"
      :aria-expanded="more"
      @click="more = true"
    >
      <Ellipsis aria-hidden="true" />
      <span>Ещё</span>
    </button>
  </nav>

  <UiSheet :open="more" title="Ещё" @close="more = false">
    <div class="more" @click="closeOnLink">
      <ProjectSwitcher v-if="accessOk" row />
      <RouterLink
        v-for="item in sections"
        :key="item.key"
        class="more__row"
        :class="{ active: item.active }"
        :to="item.to"
      >
        <component :is="item.icon" aria-hidden="true" />
        <span class="more__t">
          <b>{{ item.label }}</b>
          <small>{{ item.hint }}</small>
        </span>
        <ChevronRight class="more__go" aria-hidden="true" />
      </RouterLink>
      <hr />
      <RouterLink
        v-for="item in service"
        :key="item.key"
        class="more__row"
        :class="{ active: item.active }"
        :to="item.to"
      >
        <component :is="item.icon" aria-hidden="true" />
        <span class="more__t">
          <b>{{ item.label }}</b>
          <small>{{ item.hint }}</small>
        </span>
        <ChevronRight class="more__go" aria-hidden="true" />
      </RouterLink>
      <hr />
      <button class="more__row" type="button" role="switch" :aria-checked="isDark" @click="toggleTheme">
        <Moon aria-hidden="true" />
        <span class="more__t">
          <b>Тёмная тема</b>
          <small>Запоминается на этом устройстве</small>
        </span>
        <span class="menu-switch" :class="{ 'is-on': isDark }" aria-hidden="true"></span>
      </button>
      <button class="more__row is-danger" type="button" @click="signOut">
        <LogOut aria-hidden="true" />
        <span class="more__t">
          <b>Выйти</b>
          <small>{{ user?.email || 'Закончить работу на этом устройстве' }}</small>
        </span>
      </button>
    </div>
  </UiSheet>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch, watchEffect } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ChevronRight, Ellipsis, House, ListChecks, LogOut, Megaphone, Moon, Sparkles } from '@lucide/vue'
import ProjectSwitcher from './ProjectSwitcher.vue'
import UiSheet from './ui/UiSheet.vue'
import { SECTIONS, SERVICE, pageMatches, sectionTo } from './shellNav'
import { useExpert } from '../composables/useExpert'
import { usePending } from '../composables/usePending'
import { useProfile } from '../composables/useProfile'
import { useProjectFilter } from '../composables/useProjectFilter'
import { useTheme } from '../composables/useTheme'
import { useViewport } from '../composables/useViewport'

/**
 * Нижняя панель кабинета на узком экране (до 900 px) — вместо бокового меню: «Главная», «Кампании», крупная кнопка
 * «ИИ» (новый чат выбранного проекта или общий), «Решения» со счётчиком и «Ещё». В шторке «Ещё» — остальные разделы
 * с пояснениями, выбор проекта, служебные страницы, тема и выход.
 *
 * Панель прячется, пока открыта экранная клавиатура, и на странице чата: там внизу поле ввода, а выйти можно
 * стрелкой «назад» в верхней строке. Пока панель видна, у <html> класс has-bbar — по нему страница и всё, что
 * прилипает к низу, оставляют под панель место (переменная --bbar-h).
 */
const props = defineProps({
  accessOk: { type: Boolean, default: true },
  chatTo: { type: String, required: true },
  /** Тариф и лимит из оболочки: { percent, percentText, text } или null. */
  limitChip: { type: Object, default: null },
})
const emit = defineEmits(['logout'])

const route = useRoute()
const { user } = useProfile()
const { pendingCount } = usePending()
const { projectId } = useProjectFilter()
const { expert } = useExpert()
const { isDark, toggleTheme } = useTheme()
const { typing } = useViewport()

const section = computed(() => route.meta.section)
const shown = computed(() => section.value !== 'chat' && !typing.value)

watchEffect(() => document.documentElement.classList.toggle('has-bbar', shown.value))
onBeforeUnmount(() => document.documentElement.classList.remove('has-bbar'))

// Разделы, у которых своя кнопка в панели; остальные — в «Ещё».
const IN_BAR = ['summary', 'campaigns']

function linkTo(key) {
  const item = SECTIONS.find((s) => s.key === key)
  return sectionTo(item, projectId.value) || item.all
}

const to = computed(() => Object.fromEntries(IN_BAR.map((key) => [key, linkTo(key)])))

const sections = computed(() =>
  SECTIONS.filter((s) => !s.hidden && !IN_BAR.includes(s.key) && (expert.value || !s.expert)).map((s) => ({
    ...s,
    to: linkTo(s.key),
    active: section.value === s.key,
  })),
)

const service = computed(() =>
  SERVICE.map((s) => ({
    ...s,
    hint: s.key === 'plan' ? planHint.value || s.hint : s.hint,
    active: pageMatches(s, route.path),
  })),
)

/** «Тариф Старт до 12 декабря · лимит ИИ: 34 %» — вместо шкалы, которая на широком экране стоит внизу меню. */
const planHint = computed(() => {
  const chip = props.limitChip
  if (!chip) return ''
  return chip.percent === null ? chip.text : `${chip.text} · лимит ИИ: ${chip.percentText}`
})

// «Ещё» подсвечена, когда открытая страница — из её списка.
const moreActive = computed(() => sections.value.some((s) => s.active) || service.value.some((s) => s.active))

// --- Шторка «Ещё» ---

const more = ref(false)

watch(
  () => route.fullPath,
  () => (more.value = false),
)

// Ссылка на уже открытую страницу адрес не меняет — закрываем по самому нажатию.
function closeOnLink(event) {
  if (event.target.closest('a')) more.value = false
}

function signOut() {
  more.value = false
  emit('logout')
}
</script>
