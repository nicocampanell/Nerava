/**
 * Step 9: Merchant portal E2E spec.
 *
 * Smokes the merchant portal login + dashboard shell. Full
 * acquisition / claim flow is covered separately in the existing
 * merchant-flow.spec.ts. This file asserts the portal renders
 * after Google SSO AND that the login page renders when the auth
 * state is cleared.
 *
 * IMPORTANT (CodeRabbit review fix): Playwright's `page.addInitScript`
 * re-executes on EVERY new document load, including `page.reload()`.
 * That means seeding localStorage in `beforeEach` and then clearing
 * it + reloading in a "logged out" test does NOT produce a logged-out
 * state — the init script re-seeds auth before the page code runs.
 *
 * Fix: move the auth seeding out of `beforeEach` and into a per-test
 * helper that only runs in tests that WANT auth. Tests that need a
 * logged-out state skip the seeding entirely.
 */
import { test, expect, Page } from '@playwright/test'

const MERCHANT_URL = process.env.DOCKER_COMPOSE
  ? 'http://localhost/merchant'
  : 'http://localhost:5174'

async function seedMerchantAuth(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem('access_token', 'mock.test.token')
    window.localStorage.setItem('merchant_id', 'mock-merchant-id-12345')
    window.localStorage.setItem('merchant_name', 'Test Pizzeria')
    window.localStorage.setItem('businessClaimed', 'true')
    window.localStorage.setItem('merchant_authenticated', 'true')
    window.localStorage.setItem('place_id', 'ChIJ_test_merchant')
  })
}

test.describe('Merchant portal — dashboard shell', () => {
  test('dashboard shell loads with navigation when auth is seeded', async ({ page }) => {
    // Seed localStorage BEFORE the first navigation so the app
    // sees the logged-in state on first render.
    await seedMerchantAuth(page)
    await page.goto(MERCHANT_URL)

    // The portal chrome has a sidebar or nav with these labels.
    // Accept any one of them as proof the shell rendered.
    const navMarker = page
      .locator('text=/dashboard|overview|exclusives|visits|settings/i')
      .first()
    await expect(navMarker).toBeVisible({ timeout: 10_000 })
  })

  test('login page renders the Google sign-in affordance', async ({ page }) => {
    // NO auth seeding — the portal boots into the unauthenticated state.
    // This is the correct Playwright pattern: never addInitScript then
    // clear + reload, because addInitScript re-executes on reload.
    await page.goto(MERCHANT_URL)

    // The merchant portal login surface shows Google SSO as the
    // primary affordance. It may also show "Sign in" or "Claim".
    const loginMarker = page
      .locator('text=/sign in|google|continue|claim your business/i')
      .first()
    await expect(loginMarker).toBeVisible({ timeout: 10_000 })
  })

  test('dashboard does not leak raw stack traces to the user', async ({ page }) => {
    // Seed auth so we're in the dashboard code path, not the login
    // page. If the mock JWT triggers a 401/403 on a dashboard API
    // call, the UI should show a friendly error state, not a raw
    // stack trace or exception type leaked into the visible DOM.
    await seedMerchantAuth(page)
    await page.goto(MERCHANT_URL)
    await page.waitForLoadState('domcontentloaded')

    const bodyText = await page.locator('body').innerText()
    expect(bodyText).not.toMatch(/Traceback|stack trace|SyntaxError|TypeError:/)
  })
})
