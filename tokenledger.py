#!/usr/bin/env python3
"""tokenledger — output-token accounting for agent cron fleets.

Wraps any shell command, measures the *output-token* cost of what it
printed, appends a tamper-evident line to a per-job ledger, and — when the
output is bloated past a per-job threshold — routes stdout through a small
deterministic compressor before it lands in the log. stderr always passes
through untouched, and the wrapped command's exit code always propagates.

Token counts are ESTIMATES (see README "Token heuristic"). They exist to
rank jobs and spot bloat, not to audit a provider's bill.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

VERSION = "0.1.0"
STATE_DIR = os.path.expanduser("~/.tokenledger")
LEDGER_PATH = os.path.join(STATE_DIR, "ledger.jsonl")
POLICY_PATH = os.path.join(STATE_DIR, "policy.json")
DEFAULT_THRESHOLD = 100  # tokens of stdout before the compressor kicks in

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Z]")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def estimate_tokens(text):
    """Honest heuristic token estimate. tiktoken used ONLY if installed."""
    try:
        import tiktoken  # optional, never required

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        # ~4 chars per token is the industry rule of thumb; we ceil it so
        # small outputs never estimate as 0 tokens.
        return max(1, (len(text) + 3) // 4)


def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(POLICY_PATH):
        with open(POLICY_PATH, "w") as f:
            json.dump({"default_compress_threshold": DEFAULT_THRESHOLD,
                       "jobs": {}}, f, indent=2)
    if not os.path.exists(LEDGER_PATH):
        open(LEDGER_PATH, "a").close()


def load_policy():
    ensure_state()
    try:
        with open(POLICY_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"default_compress_threshold": DEFAULT_THRESHOLD, "jobs": {}}


def threshold_for(policy, job, override=None):
    if override is not None:
        return override
    per_job = policy.get("jobs", {}).get(job, {})
    if "compress_threshold" in per_job:
        return int(per_job["compress_threshold"])
    return int(policy.get("default_compress_threshold", DEFAULT_THRESHOLD))


def compress_stdout(text):
    """Deterministic, lossless-ish stdout shrinker (the caveman transform).

    Strips ANSI escapes, trailing whitespace, collapses repeated blank
    lines, and folds runs of identical consecutive lines into one line with
    a repetition count. Never invents or deletes distinct content lines.
    Returns (compressed_text, ratio) where ratio = orig_tokens/new_tokens.
    """
    text = _ANSI_RE.sub("", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    folded = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run > 1 and lines[i].strip():
            folded.append(f"{lines[i]} [x{run} repeated]")
        else:
            folded.extend(lines[i:j])
        i = j
    # collapse 3+ blank lines to a single blank line
    compact, blanks = [], 0
    for ln in folded:
        if not ln.strip():
            blanks += 1
            if blanks <= 1:
                compact.append("")
        else:
            blanks = 0
            compact.append(ln)
    out = "\n".join(compact)
    if text.endswith("\n"):
        out += "\n"
    orig = estimate_tokens(text if text else "x")
    new = estimate_tokens(out if out else "x")
    return out, (orig / new) if new else 1.0


def _entry_hash(entry, prev):
    canon = json.dumps({k: v for k, v in entry.items() if k != "hash"},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + canon).encode("utf-8")).hexdigest()


def read_ledger():
    entries = []
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        entries.append({"_corrupt": line})
    return entries


def append_entry(entry):
    entries = read_ledger()
    prev = entries[-1].get("hash", "GENESIS") if entries else "GENESIS"
    entry["prev"] = prev
    entry["hash"] = _entry_hash(entry, prev)
    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def cmd_run(args):
    ensure_state()
    policy = load_policy()
    threshold = threshold_for(policy, args.job, args.threshold)
    if not args.cmd:
        print("tokenledger run: no command given", file=sys.stderr)
        return 2
    try:
        proc = subprocess.run(args.cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        exit_code = proc.returncode
        spawn_error = None
    except FileNotFoundError:
        proc = None
        exit_code = 127
        spawn_error = f"command not found: {args.cmd[0]}"
    except OSError as e:
        proc = None
        exit_code = 126
        spawn_error = f"exec failed: {e}"

    if proc is not None:
        out_bytes, err_bytes = proc.stdout, proc.stderr
        sys.stderr.buffer.write(err_bytes)
        sys.stderr.buffer.flush()
        out_text = out_bytes.decode("utf-8", errors="replace")
        err_text = err_bytes.decode("utf-8", errors="replace")
    else:
        out_bytes = err_bytes = b""
        out_text = err_text = ""

    out_tokens = estimate_tokens(out_text)
    err_tokens = estimate_tokens(err_text)
    compressed = False
    comp_tokens = out_tokens
    ratio = 1.0
    shown = out_text
    if proc is not None and out_tokens > threshold:
        shrunk, r = compress_stdout(out_text)
        if estimate_tokens(shrunk) < out_tokens:
            compressed = True
            comp_tokens = estimate_tokens(shrunk)
            ratio = r
            shown = (f"[tokenledger] stdout compressed for job '{args.job}': "
                     f"{out_tokens}->{comp_tokens} tokens (~{ratio:.1f}x). "
                     f"Full output hashed in ledger.\n") + shrunk
    sys.stdout.write(shown)
    sys.stdout.flush()

    append_entry({
        "v": 1, "ts": utcnow(), "job": args.job, "argv": args.cmd,
        "exit_code": exit_code,
        "spawn_error": spawn_error,
        "stdout_bytes": len(out_bytes), "stderr_bytes": len(err_bytes),
        "stdout_tokens": out_tokens, "stderr_tokens": err_tokens,
        "compressed": compressed, "compressed_tokens": comp_tokens,
        "ratio": round(ratio, 3),
        "stdout_sha256": hashlib.sha256(out_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(err_bytes).hexdigest(),
        "threshold": threshold,
    })
    return exit_code


def cmd_verify(_args):
    ensure_state()
    entries = read_ledger()
    prev = "GENESIS"
    for n, e in enumerate(entries, start=1):
        if "_corrupt" in e:
            print(f"tampered line {n}: unparseable JSON", file=sys.stderr)
            return 1
        if e.get("prev") != prev:
            print(f"tampered line {n} (job={e.get('job')} ts={e.get('ts')}): "
                  f"chain break", file=sys.stderr)
            return 1
        if e.get("hash") != _entry_hash(e, prev):
            print(f"tampered line {n} (job={e.get('job')} ts={e.get('ts')}): "
                  f"hash mismatch", file=sys.stderr)
            return 1
        prev = e["hash"]
    print(f"ledger ok: {len(entries)} entries")
    return 0


def cmd_ledger(args):
    entries = read_ledger()
    if args.job:
        entries = [e for e in entries if e.get("job") == args.job]
    entries = entries[-args.limit:]
    for e in entries:
        if "_corrupt" in e:
            print("(corrupt line)")
            continue
        flag = f" compressed {e['compressed_tokens']}tok {e['ratio']}x" \
            if e.get("compressed") else ""
        print(f"{e['ts']} {e['job']} exit={e['exit_code']} "
              f"out={e['stdout_tokens']}tok err={e['stderr_tokens']}tok{flag}")
    return 0


def cmd_top(args):
    from datetime import timedelta
    entries = read_ledger()
    cutoff = None
    if args.since:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since)
    totals = {}
    for e in entries:
        if "_corrupt" in e:
            continue
        if cutoff:
            try:
                ts = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ") \
                    .replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < cutoff:
                continue
        t = totals.setdefault(e["job"], {"tokens": 0, "runs": 0,
                                         "compressed": 0})
        t["tokens"] += e.get("stdout_tokens", 0) + e.get("stderr_tokens", 0)
        t["runs"] += 1
        t["compressed"] += 1 if e.get("compressed") else 0
    ranked = sorted(totals.items(), key=lambda kv: kv[1]["tokens"],
                    reverse=True)[:args.limit]
    if not ranked:
        print("no ledger entries")
        return 0
    width = max(len(j) for j, _ in ranked)
    peak = ranked[0][1]["tokens"] or 1
    for job, t in ranked:
        bar = "#" * max(1, int(30 * t["tokens"] / peak))
        print(f"{job:<{width}} {t['tokens']:>8}tok "
              f"{t['runs']:>3} runs c:{t['compressed']:<3} {bar}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tokenledger",
        description="Output-token accounting for agent cron fleets.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = ap.add_subparsers(dest="sub", required=True)

    r = sub.add_parser("run", help="wrap a command and ledger its output cost")
    r.add_argument("--job", required=True, help="job name for the ledger")
    r.add_argument("--threshold", type=int, default=None,
                   help="compress stdout above this many estimated tokens")
    r.add_argument("cmd", nargs=argparse.REMAINDER,
                   help="command to run, after --")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("verify", help="verify the ledger hash chain")
    v.set_defaults(fn=cmd_verify)

    l = sub.add_parser("ledger", help="show ledger entries")
    l.add_argument("--job", default=None)
    l.add_argument("--limit", type=int, default=20)
    l.set_defaults(fn=cmd_ledger)

    t = sub.add_parser("top", help="rank jobs by output-token spend")
    t.add_argument("--limit", type=int, default=10)
    t.add_argument("--since", type=float, default=None,
                   help="only entries from the last N hours")
    t.set_defaults(fn=cmd_top)

    args = ap.parse_args(argv)
    if args.sub == "run" and args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
