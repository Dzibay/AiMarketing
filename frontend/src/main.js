import { createApp } from 'vue'
import '@fontsource-variable/onest'
import App from './App.vue'
import { vField } from './directives/field'
import router from './router'
import './styles/site.css'

createApp(App).directive('field', vField).use(router).mount('#app')
