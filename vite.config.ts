import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/fetch-for-print': 'http://localhost:3000',
      '/printimage': 'http://localhost:3000',
      '/test': 'http://localhost:3000',
    },
  },
})
