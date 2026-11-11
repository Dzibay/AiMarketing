<template>
  <section class="hask">
    <h2 class="hask__title">{{ greeting }}</h2>
    <p class="hask__lead">
      {{
        wsId
          ? 'Спросите про этот проект — ИИ посмотрит кампании, запросы и статистику и предложит правки на согласование.'
          : 'Спросите про любой проект — ИИ посмотрит кампании, запросы и статистику и предложит правки на согласование.'
      }}
    </p>
    <form class="hask__box" @submit.prevent="submit()">
      <textarea
        v-model="text"
        rows="2"
        maxlength="4000"
        placeholder="Например: почему вырос CPA на этой неделе?"
        aria-label="Вопрос ИИ"
        @keydown.enter.exact.prevent="submit()"
      />
      <button class="hask__send" type="submit" :disabled="!text.trim()" aria-label="Спросить">
        <ArrowUp aria-hidden="true" />
      </button>
    </form>
    <div class="hask__chips">
      <button v-for="p in presets" :key="p.ask" class="hask__chip" type="button" @click="preset(p.ask)">
        <component :is="p.icon" aria-hidden="true" />
        {{ p.label }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUp, FlaskConical, Link2, ListChecks, SearchCheck, Sparkles, Target, TrendingUp } from '@lucide/vue'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { useProfile } from '../composables/useProfile'

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

const presets = computed(() =>
  props.wsId
    ? [
        { ask: 'problems', label: 'Что горит', icon: Sparkles },
        { ask: 'audit', label: 'Аудит кампаний', icon: ListChecks },
        { ask: 'experiment', label: 'Предложить эксперимент', icon: FlaskConical },
        { ask: 'links', label: 'Проверить ссылки', icon: Link2 },
      ]
    : [
        { ask: 'first', label: 'С чего начать', icon: Sparkles },
        { ask: 'cleanup', label: 'Почистить запросы', icon: SearchCheck },
        { ask: 'analytics', label: 'Сравнить проекты', icon: TrendingUp },
        { ask: 'kpi', label: 'Проверить цели и KPI', icon: Target },
      ],
)

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
