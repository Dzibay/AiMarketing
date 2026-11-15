<template>
  <!-- Приветствие — заголовок страницы (h1 в шапке), пояснение — строка под ним. -->
  <PageHeader :title="greeting" :description="lead" />
  <!-- Между приветствием и вопросом — сводка «как дела» (HomeHeadline): первое, что видно на главной. -->
  <slot />
  <section class="hask" aria-label="Вопрос ИИ">
    <form class="hask__box" @submit.prevent="submit()">
      <textarea
        v-model="text"
        rows="2"
        maxlength="4000"
        placeholder="Например: почему подорожали заявки на этой неделе?"
        aria-label="Вопрос ИИ"
        @keydown.enter.exact.prevent="submit()"
      />
      <button class="hask__send" type="submit" :disabled="!text.trim()" aria-label="Спросить">
        <ArrowUp aria-hidden="true" />
      </button>
    </form>
    <div class="hask__chips">
      <button v-for="p in home" :key="p.key" class="hask__chip" type="button" v-tip="p.text" @click="preset(p.key)">
        <component :is="ICONS[p.icon] || Sparkles" aria-hidden="true" />
        {{ p.title }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUp, FlaskConical, Link2, ListChecks, SearchCheck, Sparkles, Target, TrendingUp } from '@lucide/vue'
import PageHeader from './ui/PageHeader.vue'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { useProfile } from '../composables/useProfile'
import { usePrompts } from '../composables/usePrompts'

/** Главная «Диалога»: вопрос ИИ крупно сверху, под ним — быстрые вопросы. Без wsId — общий чат. */
const props = defineProps({ wsId: { type: [Number, String], default: null } })

const router = useRouter()
const { user } = useProfile()
const text = ref('')

const chatPath = computed(() => (props.wsId ? `/w/${props.wsId}/chat` : '/cabinet/chat'))

const greeting = computed(() => {
  const h = new Date().getHours()
  const part = h < 5 ? 'Доброй ночи' : h < 12 ? 'Доброе утро' : h < 18 ? 'Добрый день' : 'Добрый вечер'
  const name = (user.value?.name || '').trim().split(/\s+/)[0]
  return name ? `${part}, ${name}` : part
})

const lead = computed(() =>
  props.wsId
    ? 'Спросите про этот проект — ИИ посмотрит кампании, запросы и статистику и предложит правки на согласование.'
    : 'Спросите про любой проект — ИИ посмотрит кампании, запросы и статистику и предложит правки на согласование.',
)

// Быстрые вопросы — готовые задачи с пометкой home (backend/app/ai_prompts.py); чат берёт текст по ключу.
const ICONS = { FlaskConical, Link2, ListChecks, SearchCheck, Sparkles, Target, TrendingUp }
const { home } = usePrompts(computed(() => (props.wsId ? 'project' : 'general')))

function submit() {
  const q = text.value.trim()
  if (!q) return
  putHomeQuestion(q)
  router.push({ path: chatPath.value, query: { ask: 'text' } })
}

function preset(ask) {
  router.push({ path: chatPath.value, query: { ask } })
}
</script>
