package network.nerava.app.bridge

/**
 * Provides the JavaScript injection script that creates window.neravaNative.
 *
 * This is the EXACT same script as the iOS WKUserScript, with one difference:
 * instead of window.webkit.messageHandlers.neravaBridge.postMessage(),
 * it calls AndroidBridge.onMessage() (the @JavascriptInterface method).
 *
 * The web app sees the same window.neravaNative API regardless of platform.
 */
object BridgeInjector {

    /**
     * Returns the JavaScript to inject at document start.
     * Must be called via webView.evaluateJavascript() in onPageStarted().
     */
    val injectionScript: String = """
        (function() {
            if (window.neravaNative) return;

            const pendingRequests = new Map();
            let requestCounter = 0;

            window.neravaNative = {
                postMessage: function(action, payload) {
                    try {
                        AndroidBridge.onMessage(JSON.stringify({
                            action: action,
                            payload: payload || {}
                        }));
                    } catch(e) {
                        console.error('[NativeBridge] postMessage error:', e);
                    }
                },

                request: function(action, payload) {
                    return new Promise(function(resolve, reject) {
                        var requestId = 'req_' + (++requestCounter) + '_' + Date.now();
                        pendingRequests.set(requestId, { resolve: resolve, reject: reject, timestamp: Date.now() });

                        try {
                            AndroidBridge.onMessage(JSON.stringify({
                                action: action,
                                payload: Object.assign({}, payload || {}, { requestId: requestId })
                            }));
                        } catch(e) {
                            pendingRequests.delete(requestId);
                            reject(e);
                            return;
                        }

                        setTimeout(function() {
                            if (pendingRequests.has(requestId)) {
                                pendingRequests.delete(requestId);
                                reject(new Error('Request timeout'));
                            }
                        }, 10000);
                    });
                },

                setChargerTarget: function(chargerId, chargerLat, chargerLng) {
                    this.postMessage('SET_CHARGER_TARGET', {
                        chargerId: chargerId,
                        chargerLat: chargerLat,
                        chargerLng: chargerLng
                    });
                },

                setAuthToken: function(token) {
                    this.postMessage('SET_AUTH_TOKEN', { token: token });
                },

                confirmExclusiveActivated: function(sessionId, merchantId, merchantLat, merchantLng) {
                    this.postMessage('EXCLUSIVE_ACTIVATED', {
                        sessionId: sessionId,
                        merchantId: merchantId,
                        merchantLat: merchantLat,
                        merchantLng: merchantLng
                    });
                },

                confirmVisitVerified: function(sessionId, verificationCode) {
                    this.postMessage('VISIT_VERIFIED', {
                        sessionId: sessionId,
                        verificationCode: verificationCode
                    });
                },

                endSession: function() {
                    this.postMessage('END_SESSION', {});
                },

                requestAlwaysLocation: function() {
                    this.postMessage('REQUEST_ALWAYS_LOCATION', {});
                },

                getLocation: function() {
                    return this.request('GET_LOCATION', {});
                },

                getSessionState: function() {
                    return this.request('GET_SESSION_STATE', {});
                },

                getPermissionStatus: function() {
                    return this.request('GET_PERMISSION_STATUS', {});
                },

                getAuthToken: function() {
                    return this.request('GET_AUTH_TOKEN', {});
                },

                openExternalUrl: function(url) {
                    this.postMessage('OPEN_EXTERNAL_URL', { url: url });
                },

                openInAppBrowser: function(url) {
                    this.postMessage('OPEN_IN_APP_BROWSER', { url: url });
                },

                updateChargerGeofences: function(chargers) {
                    this.postMessage('UPDATE_CHARGER_GEOFENCES', { chargers: chargers });
                }
            };

            window.neravaNativeCallback = function(action, payload) {
                if (payload && payload.requestId && pendingRequests.has(payload.requestId)) {
                    var entry = pendingRequests.get(payload.requestId);
                    pendingRequests.delete(payload.requestId);
                    entry.resolve(payload);
                    return;
                }

                window.dispatchEvent(new CustomEvent('neravaNative', {
                    detail: { action: action, payload: payload }
                }));
            };

            console.log('[NativeBridge] Initialized (Android)');

            window.dispatchEvent(new CustomEvent('neravaNativeReady'));
        })();
    """.trimIndent()
}
