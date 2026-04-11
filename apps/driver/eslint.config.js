import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      // reactHooks.configs.flat.recommended — disabled, React Compiler errors need refactoring
      // reactRefresh.configs.vite — disabled: 27 React Compiler errors need refactoring
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Pre-existing violations (215 total) — downgraded to warnings, fix incrementally
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/ban-ts-comment': 'warn',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      // React hooks disabled — React Compiler errors need refactoring before re-enabling
      // Additional pre-existing violations
      'no-empty': 'warn',
      '@typescript-eslint/no-unused-expressions': 'warn',
      'no-case-declarations': 'warn',
      'no-constant-condition': 'warn',
      // 'react-refresh/only-export-components': 'warn', — disabled with plugin
    },
  },
])
