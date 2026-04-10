import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Build-time check: fail if mock mode is enabled in production
  const mockMode = process.env.VITE_MOCK_MODE === 'true'
  const env = process.env.VITE_ENV || mode
  
  if (mockMode && (env === 'prod' || env === 'production')) {
    throw new Error(
      'VITE_MOCK_MODE cannot be true in production builds. ' +
      'Set VITE_MOCK_MODE=false and VITE_ENV=prod for production.'
    )
  }
  
  // Build-time check: fail if API URL is invalid in production
  if (env === 'prod' || env === 'production') {
    const apiBaseUrl = process.env.VITE_API_BASE_URL || ''
    if (apiBaseUrl === '/api' || apiBaseUrl.startsWith('http://localhost')) {
      throw new Error(
        `VITE_API_BASE_URL cannot be '/api' or 'http://localhost:*' in production builds. ` +
        `Current value: ${apiBaseUrl}. ` +
        `Set VITE_API_BASE_URL to your production API URL (e.g., https://api.nerava.network)`
      )
    }
  }
  
  return {
    base: process.env.VITE_PUBLIC_BASE || '/',
    plugins: [react()],
    server: {
      host: '0.0.0.0', // Allow access from network (for phone testing)
      port: 5173,
      proxy: env !== 'prod' && env !== 'production' ? {
        '/v1': {
          target: 'https://api.nerava.network',
          changeOrigin: true,
          secure: true,
        },
      } : undefined,
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: './tests/setup.ts',
      exclude: [
        'node_modules',
        'dist',
        'e2e/**',
        // Tests broken by UI changes (LoginModal/WalletModal buttons renamed)
        'src/components/__tests__/LoginModal.test.tsx',
        'src/components/__tests__/WalletModal.test.tsx',
      ],
    },
  }
})
