import { useState, useEffect } from 'react'
import { ArrowLeft, Zap, Car, Plug, ChevronRight } from 'lucide-react'

interface VehicleConnectOptionsProps {
  onClose: () => void
  onConnectTesla: () => void
  onConnectSmartcar: () => void
}

export function VehicleConnectOptions({
  onClose,
  onConnectTesla,
  onConnectSmartcar,
}: VehicleConnectOptionsProps) {
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  return (
    <div className="fixed inset-0 z-[4000] bg-white flex flex-col">
      {/* Header */}
      <header className="flex items-center p-4 border-b border-[#E4E6EB]">
        <button
          onClick={onClose}
          className="p-2 -ml-2 hover:bg-gray-100 rounded-full transition-colors"
          aria-label="Go back"
        >
          <ArrowLeft className="w-6 h-6" />
        </button>
        <h1 className="flex-1 text-center font-semibold text-lg">
          Connect Your Vehicle
        </h1>
        <div className="w-10" />
      </header>

      {/* Options */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <p className="text-sm text-[#65676B] mb-2">
          Choose how you want to connect your EV to start earning rewards while charging.
        </p>

        {/* Option 1: Tesla */}
        <button
          onClick={onConnectTesla}
          className="w-full bg-gray-50 rounded-2xl border border-[#E4E6EB] p-4 flex items-center gap-4 hover:bg-gray-100 active:bg-gray-200 transition-colors text-left"
        >
          <div className="w-10 h-10 bg-blue-50 rounded-full flex items-center justify-center flex-shrink-0">
            <Zap className="w-5 h-5 text-blue-600" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-medium text-[#050505]">Tesla</p>
            <p className="text-sm text-[#65676B]">Connect via Tesla Fleet API</p>
          </div>
          <ChevronRight className="w-5 h-5 text-gray-400 flex-shrink-0" />
        </button>

        {/* Option 2: Other EVs via Smartcar */}
        <button
          onClick={onConnectSmartcar}
          className="w-full bg-gray-50 rounded-2xl border border-[#E4E6EB] p-4 flex items-center gap-4 hover:bg-gray-100 active:bg-gray-200 transition-colors text-left"
        >
          <div className="w-10 h-10 bg-green-50 rounded-full flex items-center justify-center flex-shrink-0">
            <Car className="w-5 h-5 text-green-600" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-medium text-[#050505]">Other EVs</p>
            <p className="text-sm text-[#65676B]">Nissan, Ford, Chevy, Rivian, and more</p>
          </div>
          <ChevronRight className="w-5 h-5 text-gray-400 flex-shrink-0" />
        </button>

        {/* Option 3: EVject Hardware */}
        <button
          onClick={() => setToast('EVject connected hardware integration coming soon')}
          className="w-full bg-gray-50 rounded-2xl border border-[#E4E6EB] p-4 flex items-center gap-4 hover:bg-gray-100 active:bg-gray-200 transition-colors text-left"
        >
          <div className="w-10 h-10 bg-orange-50 rounded-full flex items-center justify-center flex-shrink-0">
            <Plug className="w-5 h-5 text-orange-600" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-medium text-[#050505]">EVject Hardware</p>
            <p className="text-sm text-[#65676B]">Connected hardware adapter</p>
          </div>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full flex-shrink-0">
            Coming Soon
          </span>
        </button>
      </div>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-4 right-4 z-[4100] flex justify-center pointer-events-none">
          <div className="bg-gray-900 text-white text-sm px-4 py-3 rounded-xl shadow-lg max-w-sm text-center">
            {toast}
          </div>
        </div>
      )}
    </div>
  )
}
