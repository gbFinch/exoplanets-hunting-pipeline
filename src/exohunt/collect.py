"""Collect all vetted candidates across targets into a single summary JSON."""
from __future__ import annotations

import json
from pathlib import Path


def collect_passed_candidates(
    run_dir: Path,
    iterative_only: bool = False,
    passed_only: bool = True,
) -> dict:
    """Scan all target candidate JSONs in a single run directory.

    Args:
        run_dir: Root run directory.
        iterative_only: If True, only include candidates from iteration >= 1.
        passed_only: If True, only include candidates that passed vetting.

    Returns:
        Summary dict with systems and their candidates.
    """
    results: dict[str, list[dict]] = {}

    for json_path in sorted(run_dir.rglob("candidates/*__bls_*.json")):
        # Skip per-iteration files — use the combined file only
        if "_iter_" in json_path.name:
            continue

        with json_path.open() as f:
            data = json.load(f)

        target = data.get("metadata", {}).get("target", json_path.parent.parent.name)

        for cand in data.get("candidates", []):
            if passed_only and not cand.get("vetting_pass"):
                continue
            if iterative_only and cand.get("iteration", 0) < 1:
                continue

            entry = {
                "period_days": cand["period_days"],
                "depth_ppm": cand["depth_ppm"],
                "snr": cand["snr"],
                "duration_hours": cand["duration_hours"],
                "iteration": cand.get("iteration", 0),
                "rank": cand["rank"],
                "vetting_reasons": cand.get("vetting_reasons", ""),
                "transit_count_observed": cand.get("transit_count_observed"),
                "source_file": str(json_path.relative_to(run_dir)),
            }
            results.setdefault(target, []).append(entry)

    # Sort each system's candidates by iteration then SNR
    for target in results:
        results[target].sort(key=lambda c: (c["iteration"], -c["snr"]))

    summary = {
        "total_systems_with_candidates": len(results),
        "total_candidates": sum(len(v) for v in results.values()),
        "filters": {
            "passed_only": passed_only,
            "iterative_only": iterative_only,
        },
        "systems": results,
    }
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Collect passed BLS candidates across all targets")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="Path to a run directory under outputs/runs/.")
    parser.add_argument("--iterative-only", action="store_true",
                        help="Only include candidates from iteration >= 1 (new discoveries)")
    parser.add_argument("--all", action="store_true",
                        help="Include failed vetting candidates too")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output JSON path (default: <run-dir>/candidates_summary.json)")
    args = parser.parse_args()

    out_path = args.output or args.run_dir / "candidates_summary.json"

    summary = collect_passed_candidates(
        run_dir=args.run_dir,
        iterative_only=args.iterative_only,
        passed_only=not args.all,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=False))

    print(f"Systems with candidates: {summary['total_systems_with_candidates']}")
    print(f"Total candidates: {summary['total_candidates']}")
    print(f"Saved to: {out_path}")

    # Print quick overview
    for target, cands in summary["systems"].items():
        iters = sorted(set(c["iteration"] for c in cands))
        print(f"\n  {target}: {len(cands)} candidate(s), iterations={iters}")
        for c in cands:
            print(f"    iter={c['iteration']} P={c['period_days']:.4f}d "
                  f"depth={c['depth_ppm']:.0f}ppm snr={c['snr']:.1f}")


if __name__ == "__main__":
    main()
