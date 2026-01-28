# Deprecation Policy

This project follows Semantic Versioning and provides advance notice for breaking changes.

## Policy
- Any public API slated for removal is deprecated for at least one minor release.
- Deprecated APIs emit a `DeprecationWarning` where feasible.
- The changelog will list deprecated items and the earliest removal version.
- Removals only occur in a new minor/major release following the deprecation window.

## Public Surface
Only the documented public surface is supported for external imports. All other modules are internal and may change without notice.
