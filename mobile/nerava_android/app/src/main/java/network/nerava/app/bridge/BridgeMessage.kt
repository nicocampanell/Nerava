package network.nerava.app.bridge

import org.json.JSONObject

/**
 * Represents messages sent from native to web via neravaNativeCallback().
 * Mirrors iOS NativeBridgeMessage enum.
 */
sealed class BridgeMessage(val action: String) {

    abstract fun toPayloadJson(): JSONObject

    data class SessionStateChanged(val state: String) : BridgeMessage("SESSION_STATE_CHANGED") {
        override fun toPayloadJson() = JSONObject().put("state", state)
    }

    data class PermissionStatus(
        val requestId: String,
        val status: String,
        val alwaysGranted: Boolean,
    ) : BridgeMessage("PERMISSION_STATUS") {
        override fun toPayloadJson() = JSONObject().apply {
            put("requestId", requestId)
            put("status", status)
            put("alwaysGranted", alwaysGranted)
        }
    }

    data class LocationResponse(
        val requestId: String,
        val lat: Double,
        val lng: Double,
        val accuracy: Double,
    ) : BridgeMessage("LOCATION_RESPONSE") {
        override fun toPayloadJson() = JSONObject().apply {
            put("requestId", requestId)
            put("lat", lat)
            put("lng", lng)
            put("accuracy", accuracy)
        }
    }

    data class SessionStartRejected(val reason: String) : BridgeMessage("SESSION_START_REJECTED") {
        override fun toPayloadJson() = JSONObject().put("reason", reason)
    }

    data class Error(val requestId: String?, val message: String) : BridgeMessage("ERROR") {
        override fun toPayloadJson() = JSONObject().apply {
            put("message", message)
            requestId?.let { put("requestId", it) }
        }
    }

    data class EventEmissionFailed(val event: String, val reason: String) : BridgeMessage("EVENT_EMISSION_FAILED") {
        override fun toPayloadJson() = JSONObject().apply {
            put("event", event)
            put("reason", reason)
        }
    }

    data object AuthRequired : BridgeMessage("AUTH_REQUIRED") {
        override fun toPayloadJson() = JSONObject()
    }

    data class AuthTokenResponse(
        val requestId: String,
        val token: String?,
    ) : BridgeMessage("AUTH_TOKEN_RESPONSE") {
        override fun toPayloadJson() = JSONObject().apply {
            put("requestId", requestId)
            put("hasToken", token != null)
            token?.let { put("token", it) }
        }
    }

    data object Ready : BridgeMessage("NATIVE_READY") {
        override fun toPayloadJson() = JSONObject()
    }

    data class DeviceTokenRegistered(val token: String) : BridgeMessage("DEVICE_TOKEN_REGISTERED") {
        override fun toPayloadJson() = JSONObject().put("token", token)
    }

    data class PushDeepLink(val type: String, val deepLink: String) : BridgeMessage("PUSH_DEEP_LINK") {
        override fun toPayloadJson() = JSONObject().apply {
            put("type", type)
            put("deep_link", deepLink)
        }
    }

    data object InAppBrowserClosed : BridgeMessage("IN_APP_BROWSER_CLOSED") {
        override fun toPayloadJson() = JSONObject()
    }
}

/**
 * Represents parsed web → native message (from JavaScript postMessage).
 */
data class IncomingBridgeAction(
    val action: String,
    val payload: JSONObject,
) {
    val requestId: String? get() = payload.optString("requestId", null)

    companion object {
        /**
         * Parse a raw JSON string from the JavaScript interface.
         * Returns null if parsing fails.
         */
        fun parse(raw: String): IncomingBridgeAction? {
            return try {
                val json = JSONObject(raw)
                IncomingBridgeAction(
                    action = json.getString("action"),
                    payload = json.optJSONObject("payload") ?: JSONObject(),
                )
            } catch (e: Exception) {
                null
            }
        }
    }
}
