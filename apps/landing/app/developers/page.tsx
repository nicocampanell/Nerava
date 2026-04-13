import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Developers - Nerava',
  description: 'Build on the Nerava EV charging intelligence platform. Submit sessions, evaluate incentives, and manage driver wallets with our TypeScript SDK.',
}

export default function DevelopersPage() {
  return (
    <main className="min-h-screen bg-white">
      {/* Hero */}
      <section className="bg-[#E8F0FF] py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <p className="text-sm font-semibold text-[#2952E8] uppercase tracking-wide mb-4">
            Nerava for Developers
          </p>
          <h1 className="text-4xl md:text-5xl font-bold text-[#1a1a1a] mb-6">
            Build on verified EV charging data
          </h1>
          <p className="text-lg text-[#6b6b6b] max-w-2xl mx-auto mb-8">
            Integrate with the Nerava platform to submit charging sessions, evaluate incentive campaigns, and manage driver wallets. One SDK, typed end-to-end, zero runtime dependencies.
          </p>
          <div className="inline-flex items-center gap-3 bg-white border border-[#e5e5e5] rounded-lg px-5 py-3 shadow-sm">
            <code className="text-sm font-mono text-[#2952E8]">npm install @nerava/sdk</code>
          </div>
        </div>
      </section>

      {/* SDK Overview */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-3xl font-bold text-[#1a1a1a] mb-4">The Nerava SDK</h2>
          <p className="text-[#6b6b6b] mb-12 max-w-2xl">
            The official TypeScript SDK wraps the Nerava Partner API behind typed modules. Partners install it into their Node services or TypeScript apps and call methods instead of hand-rolling HTTP requests, auth headers, or response parsing.
          </p>

          <div className="grid md:grid-cols-2 gap-6 mb-16">
            <div className="border border-[#e5e5e5] rounded-lg p-6">
              <div className="w-10 h-10 bg-[#E8F0FF] rounded-lg flex items-center justify-center mb-4">
                <svg className="w-5 h-5 text-[#2952E8]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[#1a1a1a] mb-2">Submit Sessions</h3>
              <p className="text-sm text-[#6b6b6b]">
                Send verified charging sessions from your network. Each session is evaluated against active campaigns and returns incentive grants in real time.
              </p>
            </div>

            <div className="border border-[#e5e5e5] rounded-lg p-6">
              <div className="w-10 h-10 bg-[#E8F0FF] rounded-lg flex items-center justify-center mb-4">
                <svg className="w-5 h-5 text-[#2952E8]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[#1a1a1a] mb-2">Evaluate Incentives</h3>
              <p className="text-sm text-[#6b6b6b]">
                Query active campaigns by location, network, or charger type. The incentive engine matches sessions to the highest-priority campaign automatically.
              </p>
            </div>

            <div className="border border-[#e5e5e5] rounded-lg p-6">
              <div className="w-10 h-10 bg-[#E8F0FF] rounded-lg flex items-center justify-center mb-4">
                <svg className="w-5 h-5 text-[#2952E8]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[#1a1a1a] mb-2">Manage Wallets</h3>
              <p className="text-sm text-[#6b6b6b]">
                Credit driver wallets, query balances, and initiate payouts. All money is handled in cent-integers with double-entry ledger guarantees.
              </p>
            </div>

            <div className="border border-[#e5e5e5] rounded-lg p-6">
              <div className="w-10 h-10 bg-[#E8F0FF] rounded-lg flex items-center justify-center mb-4">
                <svg className="w-5 h-5 text-[#2952E8]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[#1a1a1a] mb-2">Mock Server</h3>
              <p className="text-sm text-[#6b6b6b]">
                Exercise every endpoint locally without backend credentials. The included mock server returns realistic responses for all SDK methods.
              </p>
            </div>
          </div>

          {/* Code Example */}
          <h3 className="text-xl font-semibold text-[#1a1a1a] mb-4">Quick start</h3>
          <div className="bg-[#1a1a1a] rounded-lg p-6 overflow-x-auto mb-16">
            <pre className="text-sm font-mono text-gray-300 leading-relaxed">
              <code>{`import { Nerava, usd, latLng } from "@nerava/sdk";

const nerava = new Nerava({
  apiKey: "nrv_pk_yourPartnerKey"
});

// Submit a charging session
const session = await nerava.sessions.submit({
  vehicleId: "v_abc",
  chargerId: "c_heights",
  ...latLng(31.0824, -97.6492),
});

// Check available campaigns
const campaigns = await nerava.campaigns.list({
  lat: 31.0824, lng: -97.6492,
});

// Credit a driver wallet
await nerava.wallet.credit({
  driverId: "drv_1",
  amount: usd(500),    // $5.00 in cents
  campaignId: "camp_1",
});`}</code>
            </pre>
          </div>

          {/* SDK Features */}
          <h3 className="text-xl font-semibold text-[#1a1a1a] mb-6">Built for production</h3>
          <div className="grid md:grid-cols-3 gap-6 mb-16">
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">TypeScript-first</h4>
              <p className="text-sm text-[#6b6b6b]">Full type definitions shipped with the package. Every request and response is typed end-to-end.</p>
            </div>
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">Zero dependencies</h4>
              <p className="text-sm text-[#6b6b6b]">Uses native fetch on Node 18.17+. No axios, no node-fetch, no runtime bloat.</p>
            </div>
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">Cent-integer money</h4>
              <p className="text-sm text-[#6b6b6b]">All monetary values are integers in cents. No floating-point dollars at any API boundary.</p>
            </div>
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">Flat error model</h4>
              <p className="text-sm text-[#6b6b6b]">One NeravaError class discriminated by code. No nested error hierarchies to unpack.</p>
            </div>
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">Partner trust tiers</h4>
              <p className="text-sm text-[#6b6b6b]">Hardware-verified, API-verified, and app-reported tiers determine session quality scoring.</p>
            </div>
            <div>
              <h4 className="font-semibold text-[#1a1a1a] mb-1">Idempotent by default</h4>
              <p className="text-sm text-[#6b6b6b]">Every mutating endpoint accepts idempotency keys. Safe to retry without creating duplicates.</p>
            </div>
          </div>
        </div>
      </section>

      {/* Developer Portal CTA */}
      <section className="bg-[#1a1a1a] py-20 px-6">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-3xl font-bold text-white mb-4">Developer portal coming soon</h2>
          <p className="text-gray-400 mb-8">
            API key management, usage dashboards, webhook configuration, and interactive API docs are on the way. In the meantime, reach out to get started with a partner API key.
          </p>
          <a
            href="mailto:james@nerava.network?subject=Nerava%20Partner%20API%20Access"
            className="inline-flex items-center gap-2 bg-[#2952E8] hover:bg-[#1e3fc0] text-white font-semibold px-6 py-3 rounded-lg transition-colors"
          >
            Request API Access
          </a>
          <p className="text-gray-500 text-sm mt-4">
            Or email james@nerava.network directly
          </p>
        </div>
      </section>

      {/* Footer link back */}
      <section className="py-12 px-6 border-t border-[#e5e5e5]">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <Link href="/" className="text-sm text-[#6b6b6b] hover:text-[#1a1a1a] transition-colors">
            &larr; Back to nerava.network
          </Link>
          <div className="flex items-center gap-4 text-sm text-[#6b6b6b]">
            <Link href="/terms" className="hover:text-[#1a1a1a] transition-colors">Terms</Link>
            <Link href="/privacy" className="hover:text-[#1a1a1a] transition-colors">Privacy</Link>
          </div>
        </div>
      </section>
    </main>
  )
}
