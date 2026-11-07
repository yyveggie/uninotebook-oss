// Global configuration constants for the frontend application

/**
 * Polling interval for task status checks (PPT, Poster, Document processing)
 * 🔧 Reduced to 1500ms for smoother progress display during document processing.
 * For long-running tasks like PPT/Poster generation, this still provides good UX.
 */
export const TASK_POLLING_INTERVAL_MS = 1500


/**
 * Polling interval for countdown timers (e.g., email verification countdown)
 * Should remain at 1000ms for accurate second-by-second countdown.
 */
export const COUNTDOWN_INTERVAL_MS = 1000

/**
 * Token refresh interval (5 minutes)
 * This is for background token refresh to keep the session alive.
 */
export const TOKEN_REFRESH_INTERVAL_MS = 5 * 60 * 1000

/**
 * Max upload size for the initial document upload flow.
 * Keep this aligned with nginx client_max_body_size in production.
 */
export const MAX_UPLOAD_FILE_SIZE_MB = 20
export const MAX_UPLOAD_FILE_SIZE_BYTES = MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024
