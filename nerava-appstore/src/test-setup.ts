// Global test setup for vitest + React Testing Library.
//
// - Extends `expect` with jest-dom matchers so tests can use
//   `toBeInTheDocument`, `toHaveAttribute`, etc.
// - Registers an explicit `afterEach(cleanup)` because RTL's
//   automatic cleanup fires on `afterEach` globals from the test
//   runner, and vitest 4 in our config does NOT enable the globals
//   flag (`test.globals: false` in vitest.config.ts). Explicit
//   registration via `cleanup` keeps the DOM fresh between tests
//   without polluting every test file with an import.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
