"""Summaries and baseline-vs-candidate comparison of result files."""

import json
import math
import statistics
import typing as t
from pathlib import Path

MIN_EFFECT = 0.03  # changes under 3% are reported as noise regardless of p-value
ALPHA = 0.01


def load(path: str | Path) -> dict[str, t.Any]:
    return json.loads(Path(path).read_text())


def _key(r: dict[str, t.Any]) -> tuple[str, str, str, int]:
    return (r["backend"], r["case"], r["variant"], r["n"])


def mann_whitney_p(a: list[float], b: list[float]) -> float:
    """Two-sided Mann-Whitney U p-value (normal approximation with tie correction)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return 1.0
    allv = sorted((v, i) for i, v in enumerate(a + b))
    ranks = [0.0] * (n1 + n2)
    ties = 0.0
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[allv[k][1]] = (i + j) / 2 + 1
        cnt = j - i + 1
        ties += cnt**3 - cnt
        i = j + 1
    u1 = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (abs(u1 - mu) - 0.5) / sigma
    return math.erfc(max(z, 0) / math.sqrt(2))


def classify(base: list[float], cand: list[float]) -> tuple[float, str, float]:
    speedup = statistics.median(base) / statistics.median(cand)
    p = mann_whitney_p(base, cand)
    disjoint = max(cand) < min(base) or min(cand) > max(base)
    if abs(speedup - 1) < MIN_EFFECT or not (p < ALPHA or disjoint):
        verdict = "~"
    else:
        verdict = "faster" if speedup > 1 else "SLOWER"
    return speedup, verdict, p


def _fmt_ms(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}" if v < 10 else f"{v:.1f}"


def _fmt_mem(v: int | None) -> str:
    return "-" if v is None else f"{v / 2**20:.1f}"


def compare(base_path: str, cand_path: str) -> str:
    base, cand = load(base_path), load(cand_path)
    bmap = {_key(r): r for r in base["results"]}
    cmap = {_key(r): r for r in cand["results"]}
    lines = [
        f"# Comparison\n\nbaseline: `{base_path}` ({base['meta'].get('git', {}).get('sha', '?')[:12]})  ",
        f"candidate: `{cand_path}` ({cand['meta'].get('git', {}).get('sha', '?')[:12]})\n",
        "speedup = baseline median / candidate median. Verdict needs >= 3% change and either "
        f"Mann-Whitney p < {ALPHA} or non-overlapping samples; otherwise `~` (noise).\n",
    ]
    geo: dict[tuple[str, str], list[float]] = {}
    current = None
    for k in sorted(set(bmap) | set(cmap), key=lambda k: (k[0], k[1], k[2], k[3])):
        if current != k[:2]:
            current = k[:2]
            lines += [
                f"\n## {k[0]} / {k[1]}\n",
                "| variant | n | base ms | cand ms | speedup | verdict | p | base MiB | cand MiB |",
                "|---|---:|---:|---:|---:|---|---:|---:|---:|",
            ]
        b, c = bmap.get(k), cmap.get(k)
        if not b or not c:
            lines.append(f"| {k[2]} | {k[3]} | missing in {'candidate' if b else 'baseline'} ||||||| ")
            continue
        if not b.get("verified") or not c.get("verified"):
            err = c.get("error") or b.get("error")
            who = "candidate" if not c.get("verified") else "baseline"
            lines.append(f"| {k[2]} | {k[3]} | **FAILED ({who})**: {err} ||||||| ")
            continue
        bs, cs = [v / 1e6 for v in b["wall_ns"]], [v / 1e6 for v in c["wall_ns"]]
        speedup, verdict, p = classify(bs, cs)
        geo.setdefault((k[0], k[2]), []).append(speedup)
        lines.append(
            f"| {k[2]} | {k[3]} | {_fmt_ms(statistics.median(bs))} | {_fmt_ms(statistics.median(cs))} | "
            f"{speedup:.2f}x | {verdict} | {p:.3f} | {_fmt_mem(b.get('peak_mem_bytes'))} | "
            f"{_fmt_mem(c.get('peak_mem_bytes'))} |"
        )
    lines += [
        "\n## Geometric mean speedup per backend/variant (indicative only; the matrix above is authoritative)\n",
        "| backend | variant | cells | geomean |",
        "|---|---|---:|---:|",
    ]
    for (backend, variant), vals in sorted(geo.items()):
        gm = math.exp(statistics.fmean(math.log(v) for v in vals))
        lines.append(f"| {backend} | {variant} | {len(vals)} | {gm:.2f}x |")
    return "\n".join(lines) + "\n"


def summary(path: str) -> str:
    """One result file: medians per cell plus each variant's cost relative to plain pydal."""
    data = load(path)
    rows = {_key(r): r for r in data["results"]}
    lines = [f"# Summary of `{path}`\n", "ratio = variant median / pydal median (1.00 = same cost as plain pydal)\n"]
    current = None
    for k in sorted(rows):
        if current != k[:2]:
            current = k[:2]
            lines += [
                f"\n## {k[0]} / {k[1]}\n",
                "| variant | n | median ms | q1-q3 ms | cpu ms | reps | vs pydal | peak MiB |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        r = rows[k]
        if not r.get("verified"):
            lines.append(f"| {k[2]} | {k[3]} | **FAILED**: {r.get('error')} ||||||")
            continue
        s = r["stats"]
        ref = rows.get((k[0], k[1], "pydal", k[3]))
        ratio = s["median_ms"] / ref["stats"]["median_ms"] if ref and ref.get("verified") else None
        iqr = f"{_fmt_ms(s.get('q1_ms'))}-{_fmt_ms(s.get('q3_ms'))}" if "q1_ms" in s else "-"
        lines.append(
            f"| {k[2]} | {k[3]} | {_fmt_ms(s['median_ms'])} | {iqr} | {_fmt_ms(s['cpu_median_ms'])} | "
            f"{len(r['wall_ns'])} | {'-' if ratio is None else f'{ratio:.2f}'} | {_fmt_mem(r.get('peak_mem_bytes'))} |"
        )
    return "\n".join(lines) + "\n"
