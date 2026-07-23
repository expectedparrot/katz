# EDSL Jobs Workflow

Katz prepares review jobs; EDSL runs them.
There is no generated Python finder script and Katz does not make model calls.

## 1. Build a Jobs package

```bash
katz spotter jobs --output jobs.ep
```

Katz combines the registered manuscript, mapped sections, and enabled spotters.
Section spotters produce one EDSL scenario per selected section.
Holistic spotters produce one scenario containing the complete manuscript.
Each scenario retains the Katz commit, spotter name and scope, source byte range,
spotter instructions, and manuscript content.

Options:

- `--output`, `-o`: destination `.ep` package; defaults to `jobs.ep`.
- `--section ID`: restrict section-scoped spotters to one section.
- `--spotters A,B`: include only the named enabled spotters.
- `--commit SHA`: build from another registered version.

## 2. Inspect and estimate

```bash
ep inspect jobs.ep
ep jobs cost jobs.ep
```

The package contains a standard EDSL `Jobs` object with a structured
`spotter_result` dictionary question.

## 3. Run with EDSL

```bash
ep run jobs.ep --model <model-name> --output results.ep
```

For long-running remote interviews, add `--task-timeout 900`. This controls
the worker deadline for each interview. The separate `--timeout` option only
controls how long the CLI polls with `--background --wait`.

Use `--local` to disable remote inference.
For asynchronous remote inference, use `--background` and later retrieve the
completed object:

```bash
ep jobs results <job-uuid> --output results.ep
```

## 4. Ingest into Katz

```bash
katz results audit results.ep --jobs jobs.ep
katz spotter ingest results.ep --jobs jobs.ep
```

Human-written journal reviews use the same separation between packaging,
execution, and grounded ingestion:

```bash
katz review add review.md --reviewer "Reviewer 2" --round R1
katz review jobs <review-id> --output journal-review.jobs.ep
ep run journal-review.jobs.ep --model <model-name> --task-timeout 900 \
  --output journal-review-results.ep
katz review ingest journal-review-results.ep
```

Katz first proves coverage against the originating Jobs package. A structured
`found=false` answer is a valid negative judgment; a null or malformed answer
is not. Ingestion fails closed for incomplete coverage unless an agent
deliberately passes `--allow-partial`, in which case the run remains visibly
partial and cannot support a clean zero-issue conclusion.

Katz loads the EDSL `Results` object, verifies its embedded manuscript commit
and spotter, and searches for the exact returned quotation inside the original
scenario range.
Valid positive findings become manuscript-anchored draft issues.
Null findings remain preserved in `results.ep`.
Unlocatable quotations are skipped rather than assigned guessed offsets.
Repeated ingestion is idempotent.

Use `--state STATE` to choose another initial issue state or `--commit SHA` to
target another registered version.
