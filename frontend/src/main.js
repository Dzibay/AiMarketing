import { createApp } from 'vue'
import '@fontsource-variable/onest'
import App from './App.vue'
import router from './router'
import './styles/site.css'

createApp(App).use(router).mount('#app')
