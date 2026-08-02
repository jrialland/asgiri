"""
Analyze Locust benchmark CSV outputs in .benchmarks/.

Usage:
    uv run python scripts/analyze_benchmarks.py
    uv run python scripts/analyze_benchmarks.py --server uvicorn --server asgiri
    uv run python scripts/analyze_benchmarks.py --plot output.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def find_root() -> Path:
    """Find the project root directory."""
    current = Path(__file__).parent
    while current != current.parent:
        if "pyproject.toml" in [p.name for p in current.iterdir()]:
            return current
        current = current.parent
    raise RuntimeError("Project root not found.")


project_root = find_root()
BENCHMARK_DIR = project_root / ".benchmarks"


def available_servers() -> list[str]:
    """Return the list of server names that have benchmark stats files."""
    if not BENCHMARK_DIR.exists():
        return []
    servers = []
    for f in BENCHMARK_DIR.glob("benchmark_*_stats.csv"):
        stem = f.stem  # e.g. "benchmark_asgiri_stats"
        if stem.startswith("benchmark_") and stem.endswith("_stats"):
            servers.append(stem[len("benchmark_") : -len("_stats")])
    return sorted(servers)


def read_stats(server: str) -> dict:
    """Read a server's stats CSV and return per-endpoint rows plus the aggregate."""
    path = BENCHMARK_DIR / f"benchmark_{server}_stats.csv"
    if not path.exists():
        raise FileNotFoundError(f"No stats file for server '{server}': {path}")

    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        for line in f:
            line = line.strip()
            if not line:
                continue
            values = line.split(",")
            rows.append(dict(zip(header, values)))

    endpoints = [r for r in rows if r.get("Name") and r.get("Name") != "Aggregated"]
    aggregate = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    return {"endpoints": endpoints, "aggregate": aggregate}


def fmt_float(value: str | None, default: str = "N/A") -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return default


def print_summary_table(servers: list[str]) -> None:
    """Print a comparison table of aggregate metrics across servers."""
    print("\nAggregate performance comparison")
    print("=" * 95)
    print(
        f"{'Server':<12} | {'Reqs':>6} | {'RPS':>7} | {'Avg ms':>8} | "
        f"{'Median ms':>9} | {'p50':>6} | {'p95':>6} | {'p99':>6} | {'Failures':>8}"
    )
    print("-" * 95)

    for server in servers:
        data = read_stats(server)
        agg = data["aggregate"]
        if not agg:
            print(f"{server:<12} | no aggregate row found")
            continue

        print(
            f"{server:<12} | "
            f"{int(float(agg.get('Request Count', 0))):>6} | "
            f"{fmt_float(agg.get('Requests/s')):>7} | "
            f"{fmt_float(agg.get('Average Response Time')):>8} | "
            f"{fmt_float(agg.get('Median Response Time')):>9} | "
            f"{fmt_float(agg.get('50%')):>6} | "
            f"{fmt_float(agg.get('95%')):>6} | "
            f"{fmt_float(agg.get('99%')):>6} | "
            f"{int(float(agg.get('Failure Count', 0))):>8}"
        )


def print_endpoint_table(servers: list[str], endpoint: str) -> None:
    """Print a comparison table for a single endpoint."""
    print(f"\nEndpoint: {endpoint}")
    print("=" * 95)
    print(
        f"{'Server':<12} | {'Reqs':>6} | {'RPS':>7} | {'Avg ms':>8} | "
        f"{'Median ms':>9} | {'p50':>6} | {'p95':>6} | {'p99':>6} | {'Failures':>8}"
    )
    print("-" * 95)

    for server in servers:
        data = read_stats(server)
        row = next(
            (r for r in data["endpoints"] if r.get("Name") == endpoint), None
        )
        if not row:
            print(f"{server:<12} | endpoint not found")
            continue

        print(
            f"{server:<12} | "
            f"{int(float(row.get('Request Count', 0))):>6} | "
            f"{fmt_float(row.get('Requests/s')):>7} | "
            f"{fmt_float(row.get('Average Response Time')):>8} | "
            f"{fmt_float(row.get('Median Response Time')):>9} | "
            f"{fmt_float(row.get('50%')):>6} | "
            f"{fmt_float(row.get('95%')):>6} | "
            f"{fmt_float(row.get('99%')):>6} | "
            f"{int(float(row.get('Failure Count', 0))):>8}"
        )


