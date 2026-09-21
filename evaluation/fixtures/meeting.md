# Platform sync — 2026-03-05

Attendees: Maya, Tomas, Priya

## Decisions
- Adopt the new export format in the next release.
- Defer the authentication rewrite until after the beta.

## Action items
- Maya: publish the migration guide by Friday.
- Tomas: add load tests for the export path; due next Wednesday.
- Priya: audit the retention policy and report findings.

## Open questions
- Do we need a compatibility shim for the old export format?
- Who owns the on-call rotation for the beta period?

## Next steps
Ship the beta behind a feature flag, review metrics after two weeks, then
decide on the authentication rewrite.
