/**
 * Login modal (placeholder)
 *
 * There is no login in this build: the backend short-circuits its auth dependency to a single
 * fixed local user, so no sign-in flow exists.
 *
 * This file only exists so that `lazy(() => import(...))` in App.jsx resolves at build time —
 * Rollup statically analyses dynamic imports and a missing file fails the build.
 * The component is never rendered.
 */
export default function AuthModal() {
  return null
}
