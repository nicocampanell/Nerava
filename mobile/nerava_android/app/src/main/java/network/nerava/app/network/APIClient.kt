package network.nerava.app.network

import android.util.Log
import network.nerava.app.BuildConfig
import network.nerava.app.engine.SessionConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit
import kotlin.math.pow

/**
 * HTTP client for /v1/native/ endpoints. Mirrors iOS APIClient.
 * - 3-retry exponential backoff with jitter
 * - Idempotent event emission (event_id = idempotency_key)
 * - 401/403 → authRequired callback
 */
class APIClient(
    private val baseUrl: String = BuildConfig.API_BASE_URL,
    private val onAuthRequired: (() -> Unit)? = null,
) {
    // Startup-critical client — aggressive timeouts so blocking config fetches
    // never delay app startup. Play Store reviewers give up on blank screens in ~5 seconds.
    private val startupClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    // Long-lived client — used for session events and background pings which can
    // tolerate longer timeouts since they run after startup.
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()

    @Volatile
    var accessToken: String? = null

    private val iso8601: SimpleDateFormat
        get() = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }

    /**
     * Emit a session event (requires session_id).
     * POST /v1/native/session-events
     */
    @Throws(IOException::class, AuthRequiredException::class)
    fun emitSessionEvent(
        sessionId: String,
        event: String,
        eventId: String,
        occurredAt: Date,
        appState: String,
        metadata: Map<String, String>? = null,
    ) {
        val body = JSONObject().apply {
            put("schema_version", "1.0")
            put("event_id", eventId)
            put("idempotency_key", eventId)
            put("session_id", sessionId)
            put("event", event)
            put("occurred_at", iso8601.format(occurredAt))
            put("timestamp", iso8601.format(Date()))
            put("source", "android_native")
            put("app_state", appState)
            metadata?.let {
                val meta = JSONObject()
                it.forEach { (k, v) -> meta.put(k, v) }
                put("metadata", meta)
            }
        }

        executeWithRetry("$baseUrl/v1/native/session-events", body, eventId, event)
    }

    /**
     * Emit a pre-session event (no session_id).
     * POST /v1/native/pre-session-events
     */
    @Throws(IOException::class, AuthRequiredException::class)
    fun emitPreSessionEvent(
        event: String,
        chargerId: String?,
        eventId: String,
        occurredAt: Date,
        metadata: Map<String, String>? = null,
    ) {
        val body = JSONObject().apply {
            put("schema_version", "1.0")
            put("event_id", eventId)
            put("idempotency_key", eventId)
            put("event", event)
            put("occurred_at", iso8601.format(occurredAt))
            put("timestamp", iso8601.format(Date()))
            put("source", "android_native")
            chargerId?.let { put("charger_id", it) }
            metadata?.let {
                val meta = JSONObject()
                it.forEach { (k, v) -> meta.put(k, v) }
                put("metadata", meta)
            }
        }

        executeWithRetry("$baseUrl/v1/native/pre-session-events", body, eventId, event)
    }

    /**
     * Fire-and-forget background ping to trigger Tesla charging detection.
     * Called when a charger geofence is entered (can be backgrounded/killed).
     * Mirrors iOS APIClient.sendBackgroundPing().
     *
     * @param lat device latitude from geofence trigger
     * @param lng device longitude from geofence trigger
     * @param authToken optional auth token override (for use from BroadcastReceiver
     *                  when the in-memory accessToken may not be set)
     */
    fun sendBackgroundPing(lat: Double, lng: Double, authToken: String? = null) {
        val token = authToken ?: accessToken
        if (token == null) {
            Log.w(TAG, "No auth token for background ping, skipping")
            return
        }

        val body = JSONObject().apply {
            put("lat", lat)
            put("lng", lng)
        }

        val request = Request.Builder()
            .url("$baseUrl/v1/charging-sessions/background-ping")
            .post(body.toString().toRequestBody(jsonType))
            .addHeader("Content-Type", "application/json")
            .addHeader("Authorization", "Bearer $token")
            .build()

        try {
            client.newCall(request).execute().use { response ->
                Log.i(TAG, "Background ping: HTTP ${response.code}")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Background ping failed: ${e.message}")
        }
    }

    /**
     * Fetch runtime config from GET /v1/native/config.
     */
    fun fetchConfig(): SessionConfig {
        val request = Request.Builder()
            .url("$baseUrl/v1/native/config")
            .apply { accessToken?.let { addHeader("Authorization", "Bearer $it") } }
            .build()

        return try {
            startupClient.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val json = JSONObject(response.body?.string() ?: "{}")
                    SessionConfig.fromJson(json)
                } else {
                    Log.w(TAG, "Config fetch failed (${response.code}), using defaults")
                    SessionConfig.DEFAULTS
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Config fetch error, using defaults", e)
            SessionConfig.DEFAULTS
        }
    }

    private fun executeWithRetry(url: String, body: JSONObject, eventId: String, event: String) {
        val bodyStr = body.toString()
        var lastError: Exception? = null

        for (attempt in 0 until MAX_RETRIES) {
            try {
                val request = Request.Builder()
                    .url(url)
                    .post(bodyStr.toRequestBody(jsonType))
                    .addHeader("Content-Type", "application/json")
                    .apply { accessToken?.let { addHeader("Authorization", "Bearer $it") } }
                    .build()

                client.newCall(request).execute().use { response ->
                    when (response.code) {
                        in 200..299 -> {
                            val respBody = response.body?.string()
                            val respJson = try { JSONObject(respBody ?: "{}") } catch (_: Exception) { JSONObject() }
                            if (respJson.optString("status") == "already_processed") {
                                Log.i(TAG, "Event already processed: $event (${eventId.takeLast(6)})")
                            } else {
                                Log.i(TAG, "Event sent: $event (${eventId.takeLast(6)})")
                            }
                            return
                        }
                        401, 403 -> {
                            Log.e(TAG, "Auth error (${response.code}) for event: $event")
                            onAuthRequired?.invoke()
                            throw AuthRequiredException()
                        }
                        429, in 500..599 -> {
                            val delay = BASE_RETRY_DELAY * 2.0.pow(attempt) + Math.random() * 0.5
                            Log.w(TAG, "Retryable error (${response.code}), retrying in ${String.format("%.1f", delay)}s")
                            Thread.sleep((delay * 1000).toLong())
                        }
                        else -> {
                            throw IOException("Request failed with status ${response.code}")
                        }
                    }
                }
            } catch (e: AuthRequiredException) {
                throw e
            } catch (e: Exception) {
                lastError = e
                if (attempt < MAX_RETRIES - 1) {
                    val delay = BASE_RETRY_DELAY * 2.0.pow(attempt)
                    Log.w(TAG, "Network error, retrying in ${String.format("%.1f", delay)}s: ${e.message}")
                    try { Thread.sleep((delay * 1000).toLong()) } catch (_: InterruptedException) {}
                }
            }
        }

        Log.e(TAG, "Event emission failed after $MAX_RETRIES attempts: $event")
        throw lastError ?: IOException("Event emission failed")
    }

    class AuthRequiredException : Exception("Authentication required")

    companion object {
        private const val TAG = "APIClient"
        private const val MAX_RETRIES = 3
        private const val BASE_RETRY_DELAY = 1.0
    }
}
