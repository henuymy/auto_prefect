# City Ops Probe Retry Design

## Goal

Avoid failing a dashboard collection when the city_ops health probe encounters a single transient network failure, without retrying authentication failures or starting an unnecessary login.

## Scope

Only the existing `city_ops` stage probe changes. The probe makes at most two HTTP requests per validation: its initial request and one retry.

## Request Policy

- Use a connect timeout of 2 seconds and a read timeout of 5 seconds.
- Retry once after a 0.5-second delay only for `requests.exceptions.RequestException`.
- Do not retry an HTTP response, including 302, 401, or 403. Existing response classification remains responsible for deciding whether a session must be refreshed.
- If both attempts raise a request exception, return the existing `probe_error` result so session preparation continues to classify it as infrastructure failure.

## Diagnostics

The probe result records only the safe exception class name, such as `ConnectTimeout` or `ReadTimeout`. It must not include request headers, cookies, tokens, request bodies, or raw exception text.

## Tests

- A transient request exception followed by a successful response performs exactly two attempts and returns a successful probe result.
- A persistent request exception performs exactly two attempts and returns `probe_error` with a safe exception class name.
- An HTTP authentication response performs one attempt and retains its existing authentication classification.

