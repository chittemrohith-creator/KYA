# Launch status

Verified local demonstration only. NOT approved for public hosting.

## Completed local scope
Map-picked or address-only citizen reports; role workflows; safe completion photo uploads; audited department administration; joint scheduling and delay fixes; map layers/filters. Tests use temporary databases and synthetic accounts.

## Required before public launch
- Choose hosting, domain and real SMS provider. No provider is configured yet.
- Implement real SMS delivery; never expose OTP values in HTTP responses or pages. Add OTP/request and login rate limiting.
- Add CSRF protection to all state-changing forms and cookie-authenticated APIs, with tests.
- Replace default secrets with generated environment secrets; secure session cookies and HTTPS.
- Remove automatic demo-account/database seeding from production. Provision initial administrator and Chairman safely, without known passwords.
- Replace demo phone obfuscation with authenticated encryption and key management.
- Add production WSGI serving, database migrations, persistent upload storage, backups, logging and health checks.
- Independently test complete browser workflows, accessibility, security and restoration before enabling public traffic.

CIVICSYNC_ENV=production intentionally refuses startup until those requirements are implemented and reviewed. Do not bypass this guard to deploy the current demo.

## Local Windows demo
Run start-demo.cmd. Defaults to http://127.0.0.1:5001. Uses a separate local database and synthetic demo accounts.
