/**
 * App catalog for the Nerava App Store.
 *
 * Five seeded apps matching the Step 12 prompt spec. Each app has a
 * category that drives the home-screen filter tabs, a permissions list
 * shown on the detail screen, and a default enabled state.
 *
 * This is static data — there's no backend fetch yet. In a future
 * iteration this would come from the Nerava catalog API.
 */

export type AppCategory = "Rewards" | "Fleet" | "Intelligence" | "Insurance";

export const APP_CATEGORIES: readonly AppCategory[] = [
  "Rewards",
  "Fleet",
  "Intelligence",
  "Insurance",
] as const;

export type AppPermission =
  | "Vehicle Data"
  | "Wallet Access"
  | "Session Data"
  | "Location";

export interface AppEntry {
  readonly id: string;
  readonly name: string;
  readonly developer: string;
  readonly category: AppCategory;
  readonly shortTagline: string;
  readonly description: string;
  readonly permissions: readonly AppPermission[];
  readonly defaultEnabled: boolean;
  readonly rating: number;
  /** Gradient classes for the app icon tile. */
  readonly iconGradient: string;
  /** Short label inside the icon tile (first letter is fine). */
  readonly iconLabel: string;
}

export const APPS: readonly AppEntry[] = [
  {
    id: "nerava-wallet",
    name: "Nerava Wallet",
    developer: "Nerava Inc.",
    category: "Rewards",
    shortTagline: "Get paid to charge",
    description:
      "Earn wallet credits at every charging session. Spend at nearby merchants. Get paid to charge.",
    permissions: ["Vehicle Data", "Wallet Access", "Session Data"],
    defaultEnabled: true,
    rating: 4.8,
    iconGradient: "from-[#13A3E2] to-[#0B1A3C]",
    iconLabel: "N",
  },
  {
    id: "nerava-intelligence",
    name: "Nerava Intelligence",
    developer: "Nerava Inc.",
    category: "Intelligence",
    shortTagline: "Real-time charger analytics",
    description:
      "Real-time charger utilization and behavioral analytics for fleet operators and property managers.",
    permissions: ["Vehicle Data", "Session Data", "Location"],
    defaultEnabled: false,
    rating: 4.6,
    iconGradient: "from-[#0B1A3C] to-[#13A3E2]",
    iconLabel: "I",
  },
  {
    id: "driveshield-insurance",
    name: "DriveShield Insurance",
    developer: "DriveShield",
    category: "Insurance",
    shortTagline: "Lower premiums from your data",
    description:
      "Your verified EV charging behavior becomes a data asset. Share it and earn lower premiums.",
    permissions: ["Vehicle Data", "Session Data"],
    defaultEnabled: false,
    rating: 4.3,
    iconGradient: "from-[#1A2744] to-[#4A6FA5]",
    iconLabel: "D",
  },
  {
    id: "fleetsync-trident",
    name: "FleetSync",
    developer: "Trident Chargers",
    category: "Fleet",
    shortTagline: "Fleet reimbursement + wallets",
    description:
      "Verified charging reimbursement and fleet wallet management for the Trident operator network.",
    permissions: ["Vehicle Data", "Wallet Access", "Session Data", "Location"],
    defaultEnabled: false,
    rating: 4.7,
    iconGradient: "from-[#0B1A3C] to-[#1A2744]",
    iconLabel: "F",
  },
  {
    id: "evgo-chargerewards",
    name: "ChargeRewards",
    developer: "EVgo",
    category: "Rewards",
    shortTagline: "Bonus credits at EVgo",
    description:
      "Link your EVgo account and earn bonus wallet credits on sessions at EVgo stations.",
    permissions: ["Vehicle Data", "Wallet Access"],
    defaultEnabled: false,
    rating: 4.1,
    iconGradient: "from-[#13A3E2] to-[#1A2744]",
    iconLabel: "E",
  },
] as const;
