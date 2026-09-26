# Sessions

Sessions turn repeated runs into a coherent local record, using plain files:
no database, no daemon, no background process.

```text
~/.local/share/summarizer/sessions/<name>/
  state.json    machine-readable state and run pointers (the source of truth)
  digest.md     human-readable digest, rendered from state
  digest.json   machine-readable digest
  lock          advisory flock file
```

```sh
summarize session start research       # create/activate; prints how to select it
summarize paper.pdf --profile academic
summarize session status               # sessions, age, run counts, stale warnings
summarize session end                  # finalize the digest and close
```

## Which session does a run record into?

Explicitly and deterministically, in this order:

1. `--no-session` — record nothing (overrides everything);
2. `--session NAME` — that named session;
3. `$SUMMARIZER_SESSION` — the session named in the environment (shell-scoped);
4. otherwise, the single active session whose recorded working directory
   matches the current one;
5. otherwise, nothing is recorded.

Unrelated terminals do not inherit a session by accident: they differ in
working directory, or they do not export the variable. If two active sessions
match one directory, the choice would be a guess, so nothing is recorded and a
warning says to use `--session NAME`. An explicit selection that cannot be
honoured (`--session ghost`) fails before the run, so a typo never costs a
model call. There is no PID tracking and no process monitoring.

## What is stored

Run entries are pointers, not content: timestamp, source, profile, document
type, PDF page count / chunk count where relevant, strategy, output format,
duration, and a one-sentence extract capped at 200 characters. Full model
output, prompt text, and document text are not written to session files.

Session data is local but revealing — it records *which files you summarized
and when* — so keep the sessions root outside synced or shared locations if
that matters to you. Directories are created `0700` and files `0600`.

`summarize session status --format json` emits a stable
`summarizer.session.status.v1` document, separate from the summary JSON schema.
Sessions older than `[session] max_age_hours` (default 24) are flagged in
`session status` and are not closed or deleted automatically. Updates are
serialized with `flock` and written atomically, so Ctrl-C or concurrent runs
cannot corrupt state.
