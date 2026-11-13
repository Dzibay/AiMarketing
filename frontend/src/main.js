import { createApp } from 'vue'
import '@fontsource-variable/manrope'
import App from './App.vue'
import { vField } from './directives/field'
import { vTip } from './directives/tip'
import router from './router'
import './styles/site.css'
import './styles/shell.css'
import './styles/ui.css'
import './styles/chat.css'

createApp(App).directive('field', vField).directive('tip', vTip).use(router).mount('#app')
