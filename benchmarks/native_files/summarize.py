import collections
import json
import platform
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(path):
    records = [json.loads(line) for line in Path(path).read_text().splitlines()]
    groups = collections.defaultdict(list)
    for record in records:
        fields = ("backend", "direction", "size", "concurrency", "mode")
        key = tuple(record[name] for name in fields)
        groups[key].append(record)
    summaries = []
    print(
        "| Backend | Direction | MiB × concurrency | Mode | Wall s | CPU s | "
        "Peak RSS MiB | RSS growth MiB | Loop p99 ms | Max gap ms |"
    )
    print("|---|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for (backend, direction, size, concurrency, mode), values in sorted(groups.items()):
        metrics = {
            metric: statistics.median(item[metric] for item in values)
            for metric in (
                "wall_s", "cpu_s", "rss_peak", "rss_growth", "loop_gap_p99_ms", "loop_gap_max_ms",
            )
        }
        summary = dict(
            backend=backend, direction=direction, size=size, concurrency=concurrency,
            mode=mode, samples=len(values), **metrics,
        )
        summaries.append(summary)
        print(
            f"| {backend} | {direction} | {size//1024**2} × {concurrency} | {mode} | "
            f"{metrics['wall_s']:.4f} | {metrics['cpu_s']:.4f} | "
            f"{metrics['rss_peak']/1024**2:.1f} | {metrics['rss_growth']/1024**2:.1f} | "
            f"{metrics['loop_gap_p99_ms']:.2f} | {metrics['loop_gap_max_ms']:.2f} |"
        )
    output = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "samples": len(records),
        "transfers": sum(item["concurrency"] for item in records),
        "bytes": sum(item["size"] * item["concurrency"] for item in records),
        "summaries": summaries,
    }
    Path(path).with_suffix(".summary.json").write_text(json.dumps(output, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
