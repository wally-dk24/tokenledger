# TESTLOG — tokenledger acceptance, 2026-10-03

Run in a scratch `HOME=/tmp/tl-test` so test data never touched the real
ledger at `~/.tokenledger/`. All commands: `python3 tokenledger.py`.

## (a) Three commands wrapped — chatty, quiet, failing — all ledgered ✓

- `run --job chatty-job -- python3 -c "for i in range(60): print(...)"` → exit 0
- `run --job quiet-job -- python3 -c "print('all quiet')"` → exit 0
- `run --job failing-job -- python3 -c "...; sys.exit(3)"` → exit 3
- `ledger` afterwards shows all three entries with exit codes and token counts.

## (b) Failing command: exit code propagates, output still ledgered ✓

failing-job exited **3** (the wrapped command's code), its stdout
("some output before dying") printed normally, its stderr ("boom on
stderr") passed through byte-identical on stderr, and the ledger line
records `exit=3`, `out=7tok`, `err=4tok`.

## (c) 100-token threshold triggers the compressor path; ratio recorded ✓

`run --job chatty-rep` printing 120 identical poll lines + blank runs
(1831 estimated tokens) emitted:

```
[tokenledger] stdout compressed for job 'chatty-rep': 1831->39 tokens (~46.9x). Full output hashed in ledger.
poll tick: nothing changed, all systems nominal, standing by [x80 repeated]

poll tick: nothing changed, all systems nominal, standing by [x40 repeated]
```

Ledger line: `compressed 39tok 46.949x`. stderr untouched throughout.

## (d) `top` ranks the chatty job first ✓

```
chatty-rep      1832tok   1 runs c:1   ##############################
chatty-job       554tok   1 runs c:0   #########
failing-job       11tok   1 runs c:0   #
quiet-job          4tok   1 runs c:0   #
```

## (e) Tampering with one ledger line → verify exits non-zero, names the line ✓

Edited line 2's `stdout_tokens` to 999999. `verify` exited **1** with:

```
tampered line 2 (job=quiet-job ts=2026-10-03T21:33:32Z): hash mismatch
```

Restored the backup; `verify` → `ledger ok: 4 entries`, exit 0.

## Extras verified

- Per-job `policy.json` override: `noisy-but-fine` with
  `compress_threshold: 1000000` emitted 740 tokens uncompressed (override
  honored over the 100 default).
- `--threshold` flag overrides both.
- `ledger --job <name>` filters correctly.
