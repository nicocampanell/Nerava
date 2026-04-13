'use client'

import Link from 'next/link'
import { Button } from './Button'

export default function Header() {
  const scrollToSection = (sectionId: string) => {
    const element = document.getElementById(sectionId)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  return (
    <header className="w-full bg-white border-b border-border">
      <div className="max-w-7xl mx-auto px-6 md:px-8 py-4">
        <div className="flex items-center justify-between">
          <a href="/" className="flex items-center">
            <img
              src="/nerava-logo.png"
              alt="Nerava"
              className="h-8 w-auto"
            />
          </a>
          <nav className="hidden md:flex items-center gap-6">
            <a
              href="#drivers"
              onClick={(e) => {
                e.preventDefault()
                scrollToSection('drivers')
              }}
              className="text-foreground hover:text-muted-foreground transition-colors"
            >
              Drivers
            </a>
            <a
              href="#merchants"
              onClick={(e) => {
                e.preventDefault()
                scrollToSection('merchants')
              }}
              className="text-foreground hover:text-muted-foreground transition-colors"
            >
              Merchants
            </a>
            <a
              href="#sponsors"
              onClick={(e) => {
                e.preventDefault()
                scrollToSection('sponsors')
              }}
              className="text-foreground hover:text-muted-foreground transition-colors"
            >
              Sponsors
            </a>
            <Link
              href="/developers"
              className="text-foreground hover:text-muted-foreground transition-colors"
            >
              Developers
            </Link>
            <Button
              variant="primary"
              onClick={() => scrollToSection('download')}
              className="px-4 py-2"
            >
              Download
            </Button>
          </nav>
          <div className="md:hidden">
            <Button
              variant="primary"
              onClick={() => scrollToSection('download')}
              className="px-3 py-1.5 text-sm"
            >
              Download
            </Button>
          </div>
        </div>
      </div>
    </header>
  )
}
