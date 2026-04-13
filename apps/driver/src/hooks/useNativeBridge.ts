import { useEffect, useCallback, useState, useRef } from 'react';

interface NativeLocation {
  lat: number;
  lng: number;
  accuracy: number;
}

type SessionState =
  | 'IDLE'
  | 'NEAR_CHARGER'
  | 'ANCHORED'
  | 'SESSION_ACTIVE'
  | 'IN_TRANSIT'
  | 'AT_MERCHANT'
  | 'SESSION_ENDED';

interface PermissionStatus {
  status: string;
  alwaysGranted: boolean;
}

interface AuthTokenResponse {
  requestId: string;
  hasToken?: boolean;
  token?: string;
}

declare global {
  interface Window {
    neravaNative?: {
      postMessage: (action: string, payload: any) => void;
      request: (action: string, payload: any) => Promise<any>;
      setChargerTarget: (chargerId: string, chargerLat: number, chargerLng: number) => void;
      setAuthToken: (token: string) => void;
      confirmExclusiveActivated: (sessionId: string, merchantId: string, merchantLat: number, merchantLng: number) => void;
      confirmVisitVerified: (sessionId: string, verificationCode: string) => void;
      endSession: () => void;
      requestAlwaysLocation: () => void;
      getLocation: () => Promise<NativeLocation>;
      getSessionState: () => Promise<{ state: SessionState }>;
      getPermissionStatus: () => Promise<PermissionStatus>;
      getAuthToken: () => Promise<AuthTokenResponse>;
      openExternalUrl: (url: string) => void;
      openInAppBrowser?: (url: string) => void;
    };
  }
}

const NATIVE_BRIDGE_ENABLED = import.meta.env.VITE_NATIVE_BRIDGE_ENABLED !== 'false';

/**
 * Check if native bridge object exists right now.
 */
function bridgeExists(): boolean {
  return NATIVE_BRIDGE_ENABLED && !!window.neravaNative;
}

