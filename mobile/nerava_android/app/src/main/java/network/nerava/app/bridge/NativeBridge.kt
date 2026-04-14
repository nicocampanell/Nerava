package network.nerava.app.bridge

import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebView
import network.nerava.app.BuildConfig
import network.nerava.app.engine.SessionEngine
import network.nerava.app.location.LocationService
import network.nerava.app.auth.SecureTokenStore
import network.nerava.app.notifications.FCMService
import java.net.URI
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Bi-directional bridge between WebView JavaScript and native Android code.
 * Mirrors iOS NativeBridge (WKScriptMessageHandler + evaluateJavaScript).
 *
 * Web → Native: via @JavascriptInterface AndroidBridge.onMessage(json)
 * Native → Web: via webView.evaluateJavascript("window.neravaNativeCallback(...)")
 */
class NativeBridge(
    private val locationService: LocationService,
    private val tokenStore: SecureTokenStore,
) {
    var webView: WebView? = null
    var sessionEngine: SessionEngine? = null

    private val mainHandler = Handler(Looper.getMainLooper())
    private var navigationCommitted = false

    // Debug: last 20 messages for diagnostics
    private val _messageLog = CopyOnWriteArrayList<String>()
    val messageLog: List<String> get() = _messageLog.toList()

    private val allowedOrigins: Set<String> by lazy {
        val origins = mutableSetOf("https://app.nerava.network")
        if (BuildConfig.DEBUG) {
            origins.add("http://localhost:5173")
            origins.add("http://localhost:5174")
            // Android emulator host access
            origins.add("http://10.0.2.2:5173")
            origins.add("http://10.0.2.127:5173")
        }
        origins
    }

    fun setupWebView(webView: WebView) {
        this.webView = webView
        webView.addJavascriptInterface(JsBridge(), "AndroidBridge")
        activeBridge = this

        // Send NATIVE_READY after a short delay (matches iOS DispatchQueue.main.asyncAfter 0.1s)
        mainHandler.postDelayed({ sendToWeb(BridgeMessage.Ready) }, 100)
    }

    fun detach() {
        if (activeBridge === this) activeBridge = null
    }

    fun sendDeviceToken(token: String) {
        sendToWeb(BridgeMessage.DeviceTokenRegistered(token))
    }

    fun didFinishNavigation() {
        navigationCommitted = true
    }

    fun sendToWeb(message: BridgeMessage) {
        val webView = this.webView ?: return

        try {
            val payloadStr = message.toPayloadJson().toString()
            val js = "window.neravaNativeCallback('${message.action}', $payloadStr);"

            if (BuildConfig.DEBUG) {
                logMessage("OUT", message.action, payloadStr)
            }

            mainHandler.post {
                webView.evaluateJavascript(js) { error ->
                    if (error != null && error != "null") {
                        Log.e(TAG, "JS eval error: $error")
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to send message to web: ${e.message}")
        }
    }

    fun injectBridgeScript() {
        val webView = this.webView ?: return
        mainHandler.post {
            webView.evaluateJavascript(BridgeInjector.injectionScript, null)
        }
    }

    private fun isValidOrigin(): Boolean {
        val webView = this.webView ?: return !navigationCommitted
        val url = webView.url ?: return !navigationCommitted

        if (url == "about:blank") return !navigationCommitted

        return try {
            val uri = URI(url)
            val origin = buildString {
                append(uri.scheme ?: "")
                append("://")
                append(uri.host ?: "")
                val port = uri.port
                if (port > 0 && port != 80 && port != 443) {
                    append(":$port")
                }
            }
            allowedOrigins.contains(origin)
        } catch (e: Exception) {
            Log.e(TAG, "Origin validation error: ${e.message}")
            false
        }
    }

    private fun handleAction(raw: String) {
        if (!isValidOrigin()) {
            Log.e(TAG, "Rejected message from unauthorized origin")
            return
        }

        val msg = IncomingBridgeAction.parse(raw)
        if (msg == null) {
            Log.w(TAG, "Failed to parse bridge message")
            return
        }

        if (BuildConfig.DEBUG) {
            logMessage("IN", msg.action, msg.payload.toString())
        }

        val requestId = msg.requestId

        when (msg.action) {
            "SET_CHARGER_TARGET" -> {
                val chargerId = msg.payload.optString("chargerId", "") .takeIf { it.isNotEmpty() } ?: return
                val lat = msg.payload.optDouble("chargerLat", Double.NaN).takeIf { !it.isNaN() } ?: return
                val lng = msg.payload.optDouble("chargerLng", Double.NaN).takeIf { !it.isNaN() } ?: return
                sessionEngine?.setChargerTarget(chargerId, lat, lng)
            }

            "SET_AUTH_TOKEN" -> {
                val token = msg.payload.optString("token", "").takeIf { it.isNotEmpty() } ?: return
                sessionEngine?.setAuthToken(token)
                // Forward existing FCM token after auth (mirrors iOS NativeBridge)
                FCMService.cachedToken?.let { fcmToken ->
                    mainHandler.postDelayed({ sendDeviceToken(fcmToken) }, 1000)
                }
            }

            "EXCLUSIVE_ACTIVATED" -> {
                val sessionId = msg.payload.optString("sessionId", "").takeIf { it.isNotEmpty() } ?: return
                val merchantId = msg.payload.optString("merchantId", "").takeIf { it.isNotEmpty() } ?: return
                val lat = msg.payload.optDouble("merchantLat", Double.NaN).takeIf { !it.isNaN() } ?: return
                val lng = msg.payload.optDouble("merchantLng", Double.NaN).takeIf { !it.isNaN() } ?: return
                sessionEngine?.webConfirmsExclusiveActivated(sessionId, merchantId, lat, lng)
            }

            "VISIT_VERIFIED" -> {
                val sessionId = msg.payload.optString("sessionId", "").takeIf { it.isNotEmpty() } ?: return
                val code = msg.payload.optString("verificationCode", "").takeIf { it.isNotEmpty() } ?: return
                sessionEngine?.webConfirmsVisitVerified(sessionId, code)
            }

            "END_SESSION" -> {
                sessionEngine?.webRequestsSessionEnd()
            }

            "REQUEST_ALWAYS_LOCATION" -> {
                locationService.requestBackgroundPermission()
            }

            "GET_LOCATION" -> {
                val location = locationService.currentLocation
                if (location != null) {
                    sendToWeb(BridgeMessage.LocationResponse(
                        requestId = requestId ?: "",
                        lat = location.latitude,
                        lng = location.longitude,
                        accuracy = location.accuracy.toDouble(),
                    ))
                } else {
                    sendToWeb(BridgeMessage.Error(requestId, "Location unavailable"))
                }
            }

            "GET_SESSION_STATE" -> {
                val engine = sessionEngine
                if (engine != null) {
                    sendToWeb(BridgeMessage.SessionStateChanged(engine.currentState.raw))
                }
            }

            "GET_PERMISSION_STATUS" -> {
                val alwaysGranted = locationService.hasBackgroundPermission
                val status = when {
                    alwaysGranted -> "authorizedAlways"
                    locationService.hasForegroundPermission -> "authorizedWhenInUse"
                    else -> "notDetermined"
                }
                sendToWeb(BridgeMessage.PermissionStatus(
                    requestId = requestId ?: "",
                    status = status,
                    alwaysGranted = alwaysGranted,
                ))
            }

            "GET_AUTH_TOKEN" -> {
                if (requestId == null) return
                val token = tokenStore.getAccessToken()
                sendToWeb(BridgeMessage.AuthTokenResponse(requestId, token))
            }

            "OPEN_EXTERNAL_URL" -> {
                val url = msg.payload.optString("url", "").takeIf { it.isNotEmpty() } ?: return
                try {
                    val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    webView?.context?.startActivity(intent)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to open external URL: $url", e)
                }
            }

            "OPEN_IN_APP_BROWSER" -> {
                val url = msg.payload.optString("url", "").takeIf { it.isNotEmpty() } ?: return
                val uri = Uri.parse(url)
                if (uri.scheme != "http" && uri.scheme != "https") return
                try {
                    val context = webView?.context ?: return
                    val customTabsIntent = androidx.browser.customtabs.CustomTabsIntent.Builder()
                        .setShowTitle(true)
                        .build()
                    customTabsIntent.launchUrl(context, uri)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to open in-app browser, falling back to external: $url", e)
                    try {
                        val intent = Intent(Intent.ACTION_VIEW, uri)
                        webView?.context?.startActivity(intent)
                    } catch (e2: Exception) {
                        Log.e(TAG, "Failed to open URL: $url", e2)
                    }
                }
            }

            "UPDATE_CHARGER_GEOFENCES" -> {
                val chargersJson = msg.payload.optJSONArray("chargers") ?: return
                val chargers = mutableListOf<Triple<String, Double, Double>>()
                for (i in 0 until chargersJson.length()) {
                    val obj = chargersJson.optJSONObject(i) ?: continue
                    val id = obj.optString("id", "").takeIf { it.isNotEmpty() } ?: continue
                    val lat = obj.optDouble("lat", Double.NaN).takeIf { !it.isNaN() } ?: continue
                    val lng = obj.optDouble("lng", Double.NaN).takeIf { !it.isNaN() } ?: continue
                    chargers.add(Triple(id, lat, lng))
                }
                sessionEngine?.updateChargerGeofences(chargers)
            }

            else -> {
                Log.w(TAG, "Unknown bridge action: ${msg.action}")
            }
        }
    }

    private fun logMessage(direction: String, action: String, payload: String) {
        val entry = "$direction | $action | ${payload.take(200)}"
        _messageLog.add(entry)
        while (_messageLog.size > MAX_LOG_ENTRIES) {
            _messageLog.removeAt(0)
        }
        Log.d(TAG, entry)
    }

    /**
     * JavaScript interface exposed to web as `AndroidBridge`.
     * The onMessage method receives JSON strings from window.neravaNative.postMessage/request.
     */
    inner class JsBridge {
        @JavascriptInterface
        fun onMessage(json: String) {
            // @JavascriptInterface runs on a WebView internal thread, dispatch to main
            mainHandler.post { handleAction(json) }
        }
    }

    companion object {
        private const val TAG = "NativeBridge"
        private const val MAX_LOG_ENTRIES = 20

        /** Singleton reference for FCMService to forward tokens without holding Activity. */
        var activeBridge: NativeBridge? = null
            private set
    }
}
