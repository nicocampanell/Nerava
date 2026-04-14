import SwiftUI
import WebKit
import UIKit
import SafariServices
import os

struct WebViewContainer: View {
    @EnvironmentObject private var locationService: LocationService
    @EnvironmentObject private var sessionEngine: SessionEngine
    @ObservedObject private var networkMonitor = NetworkMonitor.shared
    @Binding var pendingDeepLinkURL: URL?
    @State private var isLoading = true
    @State private var loadError: WebViewError?
    @State private var reloadToken = 0

    var body: some View {
        ZStack {
            WebViewRepresentable(
                locationService: locationService,
                sessionEngine: sessionEngine,
                isLoading: $isLoading,
                loadError: $loadError,
                reloadToken: $reloadToken,
                pendingDeepLinkURL: $pendingDeepLinkURL
            )

            if isLoading {
                LoadingOverlay()
            }

            if !networkMonitor.isConnected {
                OfflineOverlay(onRetry: reload)
            } else if let loadError = loadError {
                ErrorOverlay(error: loadError, onRetry: reload)
            }
        }
    }

    private func reload() {
        loadError = nil
        isLoading = true
        reloadToken += 1
    }
}

enum WebViewError: Equatable {
    case network
    case server(statusCode: Int)
    case ssl
    case processTerminated
    case unknown

    var title: String {
        switch self {
        case .network:
            return "Can't Connect"
        case .server:
            return "Server Error"
        case .ssl:
            return "Secure Connection Failed"
        case .processTerminated:
            return "Something Went Wrong"
        case .unknown:
            return "Unable to Load"
        }
    }

    var message: String {
        switch self {
        case .network:
            return "Check your connection and try again."
        case .server(let statusCode):
            return "The server responded with an error (HTTP \(statusCode)). Please try again."
        case .ssl:
            return "We couldn't establish a secure connection. Please try again."
        case .processTerminated:
            return "The web content stopped unexpectedly. Tap Retry to continue."
        case .unknown:
            return "An unexpected error occurred. Please try again."
        }
    }

    var systemImage: String {
        switch self {
        case .network:
            return "wifi.exclamationmark"
        case .server:
            return "exclamationmark.triangle"
        case .ssl:
            return "lock.slash"
        case .processTerminated:
            return "arrow.clockwise"
        case .unknown:
            return "exclamationmark.triangle"
        }
    }
}

private struct LoadingOverlay: View {
    var body: some View {
        ZStack {
            Color("LaunchBackground")
            ProgressView()
                .scaleEffect(1.5)
                .tint(.primary)
                .accessibilityLabel("Loading")
        }
        .ignoresSafeArea()
    }
}

private struct OfflineOverlay: View {
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "wifi.slash")
                .font(.system(size: 40))
                .foregroundColor(.gray)
                .accessibilityHidden(true)
            Text("No internet connection")
                .font(.headline)
                .foregroundColor(.primary)
            Text("Reconnect and tap Retry to continue.")
                .font(.body)
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Button("Retry", action: onRetry)
                .padding(.horizontal, 24)
                .padding(.vertical, 8)
                .background(Color.blue.opacity(0.15))
                .cornerRadius(10)
                .accessibilityHint("Reload the web app")

            if let url = URL(string: "https://nerava.network/privacy") {
                Link("Privacy Policy", destination: url)
                    .font(.footnote)
            }
        }
        .padding(24)
        .background(.ultraThinMaterial)
        .cornerRadius(16)
        .padding()
    }
}

private struct ErrorOverlay: View {
    let error: WebViewError
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: error.systemImage)
                .font(.system(size: 40))
                .foregroundColor(.orange)
                .accessibilityHidden(true)
            Text(error.title)
                .font(.headline)
                .foregroundColor(.primary)
            Text(error.message)
                .font(.body)
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Button("Retry", action: onRetry)
                .padding(.horizontal, 24)
                .padding(.vertical, 8)
                .background(Color.blue.opacity(0.15))
                .cornerRadius(10)
                .accessibilityHint("Reload the web app")

            if let url = URL(string: "https://nerava.network/privacy") {
                Link("Privacy Policy", destination: url)
                    .font(.footnote)
            }
        }
        .padding(24)
        .background(.ultraThinMaterial)
        .cornerRadius(16)
        .padding()
    }
}

struct WebViewRepresentable: UIViewRepresentable {
    let locationService: LocationService
    let sessionEngine: SessionEngine
    @Binding var isLoading: Bool
    @Binding var loadError: WebViewError?
    @Binding var reloadToken: Int
    @Binding var pendingDeepLinkURL: URL?