export function useNativeBridge() {
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  // bridgeReady is stateful - set via ready signals
  const [bridgeReady, setBridgeReady] = useState(bridgeExists());
  const initializedRef = useRef(false);

  // Listen for bridge ready - BOTH JS event AND native NATIVE_READY message
  useEffect(() => {
    if (!NATIVE_BRIDGE_ENABLED) return;

    // Check immediately
    if (bridgeExists()) {
      setBridgeReady(true);
    }

    // Listen for JS-dispatched ready event (from injection script)
    const handleJsReady = () => {
      if (bridgeExists()) {
        setBridgeReady(true);
      }
    };
    window.addEventListener('neravaNativeReady', handleJsReady);

    // Listen for native → web NATIVE_READY message
    // Set bridgeReady true unconditionally - the native sent it, so bridge exists
    const handleNativeMessage = (event: CustomEvent<{ action: string; payload: any }>) => {
      if (event.detail.action === 'NATIVE_READY') {
        setBridgeReady(true);  // Trust the native signal
      }
    };
    window.addEventListener('neravaNative', handleNativeMessage as EventListener);

    return () => {
      window.removeEventListener('neravaNativeReady', handleJsReady);
      window.removeEventListener('neravaNative', handleNativeMessage as EventListener);
    };
  }, []);

  // Listen for state changes from native
  useEffect(() => {
    if (!bridgeReady) return;

    const handleNativeEvent = (event: CustomEvent<{ action: string; payload: any }>) => {
      const { action, payload } = event.detail;

      if (action === 'SESSION_STATE_CHANGED') {
        setSessionState(payload.state);
      }

      if (action === 'SESSION_START_REJECTED') {
        console.warn('[NativeBridge] Session start rejected:', payload.reason);
        window.dispatchEvent(new CustomEvent('nerava:session-rejected', { detail: { reason: payload.reason } }));
      }

      if (action === 'AUTH_REQUIRED') {
        console.warn('[NativeBridge] Auth required - token may be expired');
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        window.dispatchEvent(new CustomEvent('nerava:auth-required'));
      }

      if (action === 'EVENT_EMISSION_FAILED') {
        // REQUIRED: Log both payload.event and payload.reason for debugging
        console.error('[NativeBridge] Event emission failed:', payload.event, payload.reason);
        // Optional: Send to analytics/error tracking service
      }

      if (action === 'PUSH_DEEP_LINK') {
        // Forward push notification deep link to the web app
        window.dispatchEvent(new CustomEvent('nerava:push-deep-link', {
          detail: { type: payload.type, deep_link: payload.deep_link, data: payload },
        }));
      }
    };

    window.addEventListener('neravaNative', handleNativeEvent as EventListener);

    // Get initial state
    window.neravaNative?.getSessionState().then(({ state }) => setSessionState(state));

    return () => {
      window.removeEventListener('neravaNative', handleNativeEvent as EventListener);
    };
  }, [bridgeReady]);

  // Sync initial auth token (once)
  useEffect(() => {
    if (!bridgeReady || initializedRef.current) return;
    initializedRef.current = true;

    const token = localStorage.getItem('access_token');
    if (token) {
      window.neravaNative?.setAuthToken(token);
      return;
    }

    window.neravaNative?.getAuthToken()
      .then((payload) => {
        if (payload?.hasToken && payload.token) {
          localStorage.setItem('access_token', payload.token);
          window.neravaNative?.setAuthToken(payload.token);
        }
      })
      .catch(() => {});
  }, [bridgeReady]);

  // Listen for cross-tab storage changes only
  useEffect(() => {
    if (!bridgeReady) return;

    const handleStorage = (e: StorageEvent) => {
      if (e.key === 'access_token' && e.newValue) {
        window.neravaNative?.setAuthToken(e.newValue);
      }
    };

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [bridgeReady]);

  const setChargerTarget = useCallback((chargerId: string, chargerLat: number, chargerLng: number) => {
    if (bridgeExists()) {
      window.neravaNative?.setChargerTarget(chargerId, chargerLat, chargerLng);
    }
  }, []);

  /**
   * CRITICAL: Call this explicitly after login or token refresh.
   * The storage event listener only fires for cross-tab changes.
   */
  const setAuthToken = useCallback((token: string) => {
    if (bridgeExists()) {
      window.neravaNative?.setAuthToken(token);
    }
  }, []);

  const confirmExclusiveActivated = useCallback((
    sessionId: string,
    merchantId: string,
    merchantLat: number,
    merchantLng: number
  ) => {
    if (bridgeExists()) {
      window.neravaNative?.confirmExclusiveActivated(sessionId, merchantId, merchantLat, merchantLng);
    }
  }, []);

  const confirmVisitVerified = useCallback((sessionId: string, verificationCode: string) => {
    if (bridgeExists()) {
      window.neravaNative?.confirmVisitVerified(sessionId, verificationCode);
    }
  }, []);

  const endSession = useCallback(() => {
    if (bridgeExists()) {
      window.neravaNative?.endSession();
    }
  }, []);

  const requestAlwaysLocation = useCallback(() => {
    if (bridgeExists()) {
      window.neravaNative?.requestAlwaysLocation();
    }
  }, []);

  const getLocation = useCallback(async (): Promise<NativeLocation> => {
    if (bridgeExists()) {
      return window.neravaNative!.getLocation();
    }
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
        reject
      );
    });
  }, []);

  const getPermissionStatus = useCallback(async (): Promise<PermissionStatus> => {
    if (bridgeExists()) {
      return window.neravaNative!.getPermissionStatus();
    }
    return { status: 'notAvailable', alwaysGranted: false };
  }, []);

  const getAuthToken = useCallback(async (): Promise<AuthTokenResponse | null> => {
    if (bridgeExists()) {
      return window.neravaNative!.getAuthToken();
    }
    return null;
  }, []);

  /**
   * Open a URL in an in-app browser (SFSafariViewController on iOS,
   * Chrome Custom Tab on Android). Falls back to window.open when the
   * native bridge method is unavailable (web browser or old app version).
   */
  const openInAppBrowser = useCallback((url: string) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return;
    } catch { return; }

    if (bridgeExists() && window.neravaNative?.openInAppBrowser) {
      window.neravaNative.openInAppBrowser(url);
    } else {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }, []);

  return {
    isNative: bridgeReady,
    sessionState,
    setChargerTarget,
    setAuthToken,
    confirmExclusiveActivated,
    confirmVisitVerified,
    endSession,
    requestAlwaysLocation,
    getLocation,
    getPermissionStatus,
    getAuthToken,
    openInAppBrowser,
  };
}
