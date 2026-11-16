<template>
  <Teleport to="body">
    <aside
      v-if="current && !phone"
      ref="panel"
      class="sup-panel help-panel"
      role="dialog"
      :aria-label="`Справка: ${current.title}`"
      tabindex="-1"
      @keydown.esc="closeHelp"
    >
      <header class="sup-panel__head">
        <div>
          <strong>{{ current.title }}</strong>
          <small>Справка по разделу</small>
        </div>
        <button class="dicon" type="button" aria-label="Закрыть" v-tip="'Закрыть'" @click="closeHelp">
          <X aria-hidden="true" />
        </button>
      </header>
      <div class="help-panel__body">
        <HelpArticle :article="current" @navigate="closeHelp" />
      </div>
      <footer class="help-panel__foot">
        <RouterLink class="btn btn--ghost" :to="`/cabinet/help/${current.slug}`" @click="closeHelp">Открыть в справке</RouterLink>
        <button class="btn btn--ghost" type="button" @click="openSupport">Написать в поддержку</button>
      </footer>
    </aside>
  </Teleport>

  <UiSheet :open="!!current && phone" :title="current?.title || 'Справка'" @close="closeHelp">
    <HelpArticle v-if="current" :article="current" @navigate="closeHelp" />
    <div v-if="current" class="help-panel__foot help-panel__foot--sheet">
      <RouterLink class="btn btn--ghost" :to="`/cabinet/help/${current.slug}`" @click="closeHelp">Открыть в справке</RouterLink>
      <button class="btn btn--ghost" type="button" @click="openSupport">Написать в поддержку</button>
    </div>
  </UiSheet>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { X } from '@lucide/vue'
import { closeHelp, useHelp } from '../composables/useHelp'
import { openSupport, useSupport } from '../composables/useSupport'
import { useViewport } from '../composables/useViewport'
import { article } from '../utils/help'
import HelpArticle from './HelpArticle.vue'
import UiSheet from './ui/UiSheet.vue'

/**
 * Статья справки сбоку, не уходя со страницы: открывается кнопкой у заголовка раздела. На компьютере — панель
 * справа (на месте поддержки: открыта одна из двух), на телефоне — шторка. Закрывается при переходе на другую
 * страницу и когда открывают поддержку.
 */
const { helpSlug } = useHelp()
const { supportOpen } = useSupport()
const { phone } = useViewport()
const route = useRoute()
const panel = ref(null)

const current = computed(() => (helpSlug.value ? article(helpSlug.value) : null))

watch(
  () => route.path,
  () => closeHelp(),
)
watch(supportOpen, (open) => open && closeHelp())

watch(current, async (a) => {
  if (!a) return
  await nextTick()
  panel.value?.focus({ preventScroll: true })
  panel.value?.querySelector('.help-panel__body')?.scrollTo(0, 0)
})
</script>
