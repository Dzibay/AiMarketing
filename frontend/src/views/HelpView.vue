<template>
  <!-- Статья -->
  <template v-if="slug">
    <PageHeader :title="current ? current.title : 'Справка'">
      <template #back>
        <RouterLink to="/cabinet/help">← Справка</RouterLink>
      </template>
    </PageHeader>
    <section v-if="current" class="panel help-page">
      <HelpArticle :article="current" />
    </section>
    <EmptyState v-else :icon="FileQuestion" text="Такой статьи нет — возможно, её переименовали.">
      <template #action><RouterLink class="btn btn--accent" to="/cabinet/help">Все статьи</RouterLink></template>
    </EmptyState>
    <HelpAsk />
  </template>

  <!-- Список и поиск -->
  <template v-else>
    <PageHeader description="Короткие статьи о кабинете. Не нашли ответ — напишите в поддержку, ответим здесь же." />
    <div class="help-search">
      <Search aria-hidden="true" />
      <input
        v-model="query"
        type="search"
        placeholder="Например: вернуть изменение, лимит, Telegram"
        aria-label="Поиск по справке"
        enterkeyhint="search"
      />
    </div>

    <template v-if="query.trim()">
      <ul v-if="found.length" class="help-list">
        <li v-for="r in found" :key="r.article.slug">
          <RouterLink class="help-item" :to="`/cabinet/help/${r.article.slug}`">
            <strong>{{ r.article.title }}</strong>
            <span>{{ r.snippet || r.article.summary }}</span>
          </RouterLink>
        </li>
      </ul>
      <EmptyState v-else :icon="SearchX" text="В справке такого нет. Спросите поддержку — ответим в кабинете.">
        <template #action>
          <button class="btn btn--accent" type="button" @click="openSupport">Написать в поддержку</button>
        </template>
      </EmptyState>
    </template>

    <template v-else>
      <RouterLink v-if="whatsNew" class="help-new" :class="{ 'is-fresh': whatsNewFresh }" to="/cabinet/help/changelog">
        <Sparkles aria-hidden="true" />
        <span>
          <small>Что нового · {{ formatDay(whatsNew.date) }}</small>
          <strong>{{ whatsNew.title }}</strong>
        </span>
        <b v-if="whatsNewFresh" class="help-new__tag">новое</b>
      </RouterLink>
      <ul class="help-list help-list--grid">
        <li v-for="a in ARTICLES" :key="a.slug">
          <RouterLink class="help-item" :to="`/cabinet/help/${a.slug}`">
            <strong>{{ a.title }}</strong>
            <span>{{ a.summary }}</span>
          </RouterLink>
        </li>
      </ul>
      <HelpAsk />
    </template>
  </template>
</template>

<script setup>
import { computed, defineComponent, h, ref, watch, watchEffect } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { FileQuestion, LifeBuoy, Search, SearchX, Sparkles } from '@lucide/vue'
import HelpArticle from '../components/HelpArticle.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { markWhatsNewSeen, useHelp } from '../composables/useHelp'
import { openSupport } from '../composables/useSupport'
import { formatDay } from '../utils/format'
import { ARTICLES, article, searchHelp } from '../utils/help'
import { useHead } from '../utils/useHead'

/**
 * Справка: список статей из docs/help, поиск по словам и «Что нового». Открытая «Что нового» гасит точку
 * на колокольчике. Внизу — вход в поддержку: справка не ответила — человек пишет сразу отсюда.
 */
const route = useRoute()
const slug = computed(() => String(route.params.slug || ''))
const current = computed(() => (slug.value ? article(slug.value) : null))
const { whatsNew, whatsNewFresh } = useHelp()

const query = ref('')
const found = computed(() => searchHelp(query.value))

watch(
  slug,
  (s) => {
    if (s === 'changelog') markWhatsNewSeen()
  },
  { immediate: true },
)

watchEffect(() => useHead(`${current.value ? current.value.title : 'Справка'} — Толк`, true))

/** «Не нашли ответ?» — под статьёй и под списком. */
const HelpAsk = defineComponent({
  setup() {
    return () =>
      h('section', { class: 'help-ask' }, [
        h(LifeBuoy, { 'aria-hidden': 'true' }),
        h('div', [h('strong', 'Не нашли ответ?'), h('span', 'Напишите в поддержку — ответим здесь же, в кабинете.')]),
        h('button', { class: 'btn btn--ghost', type: 'button', onClick: openSupport }, 'Написать в поддержку'),
      ])
  },
})
</script>
