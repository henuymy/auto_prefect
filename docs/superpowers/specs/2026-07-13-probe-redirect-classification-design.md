# Probe Redirect Classification Design

## Goal

Classify probe HTTP redirects as authentication failures so the session lifetime experiment can advance after a subsystem session expires.

## Behavior

- Any HTTP status from 300 through 399 is an `authentication_failure`.
- HTTP 401 and 403 remain `authentication_failure`.
- HTTP 500 and other unsuccessful non-authentication responses remain `infrastructure_failure`.
- Existing URL, response-body, and JSON authentication markers remain supported.
- Login configuration and credential storage are unchanged.

## Testing

Add focused classification tests for a 302 response and a 500 response, then run the complete runner test module.
