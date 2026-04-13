/**
 * Analytics event name constants for driver app
 *
 * DO NOT use string literals in components - always import from here
 */

export const DRIVER_EVENTS = {
  // Session
  SESSION_START: 'driver.session.start',

  // Page views
  PAGE_VIEW: 'driver.page.view',

  // OTP flow
  OTP_START: 'driver.otp.start',
  OTP_VERIFY_SUCCESS: 'driver.otp.verify.success',
  OTP_VERIFY_FAIL: 'driver.otp.verify.fail',
  // Button click events (snake_case format for consistency)
  OTP_SEND_CODE_CLICKED: 'driver_otp_send_code_clicked',
  OTP_VERIFY_CLICKED: 'driver_otp_verify_clicked',

  // Intent capture
  INTENT_CAPTURE_REQUEST: 'driver.intent.capture.request',
  INTENT_CAPTURE_SUCCESS: 'driver.intent.capture.success',
  INTENT_CAPTURE_FAIL: 'driver.intent.capture.fail',

  // Exclusive activation
  EXCLUSIVE_ACTIVATE_CLICK: 'driver.exclusive.activate.click',
  EXCLUSIVE_ACTIVATE_CLICKED: 'driver_activate_exclusive_clicked', // snake_case alias
  EXCLUSIVE_ACTIVATE_BLOCKED_OUTSIDE_RADIUS: 'driver.exclusive.activate.blocked_outside_radius',
  EXCLUSIVE_ACTIVATE_SUCCESS: 'driver.exclusive.activate.success',
  EXCLUSIVE_ACTIVATE_FAIL: 'driver.exclusive.activate.fail',

  // Exclusive completion
  EXCLUSIVE_COMPLETE_CLICK: 'driver.exclusive.complete.click',
  EXCLUSIVE_DONE_CLICKED: 'driver_exclusive_done_clicked', // snake_case alias
  EXCLUSIVE_COMPLETE_SUCCESS: 'driver.exclusive.complete.success',
  EXCLUSIVE_COMPLETE_FAIL: 'driver.exclusive.complete.fail',

  // Location
  LOCATION_PERMISSION_GRANTED: 'driver.location.permission.granted',
  LOCATION_PERMISSION_DENIED: 'driver.location.permission.denied',

  // CTAs
  CTA_OPEN_MAPS_CLICK: 'driver.cta.open_maps.click',
  GET_DIRECTIONS_CLICKED: 'driver_get_directions_clicked',
  IM_AT_MERCHANT_CLICKED: 'driver_im_at_merchant_clicked',

  // Favorites
  MERCHANT_FAVORITED: 'driver.merchant.favorited',

  // Share
  MERCHANT_SHARED: 'driver.merchant.shared',

  // Merchant discovery
  MERCHANT_CLICKED: 'driver_merchant_clicked',
  MERCHANT_DETAIL_VIEWED: 'driver_merchant_detail_viewed',

  // Arrival confirmation
  ARRIVAL_DONE_CLICKED: 'driver_arrival_done_clicked',
  ARRIVAL_VERIFIED: 'driver_arrival_verified',
  ARRIVAL_VERIFY_FAILED: 'driver_arrival_verify_failed',

  // Preferences
  PREFERENCES_SUBMIT: 'driver.preferences.submit',
  PREFERENCES_DONE_CLICKED: 'driver_preferences_done_clicked', // snake_case alias

  // EV Arrival
  EV_ARRIVAL_CTA_CLICKED: 'ev_arrival.cta_clicked',
  EV_ARRIVAL_VEHICLE_SETUP: 'ev_arrival.vehicle_setup',
  EV_ARRIVAL_CONFIRMED: 'ev_arrival.confirmed',
  EV_ARRIVAL_ORDER_BOUND: 'ev_arrival.order_bound',
  EV_ARRIVAL_GEOFENCE_TRIGGERED: 'ev_arrival.geofence_triggered',
  EV_ARRIVAL_CANCELED: 'ev_arrival.canceled',
  EV_ARRIVAL_FEEDBACK_SUBMITTED: 'ev_arrival.feedback_submitted',
  EV_ARRIVAL_MODE_CHANGED: 'ev_arrival.mode_changed',
  EV_ARRIVAL_COMPLETED: 'ev_arrival.completed',
  EV_ARRIVAL_ORDER_LINK_CLICKED: 'ev_arrival.order_link_clicked',
  EV_ARRIVAL_ORDER_QUEUED: 'ev_arrival.order_queued',
  EV_ARRIVAL_ORDER_RELEASED: 'ev_arrival.order_released',

  // EV Order Flow
  EV_ORDER_STARTED: 'ev_order.started',
  EV_ORDER_QUEUED: 'ev_order.queued',
  EV_ORDER_RELEASED: 'ev_order.released',

  // Phone-First Check-in (SMS link flow)
  CHECKIN_SESSION_LOADED: 'checkin.session_loaded',
  CHECKIN_SESSION_ACTIVATED: 'checkin.session_activated',
  CHECKIN_LOCATION_VERIFIED: 'checkin.location_verified',
  CHECKIN_COMPLETED: 'checkin.completed',

  // Vehicle setup
  VEHICLE_COLOR_SET: 'vehicle.color_set',

  // Virtual Key
  VIRTUAL_KEY_PROMPT_SHOWN: 'virtual_key.prompt_shown',
  VIRTUAL_KEY_PROMPT_ACCEPTED: 'virtual_key.prompt_accepted',
  VIRTUAL_KEY_PROMPT_SKIPPED: 'virtual_key.prompt_skipped',
  VIRTUAL_KEY_PAIRING_STARTED: 'virtual_key.pairing_started',
  VIRTUAL_KEY_PAIRING_QR_DISPLAYED: 'virtual_key.qr_displayed',
  VIRTUAL_KEY_PAIRING_COMPLETED: 'virtual_key.pairing_completed',
  VIRTUAL_KEY_PAIRING_FAILED: 'virtual_key.pairing_failed',
  VIRTUAL_KEY_PAIRING_TIMEOUT: 'virtual_key.pairing_timeout',
  VIRTUAL_KEY_ARRIVAL_DETECTED: 'virtual_key.arrival_detected',
  VIRTUAL_KEY_ARRIVAL_CONFIRMED: 'virtual_key.arrival_confirmed',
  VIRTUAL_KEY_REVOKED: 'virtual_key.revoked',
  VIRTUAL_KEY_PHONE_HANDOFF_SHOWN: 'virtual_key.phone_handoff_shown',
  VIRTUAL_KEY_PHONE_HANDOFF_SCANNED: 'virtual_key.phone_handoff_scanned',

  // Exclusive Active View
  SHOW_HOST_CLICKED: 'driver.exclusive.show_host_clicked',

  // Home
  HOME_REFRESHED: 'driver.home.refreshed',

  // Charging Sessions
  CHARGING_SESSION_DETECTED: 'charging.session_detected',
  CHARGING_SESSION_ENDED: 'charging.session_ended',
  CHARGING_INCENTIVE_EARNED: 'charging.incentive_earned',
  CHARGING_ACTIVITY_OPENED: 'charging.activity_opened',

  // Charger Detail
  CHARGER_DETAIL_VIEWED: 'driver.charger.detail_viewed',
  CHARGER_DIRECTIONS_CLICKED: 'driver.charger.directions_clicked',
  CHARGER_MERCHANT_CLICKED: 'driver.charger.merchant_clicked',

  // Search
  SEARCH_QUERY: 'driver.search.query',

  // Push Notifications
  DEVICE_TOKEN_REGISTERED: 'driver.device_token.registered',
  PUSH_NOTIFICATION_TAPPED: 'push_notification.tapped',

  // Fleet Telemetry
  TELEMETRY_CONFIGURED: 'telemetry.configured',
  TELEMETRY_CONFIG_FAILED: 'telemetry.config_failed',

  // In-App Browser / Ordering
  IN_APP_BROWSER_OPENED: 'in_app_browser_opened',
} as const

export type DriverEventName = typeof DRIVER_EVENTS[keyof typeof DRIVER_EVENTS]
