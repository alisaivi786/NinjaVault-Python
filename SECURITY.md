# Security Policy

Please do not open a public issue for security problems. Report them privately via
GitHub's "Report a vulnerability" (Security tab) on this repository.

Never include a real CDN API key in an issue, pull request, test, or sample. Keys are
backend-only secrets; rotate any key that was exposed. Use placeholders such as `cdn_xxxxx`.

The SDK never logs the API key or file contents. If you add your own `event_hooks` or
`httpx` logging, redact the `X-Api-Key` request header before writing headers anywhere.

## Supported versions

Only the latest released version of `ninjavault-cdn` receives security fixes.