    func makeCoordinator() -> Coordinator {
        Coordinator(
            locationService: locationService,
            sessionEngine: sessionEngine,
            isLoading: $isLoading,
            loadError: $loadError
        )
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let controller = WKUserContentController()
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.alwaysBounceVertical = true

        let refreshControl = UIRefreshControl()
        refreshControl.addTarget(context.coordinator, action: #selector(Coordinator.handleRefreshControl), for: .valueChanged)
        webView.scrollView.refreshControl = refreshControl

        context.coordinator.webView = webView
        context.coordinator.refreshControl = refreshControl

        // Setup native bridge + injection
        context.coordinator.nativeBridge.setupWebView(webView)
        context.coordinator.nativeBridge.sessionEngine = sessionEngine
        sessionEngine.setWebBridge(context.coordinator.nativeBridge)

        // Load driver app from configured environment
        let url = Environment.current.webAppURL
        let request = URLRequest(url: url)
        context.coordinator.initialRequest = request
        webView.load(request)

        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        // Handle reload requests
        if context.coordinator.lastReloadToken != reloadToken {
            context.coordinator.lastReloadToken = reloadToken
            context.coordinator.reload(uiView)
        }

        // Handle deep link navigation
        if let deepLinkURL = pendingDeepLinkURL {
            let request = URLRequest(url: deepLinkURL)
            uiView.load(request)
            DispatchQueue.main.async {
                self.pendingDeepLinkURL = nil
            }
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, SFSafariViewControllerDelegate {
        private static let internalDomains = ["nerava.network", "localhost", "127.0.0.1"]
        private static let oauthDomains = ["accounts.google.com", "appleid.apple.com",
                                           "connect.stripe.com", "dashboard.stripe.com",
                                           "auth.tesla.com"]

        let nativeBridge: NativeBridge
        var isLoading: Binding<Bool>
        var loadError: Binding<WebViewError?>
        var lastReloadToken: Int = 0
        weak var webView: WKWebView?
        weak var refreshControl: UIRefreshControl?
        var initialRequest: URLRequest?
        private var autoRetryCount: Int = 0
        private let maxAutoRetries: Int = 2

        init(locationService: LocationService,
             sessionEngine: SessionEngine,
             isLoading: Binding<Bool>,
             loadError: Binding<WebViewError?>) {
            self.nativeBridge = NativeBridge(locationService: locationService)
            self.isLoading = isLoading
            self.loadError = loadError
            super.init()
        }

        @objc func handleRefreshControl() {
            guard let webView = webView else { return }
            reload(webView)
        }

        func reload(_ webView: WKWebView) {
            if webView.url == nil, let request = initialRequest {
                webView.load(request)
            } else {
                webView.reload()
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // Ignore popup webview events for main loading state
            if webView == popupWebView { return }
            autoRetryCount = 0
            setLoading(false)
            setError(nil)
            endRefreshing()
            nativeBridge.didFinishNavigation()
            nativeBridge.sendToWeb(.ready)
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            if webView == popupWebView { return }
            setLoading(true)
            setError(nil)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if webView == popupWebView {
                // Dismiss popup on error
                popupWebView?.removeFromSuperview()
                popupWebView = nil
                return
            }
            handleWebError(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            if webView == popupWebView {
                popupWebView?.removeFromSuperview()
                popupWebView = nil
                return
            }
            handleWebError(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            Log.bridge.info("Web content process terminated — auto-reloading")
            endRefreshing()
            setLoading(true)
            // Auto-reload after a brief pause to let the process recover
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                self?.reload(webView)
            }
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            if navigationResponse.isForMainFrame,
               let response = navigationResponse.response as? HTTPURLResponse,
               response.statusCode >= 500 {
                setError(.server(statusCode: response.statusCode))
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            // Handle hostless schemes (tel:, mailto:, etc.) — delegate to system
            guard let host = url.host else {
                if navigationAction.navigationType == .linkActivated {
                    UIApplication.shared.open(url)
                    decisionHandler(.cancel)
                } else {
                    decisionHandler(.allow)
                }
                return
            }

            // Allow internal navigation (suffix match to prevent spoofing)
            if Self.internalDomains.contains(where: { host == $0 || host.hasSuffix(".\($0)") }) {
                decisionHandler(.allow)
                return
            }

            // Allow OAuth domains to proceed (handled by createWebViewWith for popups)
            if Self.oauthDomains.contains(where: { host == $0 || host.hasSuffix(".\($0)") }) {
                decisionHandler(.allow)
                return
            }

            // Only intercept user-initiated link clicks, not programmatic loads
            guard navigationAction.navigationType == .linkActivated else {
                decisionHandler(.allow)
                return
            }

            // External link clicked — open in-app browser instead of Safari
            openInAppBrowser(url: url)
            decisionHandler(.cancel)
        }

        private func openInAppBrowser(url: URL) {
            guard let scheme = url.scheme, ["http", "https"].contains(scheme) else {
                UIApplication.shared.open(url)
                return
            }
            guard let windowScene = UIApplication.shared.connectedScenes
                .compactMap({ $0 as? UIWindowScene })
                .first(where: { $0.activationState == .foregroundActive }),
                  let keyWindow = windowScene.windows.first(where: { $0.isKeyWindow }),
                  var topVC = keyWindow.rootViewController else {
                Log.bridge.warning("Could not find root view controller for in-app browser — falling back to Safari")
                UIApplication.shared.open(url)
                return
            }
            while let presented = topVC.presentedViewController {
                topVC = presented
            }
            let config = SFSafariViewController.Configuration()
            config.entersReaderIfAvailable = false
            config.barCollapsingEnabled = true
            let safari = SFSafariViewController(url: url, configuration: config)
            safari.preferredControlTintColor = UIColor(red: 0.16, green: 0.34, blue: 0.91, alpha: 1.0)
            safari.dismissButtonStyle = .close
            safari.delegate = self
            topVC.present(safari, animated: true)
        }

        func safariViewControllerDidFinish(_ controller: SFSafariViewController) {
            nativeBridge.sendToWeb(.inAppBrowserClosed)
        }

        /// Popup WKWebView for OAuth flows (Stripe Connect, Google Sign-In, etc.)
        private var popupWebView: WKWebView?

        func webView(_ webView: WKWebView,
                     createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction,
                     windowFeatures: WKWindowFeatures) -> WKWebView? {
            guard navigationAction.targetFrame == nil,
                  let url = navigationAction.request.url,
                  let host = url.host else {
                return nil
            }

            // Internal links — load in same WebView
            if Self.internalDomains.contains(where: { host == $0 || host.hasSuffix(".\($0)") }) {
                webView.load(navigationAction.request)
                return nil
            }

            // OAuth/auth popups — open in a child WKWebView so postMessage works
            let isAuthPopup = Self.oauthDomains.contains(where: { host == $0 || host.hasSuffix(".\($0)") })

            if isAuthPopup {
                let popup = WKWebView(frame: webView.bounds, configuration: configuration)
                popup.autoresizingMask = [.flexibleWidth, .flexibleHeight]
                popup.navigationDelegate = self
                popup.uiDelegate = self
                webView.addSubview(popup)
                self.popupWebView = popup
                return popup
            }

            // Other external links — open in-app browser (SFSafariViewController)
            openInAppBrowser(url: url)
            return nil
        }

        /// Dismiss popup WKWebView when it closes itself (window.close())
        func webViewDidClose(_ webView: WKWebView) {
            if webView == popupWebView {
                popupWebView?.removeFromSuperview()
                popupWebView = nil
            }
        }

        func webView(_ webView: WKWebView,
                     runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping () -> Void) {
            presentAlert(title: "Alert", message: message, actions: [
                UIAlertAction(title: "OK", style: .default) { _ in completionHandler() }
            ], fallback: completionHandler)
        }

        func webView(_ webView: WKWebView,
                     runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping (Bool) -> Void) {
            presentAlert(title: "Confirm", message: message, actions: [
                UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) },
                UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) }
            ], fallback: { completionHandler(false) })
        }

        private func handleWebError(_ error: Error) {
            guard let mappedError = classifyError(error) else { return }

            // Auto-retry network/server errors up to maxAutoRetries before showing overlay
            if autoRetryCount < maxAutoRetries, case .network = mappedError,
               let webView = webView {
                autoRetryCount += 1
                Log.bridge.info("Auto-retrying navigation (attempt \(self.autoRetryCount)/\(self.maxAutoRetries))")
                setLoading(true)
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                    self?.reload(webView)
                }
                return
            }

            setLoading(false)
            setError(mappedError)
            endRefreshing()
            Log.bridge.error("Navigation failed: \(error.localizedDescription)")
        }

        private func classifyError(_ error: Error) -> WebViewError? {
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain {
                switch nsError.code {
                case NSURLErrorCancelled:
                    return nil
                case NSURLErrorNotConnectedToInternet,
                     NSURLErrorNetworkConnectionLost,
                     NSURLErrorTimedOut,
                     NSURLErrorCannotFindHost,
                     NSURLErrorCannotConnectToHost,
                     NSURLErrorDNSLookupFailed:
                    return .network
                case NSURLErrorSecureConnectionFailed,
                     NSURLErrorServerCertificateUntrusted,
                     NSURLErrorServerCertificateHasBadDate,
                     NSURLErrorServerCertificateHasUnknownRoot,
                     NSURLErrorServerCertificateNotYetValid:
                    return .ssl
                default:
                    return .network
                }
            }
            return .unknown
        }

        private func setLoading(_ loading: Bool) {
            DispatchQueue.main.async {
                self.isLoading.wrappedValue = loading
            }
        }

        private func setError(_ error: WebViewError?) {
            DispatchQueue.main.async {
                self.loadError.wrappedValue = error
            }
        }

        private func endRefreshing() {
            DispatchQueue.main.async {
                self.refreshControl?.endRefreshing()
            }
        }

        private func presentAlert(title: String, message: String, actions: [UIAlertAction], fallback: @escaping () -> Void) {
            guard let windowScene = UIApplication.shared.connectedScenes
                .compactMap({ $0 as? UIWindowScene })
                .first(where: { $0.activationState == .foregroundActive }),
                  let root = windowScene.windows.first(where: { $0.isKeyWindow })?.rootViewController else {
                fallback()
                return
            }

            let alert = UIAlertController(title: title, message: message, preferredStyle: .alert)
            actions.forEach { alert.addAction($0) }

            var top = root
            while let presented = top.presentedViewController {
                top = presented
            }

            top.present(alert, animated: true)
        }
    }
}
