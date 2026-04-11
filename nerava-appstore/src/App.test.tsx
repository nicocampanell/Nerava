// Framework: vitest + React Testing Library (matches the monorepo's
// apps/driver test stack). Smoke tests for the nerava-appstore UI.
//
// Covers the critical paths the prompt specified:
//   - Home renders with featured banner + app grid
//   - Category tabs filter the grid
//   - Clicking an app tile opens the detail screen
//   - Toggle button shows loading state, flips enabled state after
//     the 1-second animation
//   - Bottom tab bar navigates between Discover, My Apps, Settings
//   - My Apps shows enabled apps + disable works
//
// Query hygiene: RTL's `name: "..."` option is a SUBSTRING match, so
// generic names like "Rewards" or "Settings" collide with text that
// appears inside multiple buttons. Tests use scoped queries via
// `within()` + region aria-labels, and use `name: /^foo$/` regex
// anchors for exact matching where needed.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";

import App from "./App.js";

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

/** Query helper: the category tab bar scoped via its aria-label. */
function getCategoryNav(): HTMLElement {
  return screen.getByRole("navigation", { name: "Filter by category" });
}

/** Query helper: the bottom tab bar scoped via its aria-label. */
function getBottomNav(): HTMLElement {
  return screen.getByRole("navigation", { name: "Main navigation" });
}

describe("nerava-appstore <App />", () => {
  it("renders the home screen with the featured banner and at least one app", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "App Store" })).toBeInTheDocument();
    expect(screen.getByText("Get Paid to Charge")).toBeInTheDocument();
    // Nerava Wallet appears on the home app grid.
    expect(screen.getByText("Nerava Wallet")).toBeInTheDocument();
  });

  it("filters the app grid when a category tab is selected", () => {
    render(<App />);
    // Scope to the category tab nav so we don't collide with AppCard buttons
    // whose accessible names contain the category text.
    const rewardsTab = within(getCategoryNav()).getByRole("button", { name: "Rewards" });
    fireEvent.click(rewardsTab);

    // Nerava Wallet is in Rewards — still visible.
    expect(screen.getByText("Nerava Wallet")).toBeInTheDocument();
    // DriveShield Insurance is in Insurance — hidden after filter.
    expect(screen.queryByText("DriveShield Insurance")).not.toBeInTheDocument();
  });

  it("opens the app detail screen when an app card is clicked", () => {
    render(<App />);
    // AppCard accessible names contain the app name + developer + category
    // + rating. Use a regex and be permissive, then verify detail content.
    const walletCard = screen
      .getAllByRole("button")
      .find((btn) => /Nerava Wallet/.test(btn.textContent ?? ""));
    expect(walletCard).toBeTruthy();
    fireEvent.click(walletCard as HTMLElement);

    // Detail screen shows the "App Detail" header label.
    expect(screen.getByText("App Detail")).toBeInTheDocument();
    expect(
      screen.getByText(/Earn wallet credits at every charging session/),
    ).toBeInTheDocument();
    expect(screen.getByText("Vehicle Data")).toBeInTheDocument();
    expect(screen.getByText("Wallet Access")).toBeInTheDocument();
  });

  it("back button on detail screen returns to home", () => {
    render(<App />);
    const walletCard = screen
      .getAllByRole("button")
      .find((btn) => /Nerava Wallet/.test(btn.textContent ?? ""));
    fireEvent.click(walletCard as HTMLElement);
    expect(screen.getByText("App Detail")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Back to app store" }));
    expect(screen.queryByText("App Detail")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "App Store" })).toBeInTheDocument();
  });

  it("toggle button shows loading state and flips enabled state after 1 second", () => {
    vi.useFakeTimers();
    render(<App />);

    // Open detail for an app NOT enabled by default.
    const driveShieldCard = screen
      .getAllByRole("button")
      .find((btn) => /DriveShield Insurance/.test(btn.textContent ?? ""));
    fireEvent.click(driveShieldCard as HTMLElement);

    // Initial: "Enable" button visible.
    const enableButton = screen.getByRole("button", { name: "Enable" });
    expect(enableButton).toBeInTheDocument();

    // Click enable → loading state.
    fireEvent.click(enableButton);
    expect(screen.getByText("Enabling…")).toBeInTheDocument();

    // Advance 1 second.
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // After the animation, button says "Disable".
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(screen.queryByText("Enabling…")).not.toBeInTheDocument();
  });

  it("navigates to My Apps via the bottom tab bar and shows enabled apps", () => {
    render(<App />);
    const myAppsTab = within(getBottomNav()).getByRole("button", { name: /My Apps/ });
    fireEvent.click(myAppsTab);

    expect(screen.getByRole("heading", { name: "My Apps" })).toBeInTheDocument();
    // Nerava Wallet is enabled by default, so it appears.
    expect(screen.getByText("Nerava Wallet")).toBeInTheDocument();
    // DriveShield Insurance is not enabled by default, so it does NOT appear.
    expect(screen.queryByText("DriveShield Insurance")).not.toBeInTheDocument();
  });

  it("My Apps disable button removes an app from the enabled list", () => {
    render(<App />);
    fireEvent.click(within(getBottomNav()).getByRole("button", { name: /My Apps/ }));

    // The My Apps row has a "Disable" button directly in it.
    const disableButton = screen.getByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    // Nerava Wallet is no longer in the list.
    expect(screen.queryByText("Nerava Wallet")).not.toBeInTheDocument();
    // Empty state appears.
    expect(screen.getByText("No apps enabled yet")).toBeInTheDocument();
  });

  it("navigates to Settings via the bottom tab bar", () => {
    render(<App />);
    const settingsTab = within(getBottomNav()).getByRole("button", { name: /Settings/ });
    fireEvent.click(settingsTab);

    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Preferences" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Privacy" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "About" })).toBeInTheDocument();
  });
});
