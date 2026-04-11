package network.nerava.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.View
import android.webkit.*
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.google.firebase.messaging.FirebaseMessaging
import network.nerava.app.auth.SecureTokenStore
import network.nerava.app.bridge.BridgeInjector
import network.nerava.app.bridge.NativeBridge
import network.nerava.app.deeplink.DeepLinkHandler
import network.nerava.app.engine.SessionEngine
import network.nerava.app.location.GeofenceManager
import network.nerava.app.location.LocationService
import network.nerava.app.network.APIClient
import network.nerava.app.notifications.FCMService
import network.nerava.app.webview.WebViewErrorHandler

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var loadingView: View
    private lateinit var errorView: View
    private lateinit var errorMessage: TextView
    private lateinit var retryButton: MaterialButton

    private lateinit var tokenStore: SecureTokenStore
    private lateinit var locationService: LocationService
    private lateinit var geofenceManager: GeofenceManager
    private lateinit var apiClient: APIClient
    private lateinit var bridge: NativeBridge
    private lateinit var sessionEngine: SessionEngine
    private var servicesInitialized = false

    private var pendingDeepLinkUrl: String? = null
    private var autoRetryCount = 0
    private val maxAutoRetries = 2
    private var isRetrying = false

    // Track last requested main-frame URL for reliable retry/crash recovery
    private var currentLoadUrl: String = BuildConfig.WEB_APP_URL

    // WebView load timeout (matches Play Store reviewer patience window)
    private val loadTimeoutHandler = Handler(Looper.getMainLooper())
    private var loadTimeoutRunnable: Runnable? = null
    private val loadTimeoutMs = 10_000L

    // Tracked postDelayed callbacks — must be cancelled in onDestroy to avoid
    // leaking the Activity reference after it's been torn down.
    private val mainHandler = Handler(Looper.getMainLooper())
    private var reloadRunnable: Runnable? = null
    private var sendTokenRunnable: Runnable? = null

    // Permission launchers
    private val locationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val fineGranted = permissions[Manifest.permission.ACCESS_FINE_LOCATION] == true
        if (fineGranted && ::locationService.isInitialized) {
            locationService.startLocationUpdates()
        }
    }

    private val backgroundLocationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        Log.i(TAG, "Background location permission: $granted")
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        Log.i(TAG, "Notification permission: $granted")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Bind views
        webView = findViewById(R.id.webView)
        swipeRefresh = findViewById(R.id.swipeRefresh)
        loadingView = findViewById(R.id.loadingView)
        errorView = findViewById(R.id.errorView)
        errorMessage = errorView.findViewById(R.id.errorMessage)
        retryButton = errorView.findViewById(R.id.retryButton)

        // Initialize services. Failures must NOT block the WebView — the user still
        // needs to see the loading overlay and eventually the web app. Any service
        // failure is logged but execution continues.
        servicesInitialized = initializeServicesSafely()

        // Configure WebView (always — even if services failed so user sees the app)
        configureWebView()

        // Set up pull-to-refresh
        swipeRefresh.setOnRefreshListener {
            webView.reload()
        }

        // Retry button
        retryButton.setOnClickListener {
            retryLoad()
        }

        // Handle deep link (if launched via one)
        handleIntent(intent)

        // Load web app FIRST — before permissions and background init —
        // so the loading overlay is replaced by real content ASAP.
        val deepLinkUrl = pendingDeepLinkUrl
        val url = deepLinkUrl ?: BuildConfig.WEB_APP_URL
        pendingDeepLinkUrl = null
        startLoadTimeout()
        loadMainFrameUrl(url)

        // Request permissions (non-blocking — dialog will appear over loading view)
        if (servicesInitialized) {
            requestInitialPermissions()
        }

        // Start session engine (best-effort, must not block WebView)
        if (servicesInitialized) {
            try {
                sessionEngine.start()
            } catch (e: Exception) {
                Log.e(TAG, "Session engine start failed", e)
            }
        }

        // Register FCM (best-effort)
        try {
            registerFCM()
        } catch (e: Exception) {
            Log.e(TAG, "FCM registration failed", e)
        }
    }

    /**
     * Initialize all native services. Returns true on success, false on any failure.
     * On failure, logs the error but never throws — the WebView must still load so
     * the user (or Play Store reviewer) sees the app, not a blank screen.
     */
    private fun initializeServicesSafely(): Boolean {
        return try {
            tokenStore = (application as NeravaApplication).tokenStore
            locationService = LocationService(this)
            geofenceManager = GeofenceManager(this)
            apiClient = APIClient(
                baseUrl = BuildConfig.API_BASE_URL,
                onAuthRequired = {
                    if (::bridge.isInitialized) {
                        bridge.sendToWeb(network.nerava.app.bridge.BridgeMessage.AuthRequired)
                    }
                }
            )

            // Load saved auth token
            tokenStore.getAccessToken()?.let { apiClient.accessToken = it }

            // Initialize bridge and engine
            bridge = NativeBridge(locationService, tokenStore)
            sessionEngine = SessionEngine(this, locationService, geofenceManager, tokenStore, apiClient)
            bridge.sessionEngine = sessionEngine
            sessionEngine.bridge = bridge

            // Set up location permission callback
            locationService.onRequestBackgroundPermission = { requestBackgroundLocation() }
            true
        } catch (e: Exception) {
            Log.e(TAG, "Service initialization failed — WebView will load without native services", e)
            false
        }
    }

    // MARK: - Load Timeout

    private fun startLoadTimeout() {
        loadTimeoutRunnable?.let { loadTimeoutHandler.removeCallbacks(it) }
        val runnable = Runnable {
            if (loadingView.visibility == View.VISIBLE) {
                Log.w(TAG, "WebView load timed out after ${loadTimeoutMs}ms")
                showError(WebViewErrorHandler.ErrorType.OFFLINE)
            }
        }
        loadTimeoutRunnable = runnable
        loadTimeoutHandler.postDelayed(runnable, loadTimeoutMs)
    }

    private fun cancelLoadTimeout() {
        loadTimeoutRunnable?.let { loadTimeoutHandler.removeCallbacks(it) }
        loadTimeoutRunnable = null
    }

    private fun retryLoad() {
        if (isRetrying) return
        isRetrying = true
        retryButton.isEnabled = false
        // Cancel any queued auto-retry to prevent overlapping navigations
        reloadRunnable?.let { mainHandler.removeCallbacks(it) }
        reloadRunnable = null
        hideError()
        loadingView.visibility = View.VISIBLE
        webView.visibility = View.INVISIBLE
        // Always arm the watchdog explicitly before recovery navigation.
        startLoadTimeout()
        // Use tracked currentLoadUrl — reliable for failed pre-commit navigations
        // where webView.url still points to the previous page.
        loadMainFrameUrl(currentLoadUrl)
    }

    private fun loadMainFrameUrl(url: String) {
        currentLoadUrl = url
        webView.loadUrl(url)
    }

    private fun hideLoadingView() {
        runOnUiThread {
            loadingView.visibility = View.GONE
            webView.visibility = View.VISIBLE
        }
    }

    /**
     * Recover from a dead WebView render process by detaching the dead instance,
     * creating a fresh WebView, reapplying all configuration, and loading the
     * tracked currentLoadUrl. The dead WebView cannot be reloaded — we must
     * replace it entirely.
     */
    private fun recreateWebView() {
        val parent = webView.parent as? android.view.ViewGroup
        val layoutParams = webView.layoutParams
        val index = parent?.indexOfChild(webView) ?: -1

        // Tear down the dead WebView. Don't call destroy() yet — Android docs warn
        // against destroying a WebView whose render process has died. Just clear
        // clients and detach so the GC can reclaim it.
        webView.webViewClient = WebViewClient()
        webView.webChromeClient = null
        if (parent != null && index >= 0) {
            parent.removeView(webView)
        }

        // Create a new WebView and re-attach to the same parent slot
        webView = WebView(this)
        webView.id = R.id.webView
        if (parent != null && index >= 0) {
            parent.addView(webView, index, layoutParams)
        }
        webView.visibility = View.INVISIBLE

        // Show loading overlay so the recreation isn't visible to the user
        loadingView.visibility = View.VISIBLE

        // Reapply all configuration (settings, clients, bridge, file upload handlers).
        // configureWebView() already re-attaches the bridge via bridge.setupWebView(),
        // so we must NOT call it a second time here — that would re-register the JS
        // interface and send a duplicate NATIVE_READY message.
        configureWebView()

        // Arm the watchdog and navigate to the tracked URL
        startLoadTimeout()
        loadMainFrameUrl(currentLoadUrl)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
        pendingDeepLinkUrl?.let { url ->
            // Clear any visible error overlay so the deep link navigation
            // isn't obscured. The loading view will be shown by onPageStarted
            // and dismissed normally on onPageFinished.
            hideError()
            loadingView.visibility = View.VISIBLE
            webView.visibility = View.INVISIBLE
            startLoadTimeout()
            loadMainFrameUrl(url)
            pendingDeepLinkUrl = null
        }
    }

    override fun onPause() {
        super.onPause()
        // Flush cookies so session survives
        CookieManager.getInstance().flush()
    }

    override fun onDestroy() {
        // Cancel any pending file chooser callback so the system doesn't hold
        // onto the Activity via the ValueCallback.
        fileUploadCallback?.onReceiveValue(null)
        fileUploadCallback = null

        cancelLoadTimeout()
        // Cancel any pending delayed callbacks so they don't fire against a
        // destroyed Activity (would leak the reference and can crash).
        reloadRunnable?.let { mainHandler.removeCallbacks(it) }
        reloadRunnable = null
        sendTokenRunnable?.let { mainHandler.removeCallbacks(it) }
        sendTokenRunnable = null
        dismissPopupWebView()
        if (::bridge.isInitialized) {
            bridge.detach()
        }
        if (::sessionEngine.isInitialized) {
            sessionEngine.stop()
        }

        // Detach WebView from parent and clear clients so the lambdas/anonymous
        // inner classes don't keep strong references to the Activity. Note:
        // setting webViewClient = null isn't allowed — use a default empty client.
        webView.webViewClient = WebViewClient()
        webView.webChromeClient = null
        (webView.parent as? android.view.ViewGroup)?.removeView(webView)
        webView.destroy()

        super.onDestroy()
    }

    @Deprecated("Use OnBackPressedCallback")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    // MARK: - WebView Configuration

    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = if (BuildConfig.DEBUG) {
                WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            } else {
                WebSettings.MIXED_CONTENT_NEVER_ALLOW
            }
            userAgentString = "$userAgentString NeravaAndroid/${BuildConfig.VERSION_NAME}"
            mediaPlaybackRequiresUserGesture = false
            useWideViewPort = true
            loadWithOverviewMode = true
            textZoom = 100
            databaseEnabled = true
        }

        // Cookie persistence
        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        // Setup bridge (only if services initialized successfully)
        if (::bridge.isInitialized) {
            bridge.setupWebView(webView)
        }

        webView.webViewClient = object : WebViewClient() {

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                // Sentinel: about:blank is loaded by showError() to wipe the
                // Chromium default error page. Skip all watchdog/bridge work
                // so we don't accidentally start a fresh 10s timer or fire
                // bridge events for a blank page.
                if (url == "about:blank") return
                // Track the URL being loaded — reliable source for retry/recovery
                url?.takeIf { it.isNotBlank() }?.let { currentLoadUrl = it }
                // Start watchdog for EVERY main-frame navigation — covers deep links,
                // redirects, SPA initial loads, and reloads that don't go through
                // an explicit startLoadTimeout() call site.
                startLoadTimeout()
                // Inject bridge script at page start (matches iOS atDocumentStart)
                if (::bridge.isInitialized) {
                    bridge.injectBridgeScript()
                }
            }

            override fun onPageFinished(view: WebView, url: String?) {
                // Sentinel: about:blank is loaded by showError() to wipe the
                // Chromium default error page. Don't hide the loading view
                // (would override INVISIBLE set in showError), don't fire
                // bridge events for a blank page, and don't reset state.
                if (url == "about:blank") return
                cancelLoadTimeout()
                hideLoadingView()
                swipeRefresh.isRefreshing = false
                if (::bridge.isInitialized) {
                    bridge.didFinishNavigation()
                }
                autoRetryCount = 0 // Reset retry counter on successful load
                isRetrying = false
                retryButton.isEnabled = true

                // Re-inject and send NATIVE_READY for SPA navigation
                view.evaluateJavascript(
                    "if(window.neravaNativeCallback) window.neravaNativeCallback('NATIVE_READY', {});",
                    null
                )
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (WebViewErrorHandler.isMainFrameRequest(request)) {
                    val type = WebViewErrorHandler.classifyError(error)
                    // Auto-retry network errors (up to 2 attempts, matches iOS)
                    if (type == WebViewErrorHandler.ErrorType.OFFLINE && autoRetryCount < maxAutoRetries) {
                        autoRetryCount++
                        Log.i(TAG, "Auto-retrying navigation (attempt $autoRetryCount/$maxAutoRetries)")
                        // Cancel the existing watchdog — otherwise the old 10s timeout
                        // can fire during the 1.5s retry backoff and show the offline
                        // overlay even though a retry is already queued.
                        cancelLoadTimeout()
                        // Track the reload Runnable so we can cancel it in onDestroy
                        // if the Activity is torn down while it's still pending.
                        reloadRunnable?.let { mainHandler.removeCallbacks(it) }
                        val runnable = Runnable {
                            // Explicitly arm the watchdog as a safety net in case
                            // the navigation never reaches onPageStarted.
                            startLoadTimeout()
                            // Use tracked currentLoadUrl — view.reload() can retry
                            // a stale URL when the failed navigation never committed.
                            if (currentLoadUrl.isNotBlank()) {
                                loadMainFrameUrl(currentLoadUrl)
                            } else {
                                view.reload()
                            }
                        }
                        reloadRunnable = runnable
                        mainHandler.postDelayed(runnable, 1500)
                        return
                    }
                    cancelLoadTimeout()
                    showError(type)
                }
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError?) {
                // Never proceed on SSL errors in release
                if (BuildConfig.DEBUG) {
                    Log.w(TAG, "SSL error in debug: ${error?.primaryError}")
                    handler.proceed()
                } else {
                    handler.cancel()
                    cancelLoadTimeout()
                    showError(WebViewErrorHandler.ErrorType.SSL)
                }
            }

            override fun onReceivedHttpError(
                view: WebView,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?,
            ) {
                if (WebViewErrorHandler.isMainFrameRequest(request)) {
                    val statusCode = errorResponse?.statusCode ?: return
                    val type = WebViewErrorHandler.classifyHttpError(statusCode)
                    if (type != null) {
                        cancelLoadTimeout()
                        showError(type)
                    }
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val host = url.host

                // Allow navigation within the app's domain.
                // Local dev hosts are only allowed in debug builds — production
                // must never accept localhost or emulator-host URLs.
                val isProductionHost = host == "app.nerava.network"
                val isDevHost = BuildConfig.DEBUG && (
                    host == "localhost" || host == "10.0.2.2" || host == "10.0.2.127"
                )
                if (isProductionHost || isDevHost) {
                    return false
                }

                // External links: open in browser
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to open external URL: $url", e)
                }
                return true
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail?): Boolean {
                Log.e(TAG, "WebView render process gone, recreating WebView")
                cancelLoadTimeout()
                // The WebView's render process has died — we cannot reload the same
                // instance because it's effectively dead. Detach the dead WebView,
                // create a new one, reattach to the parent, reapply all configuration,
                // then load the tracked URL on the new instance.
                recreateWebView()
                return true
            }
        }

        // Handle file uploads, camera, and OAuth popups
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?,
            ): Boolean {
                // Basic file chooser — extend with camera intent if needed
                val intent = fileChooserParams?.createIntent() ?: return false
                try {
                    fileUploadCallback?.onReceiveValue(null)
                    fileUploadCallback = filePathCallback
                    fileUploadLauncher.launch(intent)
                } catch (e: Exception) {
                    fileUploadCallback = null
                    return false
                }
                return true
            }

            // OAuth popup support — mirrors iOS createWebViewWith(configuration:)
            override fun onCreateWindow(
                view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: android.os.Message?
            ): Boolean {
                val transport = resultMsg?.obj as? WebView.WebViewTransport ?: return false

                val popupView = WebView(this@MainActivity).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.userAgentString = view.settings.userAgentString

                    webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                            val url = request.url
                            val host = url.host ?: return false
                            // If callback returns to nerava domain, load in main WebView and close popup.
                            // localhost is only allowed in debug builds — release OAuth callbacks
                            // never redirect to localhost.
                            val isDebugLocalhost = BuildConfig.DEBUG && host == "localhost"
                            if (host.contains("nerava.network") || isDebugLocalhost) {
                                view.loadUrl(url.toString())
                                dismissPopupWebView()
                                return true
                            }
                            return false
                        }
                    }

                    webChromeClient = object : WebChromeClient() {
                        override fun onCloseWindow(window: WebView) {
                            dismissPopupWebView()
                        }
                    }
                }

                popupWebView = popupView
                webView.addView(popupView, android.widget.FrameLayout.LayoutParams(
                    android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                    android.widget.FrameLayout.LayoutParams.MATCH_PARENT
                ))

                transport.webView = popupView
                resultMsg.sendToTarget()
                return true
            }

            override fun onCloseWindow(window: WebView) {
                dismissPopupWebView()
            }
        }

        // Enable popup windows for OAuth flows
        webView.settings.setSupportMultipleWindows(true)
        webView.settings.javaScriptCanOpenWindowsAutomatically = true
    }

    private var popupWebView: WebView? = null

    private fun dismissPopupWebView() {
        popupWebView?.let {
            webView.removeView(it)
            it.destroy()
        }
        popupWebView = null
    }

    private var fileUploadCallback: ValueCallback<Array<Uri>>? = null
    private val fileUploadLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val data = result.data
        val results = if (result.resultCode == RESULT_OK && data != null) {
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, data)
        } else {
            null
        }
        fileUploadCallback?.onReceiveValue(results)
        fileUploadCallback = null
    }

    // MARK: - Error Handling

    private fun showError(type: WebViewErrorHandler.ErrorType) {
        val msgRes = when (type) {
            WebViewErrorHandler.ErrorType.OFFLINE -> R.string.error_offline
            WebViewErrorHandler.ErrorType.SERVER -> R.string.error_server
            WebViewErrorHandler.ErrorType.SSL -> R.string.error_ssl
            WebViewErrorHandler.ErrorType.GENERIC -> R.string.error_generic
        }
        runOnUiThread {
            cancelLoadTimeout()
            // Hide the WebView and clear its content so the Chromium default
            // error page (e.g. "Webpage not available — ERR_NAME_NOT_RESOLVED")
            // doesn't bleed through behind our error overlay. Load about:blank
            // to wipe the failed page out of the WebView's render tree.
            webView.visibility = View.INVISIBLE
            webView.loadUrl("about:blank")
            loadingView.visibility = View.GONE
            errorMessage.setText(msgRes)
            errorView.visibility = View.VISIBLE
            isRetrying = false
            retryButton.isEnabled = true
        }
    }

    private fun hideError() {
        runOnUiThread {
            errorView.visibility = View.GONE
        }
    }

    // MARK: - Permissions

    private fun requestInitialPermissions() {
        // Location (foreground) — show prominent disclosure first
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            showLocationDisclosure()
        } else {
            locationService.startLocationUpdates()
            requestNotificationPermission()
        }
    }

    private fun showLocationDisclosure() {
        AlertDialog.Builder(this)
            .setTitle(R.string.location_permission_title)
            .setMessage(R.string.location_permission_body)
            .setPositiveButton("Continue") { _, _ ->
                locationPermissionLauncher.launch(
                    arrayOf(
                        Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION,
                    )
                )
                requestNotificationPermission()
            }
            .setNegativeButton("Not now") { dialog, _ ->
                dialog.dismiss()
                requestNotificationPermission()
            }
            .setCancelable(false)
            .show()
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun requestBackgroundLocation() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            AlertDialog.Builder(this)
                .setTitle(R.string.background_location_title)
                .setMessage(R.string.background_location_body)
                .setPositiveButton("Continue") { _, _ ->
                    backgroundLocationPermissionLauncher.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                }
                .setNegativeButton("Not now") { dialog, _ ->
                    dialog.dismiss()
                }
                .setCancelable(false)
                .show()
        }
    }

    // MARK: - FCM

    private fun registerFCM() {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                val token = task.result
                Log.i(TAG, "FCM token: ${token.take(10)}...")
                if (::tokenStore.isInitialized) {
                    tokenStore.setFCMToken(token)
                }
                FCMService.cachedToken = token
                // Forward to web bridge after delay (bridge may not be ready yet).
                // Track the Runnable so we can cancel it in onDestroy — otherwise
                // a delayed callback could fire against a destroyed Activity/bridge.
                sendTokenRunnable?.let { mainHandler.removeCallbacks(it) }
                val runnable = Runnable {
                    if (::bridge.isInitialized) {
                        bridge.sendDeviceToken(token)
                    }
                }
                sendTokenRunnable = runnable
                mainHandler.postDelayed(runnable, 2000)
            } else {
                Log.w(TAG, "FCM token fetch failed", task.exception)
            }
        }
    }

    // MARK: - Deep Links

    private fun handleIntent(intent: Intent?) {
        val url = DeepLinkHandler.resolveWebUrl(intent, BuildConfig.WEB_APP_URL)
        if (url != null) {
            pendingDeepLinkUrl = url
            Log.i(TAG, "Deep link resolved: $url")
        }
    }

    // MARK: - Debug

    fun launchDiagnostics() {
        if (BuildConfig.DEBUG) {
            startActivity(Intent(this, network.nerava.app.debug.BridgeDiagnosticsActivity::class.java))
        }
    }

    companion object {
        private const val TAG = "MainActivity"
    }
}
