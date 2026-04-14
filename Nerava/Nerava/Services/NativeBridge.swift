import WebKit
import UIKit
import Foundation
import CoreLocation
import SafariServices
import os

// MARK: - Message Types

enum NativeBridgeMessage {
    case sessionStateChanged(state: SessionState)
    case permissionStatus(status: String, alwaysGranted: Bool)
    case locationResponse(requestId: String, lat: Double, lng: Double, accuracy: Double)
    case sessionStartRejected(reason: String)
    case error(requestId: String?, message: String)
    case eventEmissionFailed(event: String, reason: String)
    case authRequired
    case authTokenResponse(requestId: String, token: String?)
    case deviceTokenRegistered(token: String)
    case pushDeepLink(type: String, deepLink: String, data: [String: Any])
    case ready
    case inAppBrowserClosed

    var action: String {
        switch self {
        case .sessionStateChanged: return "SESSION_STATE_CHANGED"
        case .permissionStatus: return "PERMISSION_STATUS"
        case .locationResponse: return "LOCATION_RESPONSE"
        case .sessionStartRejected: return "SESSION_START_REJECTED"
        case .error: return "ERROR"
        case .eventEmissionFailed: return "EVENT_EMISSION_FAILED"
        case .authRequired: return "AUTH_REQUIRED"
        case .authTokenResponse: return "AUTH_TOKEN_RESPONSE"
        case .deviceTokenRegistered: return "DEVICE_TOKEN_REGISTERED"
        case .pushDeepLink: return "PUSH_DEEP_LINK"
        case .ready: return "NATIVE_READY"
        case .inAppBrowserClosed: return "IN_APP_BROWSER_CLOSED"
        }
    }

    var payload: [String: Any] {
        switch self {
        case .sessionStateChanged(let state):
            return ["state": state.rawValue]
        case .permissionStatus(let status, let alwaysGranted):
            return ["status": status, "alwaysGranted": alwaysGranted]
        case .locationResponse(let requestId, let lat, let lng, let accuracy):
            return ["requestId": requestId, "lat": lat, "lng": lng, "accuracy": accuracy]
        case .sessionStartRejected(let reason):
            return ["reason": reason]
        case .error(let requestId, let message):
            var p: [String: Any] = ["message": message]
            if let rid = requestId { p["requestId"] = rid }
            return p
        case .eventEmissionFailed(let event, let reason):
            return ["event": event, "reason": reason]
        case .authRequired:
            return [:]
        case .authTokenResponse(let requestId, let token):
            var payload: [String: Any] = ["requestId": requestId, "hasToken": token != nil]
            if let token = token {
                payload["token"] = token
            }
            return payload
        case .deviceTokenRegistered(let token):
            return ["token": token]
        case .pushDeepLink(let type, let deepLink, _):
            return ["type": type, "deep_link": deepLink]
        case .ready:
            return [:]
        case .inAppBrowserClosed:
            return [:]
        }
    }
}

// MARK: - Bridge Implementation

final class NativeBridge: NSObject {
    weak var webView: WKWebView?
    weak var sessionEngine: SessionEngine?
    private let locationService: LocationService
    private var apnsTokenObserver: NSObjectProtocol?
    private var pushDeepLinkObserver: NSObjectProtocol?

    // Exact origin matching (NOT substring)
    // Uses Environment configuration for allowed origins
    private var allowedOrigins: Set<String> {
        return Environment.current.allowedWebOrigins
    }

    /// Track if navigation has committed (origin is now reliable)
    private var navigationCommitted = false

    init(locationService: LocationService) {
        self.locationService = locationService
        super.init()
    }