def print_failures(servers: list[str]) -> None:
    """Print any failure and exception records per server."""
    print("\nFailures / exceptions")
    print("=" * 60)
    for server in servers:
        failures_path = BENCHMARK_DIR / f"benchmark_{server}_failures.csv"
        exceptions_path = BENCHMARK_DIR / f"benchmark_{server}_exceptions.csv"

        failure_rows = []
        if failures_path.exists():
            with failures_path.open("r", encoding="utf-8") as f:
                header = f.readline().strip().split(",")
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    failure_rows.append(dict(zip(header, line.split(","))))

        exception_rows = []
        if exceptions_path.exists():
            with exceptions_path.open("r", encoding="utf-8") as f:
                header = f.readline().strip().split(",")
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    exception_rows.append(dict(zip(header, line.split(","))))

        if failure_rows or exception_rows:
            print(f"\n{server}:")
            for row in failure_rows:
                print(f"  - failure: {row.get('Method')} {row.get('Name')} | {row.get('Error')} x{row.get('Occurrences')}")
            for row in exception_rows:
                print(f"  - exception: {row.get('Message')} x{row.get('Count')}")
        else:
            print(f"{server:<12} | none")


def plot_comparison(servers: list[str], output_path: Path) -> None:
    """Generate a bar chart comparing RPS by endpoint across servers."""
    try:
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        print("Plotting requires pandas and matplotlib. Install with:")
        print("    uv add --dev pandas matplotlib")
        raise SystemExit(1)

    records: list[pd.DataFrame] = []
    for server in servers:
        data = read_stats(server)
        if not data["endpoints"]:
            continue
        df = pd.DataFrame(data["endpoints"])
        df["server"] = server
        records.append(df)

    if not records:
        print("No endpoint data to plot.")
        return

    all_df = pd.concat(records, ignore_index=True)
    numeric_cols = [
        "Request Count",
        "Failure Count",
        "Median Response Time",
        "Average Response Time",
        "Min Response Time",
        "Max Response Time",
        "Average Content Size",
        "Requests/s",
        "Failures/s",
        "50%",
        "66%",
        "75%",
        "80%",
        "90%",
        "95%",
        "98%",
        "99%",
        "99.9%",
        "99.99%",
        "100%",
    ]
    for col in numeric_cols:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Throughput plot
    pivot_rps = all_df.pivot_table(
        index="Name", columns="server", values="Requests/s", aggfunc="first"
    )
    pivot_rps.plot(kind="bar", ax=axes[0])
    axes[0].set_title("Requests/s by endpoint")
    axes[0].set_ylabel("Requests/s")
    axes[0].set_xlabel("Endpoint")
    axes[0].tick_params(axis="x", rotation=45)
    axes[0].legend(title="Server")

    # Latency plot
    pivot_avg = all_df.pivot_table(
        index="Name", columns="server", values="Average Response Time", aggfunc="first"
    )
    pivot_avg.plot(kind="bar", ax=axes[1])
    axes[1].set_title("Average response time by endpoint")
    axes[1].set_ylabel("Average response time (ms)")
    axes[1].set_xlabel("Endpoint")
    axes[1].tick_params(axis="x", rotation=45)
    axes[1].legend(title="Server")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved comparison plot to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Locust benchmark CSV files in .benchmarks/."
    )
    parser.add_argument(
        "--server",
        action="append",
        help="Server name to include (can be given multiple times; default: all found)",
    )
    parser.add_argument(
        "--plot",
        type=Path,
        help="Path to save a comparison bar chart (requires pandas + matplotlib)",
    )
    parser.add_argument(
        "--list", action="store_true", help="List available benchmark servers"
    )
    args = parser.parse_args()

    if args.list:
        print("Available benchmark servers:")
        for server in available_servers():
            print(f" - {server}")
        return 0

    if not BENCHMARK_DIR.exists():
        print(f"Benchmark directory not found: {BENCHMARK_DIR}", file=sys.stderr)
        print("Run a benchmark first with: uv run python scripts/benchmark.py --all", file=sys.stderr)
        return 1

    servers = args.server or available_servers()
    if not servers:
        print("No benchmark stats files found.", file=sys.stderr)
        return 1

    missing = [s for s in servers if not (BENCHMARK_DIR / f"benchmark_{s}_stats.csv").exists()]
    if missing:
        print(f"Missing stats files for: {', '.join(missing)}", file=sys.stderr)
        return 1

    print_summary_table(servers)

    # Collect all unique endpoints across selected servers and print per-endpoint tables
    endpoint_names: set[str] = set()
    for server in servers:
        data = read_stats(server)
        endpoint_names.update(r.get("Name", "") for r in data["endpoints"])

    for endpoint in sorted(endpoint_names):
        print_endpoint_table(servers, endpoint)

    print_failures(servers)

    if args.plot:
        plot_comparison(servers, args.plot)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
