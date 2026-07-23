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

Use `--local` to disable remote inference.
For asynchronous remote inference, use `--background` and later retrieve the
completed object:

```bash
ep jobs results <job-uuid> --output results.ep
```

## 4. Ingest into Katz

```bash
katz spotter ingest results.ep
```

Katz loads the EDSL `Results` object, verifies its embedded manuscript commit
and spotter, and searches for the exact returned quotation inside the original
scenario range.
Valid positive findings become manuscript-anchored draft issues.
Null findings remain preserved in `results.ep`.
Unlocatable quotations are skipped rather than assigned guessed offsets.
Repeated ingestion is idempotent.

Use `--state STATE` to choose another initial issue state or `--commit SHA` to
target another registered version.