    deinit {
        if let observer = apnsTokenObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        if let observer = pushDeepLinkObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    var injectionScript: String {
        """
        (function() {
            if (window.neravaNative) return;

            const pendingRequests = new Map();
            let requestCounter = 0;

            window.neravaNative = {
                postMessage: function(action, payload) {
                    window.webkit.messageHandlers.neravaBridge.postMessage({
                        action: action,
                        payload: payload || {}
                    });
                },

                request: function(action, payload) {
                    return new Promise((resolve, reject) => {
                        const requestId = 'req_' + (++requestCounter) + '_' + Date.now();
                        pendingRequests.set(requestId, { resolve, reject, timestamp: Date.now() });

                        window.webkit.messageHandlers.neravaBridge.postMessage({
                            action: action,
                            payload: { ...(payload || {}), requestId: requestId }
                        });

                        setTimeout(() => {
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
                    const { resolve } = pendingRequests.get(payload.requestId);
                    pendingRequests.delete(payload.requestId);
                    resolve(payload);
                    return;
                }

                window.dispatchEvent(new CustomEvent('neravaNative', {
                    detail: { action: action, payload: payload }
                }));
            };

            console.log('[NativeBridge] Initialized');

            // Dispatch ready event for listeners waiting for bridge
            window.dispatchEvent(new CustomEvent('neravaNativeReady'));
        })();
        """
    }

    func setupWebView(_ webView: WKWebView) {
        self.webView = webView

        let script = WKUserScript(
            source: injectionScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        webView.configuration.userContentController.addUserScript(script)
        webView.configuration.userContentController.add(self, name: "neravaBridge")

        // Send native → web ready message after setup (redundant signal for reliability)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            self?.sendToWeb(.ready)
        }

        // Listen for APNs device token and forward to web app
        apnsTokenObserver = NotificationCenter.default.addObserver(
            forName: .didReceiveAPNsToken,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            if let token = notification.userInfo?["token"] as? String {
                self?.sendToWeb(.deviceTokenRegistered(token: token))
            }
        }

        // If token was already received before bridge setup, forward it now
        if let existingToken = NotificationService.shared.apnsDeviceToken {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                self?.sendToWeb(.deviceTokenRegistered(token: existingToken))
            }
        }

        // Listen for push notification deep links and forward to web app
        pushDeepLinkObserver = NotificationCenter.default.addObserver(
            forName: .didReceivePushDeepLink,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            let pushType = notification.userInfo?["type"] as? String ?? "unknown"
            let deepLink = notification.userInfo?["deep_link"] as? String ?? ""
            let data = notification.userInfo as? [String: Any] ?? [:]
            self?.sendToWeb(.pushDeepLink(type: pushType, deepLink: deepLink, data: data))
        }
    }

    /// Call this from WKNavigationDelegate.webView(_:didFinish:) to mark navigation committed
    func didFinishNavigation() {
        navigationCommitted = true
    }

    func sendToWeb(_ message: NativeBridgeMessage) {
        guard let webView = webView else { return }

        do {
            let payloadData = try JSONSerialization.data(withJSONObject: message.payload)
            guard let payloadStr = String(data: payloadData, encoding: .utf8) else { return }

            let js = "window.neravaNativeCallback('\(message.action)', \(payloadStr));"

            DispatchQueue.main.async {
                webView.evaluateJavaScript(js) { _, error in
                    if let error = error {
                        Log.bridge.error("JS error: \(error.localizedDescription)")
                    }
                }
            }
        } catch {
            Log.bridge.error("JSON encoding error: \(error.localizedDescription)")
        }
    }

    /// Validate origin. During bootstrap (before navigation commits), we're lenient.
    /// After navigation commits, we strictly validate.
    private func isValidOrigin(_ webView: WKWebView?) -> Bool {
        guard let url = webView?.url else {
            // URL is nil during bootstrap - allow if navigation hasn't committed yet
            // This handles the case where scripts run before about:blank → real URL
            return !navigationCommitted
        }

        // about:blank during bootstrap
        if url.absoluteString == "about:blank" {
            return !navigationCommitted
        }

        var origin = ""
        if let scheme = url.scheme {
            origin += scheme + "://"
        }
        if let host = url.host {
            origin += host
        }
        if let port = url.port, port != 80 && port != 443 {
            origin += ":\(port)"
        }

        return allowedOrigins.contains(origin)
    }
}

