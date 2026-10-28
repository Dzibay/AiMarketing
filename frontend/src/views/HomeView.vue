<template>
  <div class="site">
    <SiteHeader />
    <main>
      <section class="hero">
        <div class="wrap hero__grid">
          <div>
            <div class="hero__brand">
              <BrandMark class="hero__logo" :size="148" alt="AiMarketing" />
              <h1 class="hero__title">Ai<span>Marketing</span></h1>
            </div>
            <p class="hero__lead">
              Зарегистрируйтесь, откройте кабинет и подключите Метрику с Директом одной кнопкой —
              через страницу подтверждения Яндекса, как Google OAuth.
            </p>
            <div class="hero__actions">
              <RouterLink class="btn btn--accent" to="/register">Зарегистрироваться</RouterLink>
              <RouterLink class="btn btn--ghost" to="/login">Войти</RouterLink>
            </div>
          </div>
          <aside class="hero-panel">
            <h2>Сначала аккаунт — потом Яндекс</h2>
            <p>
              В кабинете два блока: Метрика и Директ. Нажали «Подключить» —
              ушли на oauth.yandex.ru, согласились, вернулись с уже найденными счётчиками и кабинетами.
            </p>
          </aside>
        </div>
      </section>

      <section class="section">
        <div class="wrap">
          <h2>Как это работает</h2>
          <p class="lead">Три шага без ручного ввода токенов.</p>
          <div class="steps">
            <article class="step">
              <h3>Регистрация</h3>
              <p>Создаёте аккаунт по почте и паролю.</p>
            </article>
            <article class="step">
              <h3>Подтверждение в Яндексе</h3>
              <p>Одна кнопка открывает страницу Яндекса — как вход через Google.</p>
            </article>
            <article class="step">
              <h3>Кабинеты в SaaS</h3>
              <p>Метрика и Директ появляются в кабинете автоматически.</p>
            </article>
          </div>
        </div>
      </section>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import BrandMark from '../components/BrandMark.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { getSession } from '../composables/useSession'
import { useSiteConfig } from '../composables/useSiteConfig'
import { useHead } from '../utils/useHead'

useHead('AiMarketing — автоматизация Директа и Метрики')

const router = useRouter()
const { load } = useSiteConfig()

onMounted(() => {
  if (getSession()) {
    router.replace('/cabinet')
    return
  }
  load()
})
</script>
