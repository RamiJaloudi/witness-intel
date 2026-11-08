import { defineConfig } from 'vite'

export default defineConfig({
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../app/static/react',
    emptyOutDir: true,
    lib: { entry: 'src/main.jsx', formats: ['es'], fileName: 'workbench' },
    rolldownOptions: { output: { comments: { legal: true } } },
  },
  oxc: { jsx: { runtime: 'automatic' } },
})
