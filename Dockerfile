# tokenledger — output-token accounting for agent cron fleets
# (multi-arch: linux/amd64, linux/arm64, 386 — e.g. Raspberry Pi)
#
# Build:  podman build --platform linux/amd64 -t wallydk24/tokenledger:amd64 .
# Run:    docker run --rm -v ~/.tokenledger:/home/tl/.tokenledger wallydk24/tokenledger top
# Stdlib only. State lives in ~/.tokenledger/ (mount it to persist the ledger).

FROM python:3.12-alpine

WORKDIR /app
COPY tokenledger.py ./
USER 1000

ENTRYPOINT ["python3", "/app/tokenledger.py"]
CMD ["top"]
