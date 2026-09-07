import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = fileURLToPath(new URL('.', import.meta.url))
const projectDir = resolve(frontendDir, '..')
const backendDir = resolve(projectDir, 'backend')
const testDataDir = resolve(projectDir, 'test_data', 'playwright')
const testDatabasePath = resolve(testDataDir, 'noveltools.db').replace(/\\/g, '/')
const frontendUrl = 'http://127.0.0.1:19517'
const backendUrl = 'http://127.0.0.1:19518'

export default defineConfig({
  testDir: './tests',
  use: { baseURL: frontendUrl },
  webServer: [
    {
      name: 'isolated test backend',
      command: 'python -m uvicorn app.main:app --host 127.0.0.1 --port 19518',
      cwd: backendDir,
      url: `${backendUrl}/api/health`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        DATABASE_URL: `sqlite:///${testDatabasePath}`,
        DATA_DIR: testDataDir,
        RUNTIME_ENVIRONMENT: 'TEST',
        CORS_ORIGINS: frontendUrl,
      },
    },
    {
      name: 'isolated test frontend',
      command: 'npm run dev -- --host 127.0.0.1 --port 19517',
      cwd: frontendDir,
      url: frontendUrl,
      reuseExistingServer: false,
      env: {
        ...process.env,
        VITE_API_BASE_URL: `${backendUrl}/api`,
      },
    },
  ],
})
