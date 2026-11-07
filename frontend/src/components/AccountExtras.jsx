/**
 * Account extras (placeholder)
 *
 * Single-user, no sign-in: there are no accounts, hence no invites, check-ins, or balance.
 * Navbar skips rendering these, so the empty components are never called.
 *
 * Kept so Navbar's static import resolves at build time.
 */
export function InviteEntry() {
  return null
}

export function AccountMenuExtras() {
  return null
}

export function CheckinHintWatcher() {
  return null
}
