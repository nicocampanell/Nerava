/**
 * Step 9: Auth flow E2E spec.
 *
 * Covers the phone OTP login flow in the driver app using the
 * stub OTP provider configured in playwright.config.ts
 * (OTP_PROVIDER=stub in the webServer env block).
 *
 * Every test is independently runnable — no shared state. Uses
 * `page.waitForSelector` / `expect.toBeVisible` instead of
 * `page.waitForTimeout` per Step 9 requirements.
 */
import { test, expect } from '@playwright/test'

const DRIVER_URL = process.env.DOCKER_COMPOSE ? 'http://localhost/app' : 'http://localhost:5173'

test.describe('Driver auth — phone OTP flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(DRIVER_URL)
    await page.context().grantPermissions(['geolocation'])
  })

  test('OTP login with stub code lands on home screen', async ({ page }) => {
    // Phone input is always visible on the home screen when the
    // user is not authenticated — either as part of the inline
    // login block or inside a modal. Try both.
    const phoneInput = page.locator('input[type="tel"], input[placeholder*="phone" i]').first()
    await expect(phoneInput).toBeVisible({ timeout: 10_000 })

    await phoneInput.fill('+15125551234')

    // Send-code button is labeled variously: Send, Get Code, Continue
    const sendButton = page
      .locator('button:has-text("Send"), button:has-text("Get Code"), button:has-text("Continue")')
      .first()
    await sendButton.click()

    // Code entry field appears
    const codeInput = page
      .locator('input[placeholder*="code" i], input[maxlength="6"], input[inputmode="numeric"]')
      .first()
    await expect(codeInput).toBeVisible({ timeout: 5_000 })

    // Stub accepts code 000000 (see backend/app/services/auth/stub_provider.py)
    await codeInput.fill('000000')

    const verifyButton = page
      .locator('button:has-text("Verify"), button:has-text("Submit"), button:has-text("Continue")')
      .last()
    await verifyButton.click()

    // Home screen renders — assert on a text that only the
    // authenticated home shows
    await expect(
      page.locator('text=/chargers|stations|charging|dwell/i').first(),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('invalid phone format blocks send', async ({ page }) => {
    const phoneInput = page.locator('input[type="tel"], input[placeholder*="phone" i]').first()
    await expect(phoneInput).toBeVisible({ timeout: 10_000 })

    await phoneInput.fill('not-a-phone')
    const sendButton = page
      .locator('button:has-text("Send"), button:has-text("Get Code"), button:has-text("Continue")')
      .first()

    // Either the button is disabled, or clicking it surfaces an
    // error message. Both count as "blocked".
    const isDisabled = await sendButton.isDisabled()
    if (!isDisabled) {
      await sendButton.click()
      await expect(
        page.locator('text=/invalid|error|not valid/i').first(),
      ).toBeVisible({ timeout: 5_000 })
    }
  })
})
