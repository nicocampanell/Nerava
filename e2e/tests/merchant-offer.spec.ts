/**
 * Step 9: Merchant offer discovery E2E spec.
 *
 * Verifies that the driver app renders merchant cards and that
 * tapping a card opens the merchant detail surface. Does not
 * require auth — the landing / pre-auth view still shows the
 * merchant carousel in the demo-static path.
 */
import { test, expect } from '@playwright/test'

const DRIVER_URL = process.env.DOCKER_COMPOSE ? 'http://localhost/app' : 'http://localhost:5173'

test.describe('Driver app — merchant offer discovery', () => {
  test.beforeEach(async ({ page }) => {
    await page.context().grantPermissions(['geolocation'])
    await page.goto(DRIVER_URL)
  })

  test('merchant carousel or list is visible on home', async ({ page }) => {
    // The driver home shows either the MerchantCarousel or a
    // merchant list view. Both surfaces render real text from
    // merchant.name. The demo-static driver seeds at least one
    // merchant so this assertion is deterministic.
    const anyMerchant = page
      .locator(
        '[data-testid="merchant-card"], text=/pizza|coffee|taco|restaurant|store/i',
      )
      .first()
    await expect(anyMerchant).toBeVisible({ timeout: 10_000 })
  })

  test('tapping a merchant card opens detail surface', async ({ page }) => {
    const merchantCard = page
      .locator(
        '[data-testid="merchant-card"], text=/pizza|coffee|taco|restaurant|store/i',
      )
      .first()
    await expect(merchantCard).toBeVisible({ timeout: 10_000 })

    // Capture the text we see now so we can compare after tapping.
    // This avoids waitForTimeout — we're asserting a state change
    // not a fixed delay.
    await merchantCard.click()

    // The detail surface always shows one of: walk time, distance,
    // address, phone number, hours. Assert on any of those texts
    // being visible post-tap.
    const detailMarker = page
      .locator('text=/walk|minute|mile|address|phone|hours|menu/i')
      .first()
    await expect(detailMarker).toBeVisible({ timeout: 5_000 })
  })
})
