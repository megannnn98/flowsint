"""
Operator CLI: run the extraction on one PDF outside the UI.

    OPENROUTER_API_KEY=... HIMERA_ALLOW_SEND=1 \\
        python -m flowsint_core.himera report.pdf --out result.json [--gold gold.json]

The PDF is sent to OpenRouter, so HIMERA_ALLOW_SEND=1 must be set explicitly.
Only counts/metrics are printed; the full result (with personal data) goes to
--out, which the operator must keep out of the repository.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .config import HimeraSettings
from .extraction import HimeraExtractionService
from .metrics import evaluate, summary
from .openrouter import OpenRouterClient


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m flowsint_core.himera")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, help="write the full result JSON here")
    parser.add_argument("--gold", type=Path, help="gold JSON for quality metrics")
    args = parser.parse_args()

    if os.environ.get("HIMERA_ALLOW_SEND") != "1":
        print(
            "Refusing to send the PDF to OpenRouter: set HIMERA_ALLOW_SEND=1 to confirm.",
            file=sys.stderr,
        )
        return 2
    settings = HimeraSettings.from_env()
    client = OpenRouterClient(
        os.environ.get("OPENROUTER_API_KEY", ""),
        timeout_s=settings.timeout_s,
        max_retries=settings.max_retries,
        backoff_base_s=settings.backoff_base_s,
    )
    result = asyncio.run(
        HimeraExtractionService(client, settings).extract(args.pdf.read_bytes())
    )
    report = {"summary": summary(result)}
    if args.gold:
        report["metrics"] = evaluate(result, json.loads(args.gold.read_text("utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