extension NativeBridge: WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard isValidOrigin(webView) else {
            Log.bridge.error("Rejected from unauthorized origin")
            return
        }

        guard let body = message.body as? [String: Any],
              let actionStr = body["action"] as? String,
              let payload = body["payload"] as? [String: Any] else { return }

        let requestId = payload["requestId"] as? String

        switch actionStr {
        case "SET_CHARGER_TARGET":
            guard let chargerId = payload["chargerId"] as? String,
                  let lat = payload["chargerLat"] as? Double,
                  let lng = payload["chargerLng"] as? Double else { return }
            sessionEngine?.setChargerTarget(chargerId: chargerId, lat: lat, lng: lng)

        case "SET_AUTH_TOKEN":
            guard let token = payload["token"] as? String else { return }
            sessionEngine?.setAuthToken(token)
            // If we already have an APNs token, forward it to web now that user is authenticated
            // This handles the case where the token arrived before login
            if let existingToken = NotificationService.shared.apnsDeviceToken {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                    self?.sendToWeb(.deviceTokenRegistered(token: existingToken))
                }
            }

        case "EXCLUSIVE_ACTIVATED":
            guard let sessionId = payload["sessionId"] as? String,
                  let merchantId = payload["merchantId"] as? String,
                  let lat = payload["merchantLat"] as? Double,
                  let lng = payload["merchantLng"] as? Double else { return }
            sessionEngine?.webConfirmsExclusiveActivated(
                sessionId: sessionId,
                merchantId: merchantId,
                merchantLat: lat,
                merchantLng: lng
            )

        case "VISIT_VERIFIED":
            guard let sessionId = payload["sessionId"] as? String,
                  let code = payload["verificationCode"] as? String else { return }
            sessionEngine?.webConfirmsVisitVerified(sessionId: sessionId, verificationCode: code)

        case "END_SESSION":
            sessionEngine?.webRequestsSessionEnd()

        case "REQUEST_ALWAYS_LOCATION":
            locationService.requestAlwaysPermission()

        case "GET_LOCATION":
            if let location = locationService.currentLocation {
                sendToWeb(.locationResponse(
                    requestId: requestId ?? "",
                    lat: location.coordinate.latitude,
                    lng: location.coordinate.longitude,
                    accuracy: location.horizontalAccuracy
                ))
            } else {
                sendToWeb(.error(requestId: requestId, message: "Location unavailable"))
            }

        case "GET_SESSION_STATE":
            // Use public getter to avoid private(set) access issue
            if let engine = sessionEngine {
                sendToWeb(.sessionStateChanged(state: engine.currentState))
            }

        case "GET_PERMISSION_STATUS":
            let status = locationService.authorizationStatus
            let alwaysGranted = status == .authorizedAlways
            sendToWeb(.permissionStatus(status: status.description, alwaysGranted: alwaysGranted))

        case "GET_AUTH_TOKEN":
            guard let requestId = requestId else { return }
            let token = KeychainService.shared.getAccessToken()
            sendToWeb(.authTokenResponse(requestId: requestId, token: token))

        case "OPEN_EXTERNAL_URL":
            guard let urlString = payload["url"] as? String,
                  let url = URL(string: urlString) else { return }
            DispatchQueue.main.async {
                UIApplication.shared.open(url, options: [:], completionHandler: nil)
            }

        case "OPEN_IN_APP_BROWSER":
            guard let urlString = payload["url"] as? String,
                  let url = URL(string: urlString),
                  let scheme = url.scheme,
                  ["http", "https"].contains(scheme) else { return }
            DispatchQueue.main.async { [weak self] in
                guard let windowScene = UIApplication.shared.connectedScenes
                    .compactMap({ $0 as? UIWindowScene })
                    .first(where: { $0.activationState == .foregroundActive }),
                      let keyWindow = windowScene.windows.first(where: { $0.isKeyWindow }),
                      var topVC = keyWindow.rootViewController else { return }
                while let presented = topVC.presentedViewController {
                    topVC = presented
                }
                let config = SFSafariViewController.Configuration()
                config.entersReaderIfAvailable = false
                config.barCollapsingEnabled = true
                let safari = SFSafariViewController(url: url, configuration: config)
                safari.preferredControlTintColor = UIColor(red: 0.16, green: 0.34, blue: 0.91, alpha: 1.0) // #2952E8
                safari.dismissButtonStyle = .close
                safari.delegate = self
                topVC.present(safari, animated: true)
            }

        case "UPDATE_CHARGER_GEOFENCES":
            guard let chargerDicts = payload["chargers"] as? [[String: Any]] else { return }
            let chargers: [(id: String, lat: Double, lng: Double)] = chargerDicts.compactMap { dict in
                guard let id = dict["id"] as? String,
                      let lat = dict["lat"] as? Double,
                      let lng = dict["lng"] as? Double else { return nil }
                return (id: id, lat: lat, lng: lng)
            }
            let targetId = sessionEngine?.currentTargetedCharger?.id
            sessionEngine?.updateChargerGeofences(chargers, targetChargerId: targetId)

        default:
            Log.bridge.warning("Unknown action: \(actionStr)")
        }
    }
}

// MARK: - SFSafariViewControllerDelegate

extension NativeBridge: SFSafariViewControllerDelegate {
    func safariViewControllerDidFinish(_ controller: SFSafariViewController) {
        // User tapped "Done" / closed the in-app browser
        // Notify the web app so it can fire completeDriverOrder or track the close
        sendToWeb(.inAppBrowserClosed)
    }
}
