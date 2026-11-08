import { createRoot } from 'react-dom/client'
import { App } from './App'

const bridge = window.witnessWorkbench
if (bridge) createRoot(document.getElementById('reactWorkbench')).render(<App bridge={bridge} />)
