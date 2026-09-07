"""Offline benchmark scoring commands."""
from pathlib import Path
import typer
from ..benchmark import score_benchmark
from ..errors import KatzError, emit_json, fail
from ..storage import read_json, write_json, sha256_file

benchmark_app = typer.Typer(help="Score adjudicated benchmark evidence; never executes models.")


@benchmark_app.command("score")
def benchmark_score(
    truth: Path = typer.Option(..., "--truth", exists=True, dir_okay=False),
    judgments: Path = typer.Option(..., "--judgments", exists=True, dir_okay=False),
    output: Path = typer.Option(..., "--output"),
) -> None:
    """Score clean and defective papers with explicit coverage and adjudication."""
    try:
        if output.exists():
            raise KatzError("Output already exists", "validation_error")
        result = score_benchmark(read_json(truth), read_json(judgments))
        result["provenance"] = {"truth_sha256": sha256_file(truth), "judgments_sha256": sha256_file(judgments)}
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, result)
        emit_json(result)
    except KatzError as exc:
        fail(exc.message, exc.code, exc.details)
    except (KeyError, TypeError) as exc:
        fail("Malformed benchmark input", "validation_error", {"field": str(exc)})
