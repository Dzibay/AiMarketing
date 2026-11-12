import { createApp } from 'vue'
import '@fontsource-variable/manrope'
import App from './App.vue'
import { vField } from './directives/field'
import router from './router'
import './styles/site.css'
import './styles/shell.css'
import './styles/ui.css'

createApp(App).directive('field', vField).use(router).mount('#app')
