# tokenledger 🧾

A tiny personal CLI that wraps every shell command an agent runs, measures
the **output-token cost** of what it printed, keeps a tamper-evident per-job
ledger, and routes bloated stdout through a small deterministic compressor
before it lands in the log.

Built for one person's cron fleet: ~20 silent jobs, most of whose cost is
verbose stdout nobody reads. `tokenledger top` shows where the tokens go.

Lineage: [rtk](https://github.com/rtk-ai/rtk) (the proxy model),
[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (the
terse-transform insight), and a receiptchain-style hash-chained ledger.

## Install

```bash
git clone https://github.com/wally-dk24/tokenledger.git
cd tokenledger
# stdlib only — nothing to install
```

Or run the multi-arch image (linux/amd64, linux/arm64, 386):

```bash
docker run --rm -v ~/.tokenledger:/root/.tokenledger wallydk24/tokenledger top
```

## Usage

```bash
./tokenledger.py run --job console-prep -- python3 manifest.py
./tokenledger.py run --job inbox --threshold 2000 -- ./poll.sh
./tokenledger.py ledger --job inbox --limit 10
./tokenledger.py top
./tokenledger.py top --since 24
./tokenledger.py verify
```

`run` prints the command's stdout (compressed when over threshold) and
passes stderr through **untouched, always**. The wrapped command's exit
code always propagates — a failing command is ledgered, not hidden.

Per-job overrides live in `~/.tokenledger/policy.json`:

```json
{
  "default_compress_threshold": 100,
  "jobs": { "chatty-but-fine": { "compress_threshold": 50000 } }
}
```

## The compressor

Deterministic, stdlib-only, and disclosed: strips ANSI escapes, trailing
whitespace, collapses 3+ blank lines to one, and folds runs of identical
consecutive lines into one line with a repetition count. It never invents
or deletes distinct content lines. When it fires, the first stdout line
says so, with the before/after token counts and ratio. The full original
output is hashed into the ledger (not stored — the log stays small, the
receipt stays checkable).

## Token heuristic — read this

Token counts are **estimates**, not meter readings. Without `tiktoken`
installed, tokenledger uses `ceil(chars / 4)` — the industry rule of thumb.
If `tiktoken` happens to be installed it is used instead, but it is never
required. These numbers exist to *rank jobs and spot bloat*, not to audit
a provider's bill. Treat any single number as ±30%; treat rankings and
week-over-week trends as signal.

## Tamper evidence

Each ledger line carries `prev` + `hash` (SHA-256 over the canonical record
chained to the previous line). `tokenledger verify` recomputes the chain
and names the offending line on any mismatch. Editing history is the only
way to fake the books — the chain makes that visible.

## Wiring it into a cron fleet

Wrap the chatty steps inside a cron job's body (agent workers: put it
around the shell step, not the whole body):

```bash
tokenledger run --job console-manifest -- python3 ~/workspace/ops-console/manifest.py
```

Then read `tokenledger top --since 24` in the evening report to see which
jobs cost the most. Start with the noisiest runners; give quiet,
safety-critical jobs a high `compress_threshold` (or wrap nothing at all —
stderr is never touched, but a compressed stdout is still a changed log).

## License

MIT — see LICENSE.
