# Commands run inside the `api` container by default.
# `make LOCAL=1 <target>` runs them with the local .venv instead (DB still in Docker).
DC      ?= docker compose
export APP_UID := $(shell id -u)

ifdef LOCAL
PY := .venv/bin/python
else
PY := $(DC) exec -T api python
endif

.PHONY: up down logs ingest stats search ask eval eval-retrieval eval-fixture calibrate \
        report baseline gate test lint judge-sample judge-score verify-golden

up:                ## build and start db + api
	$(DC) up -d --build

down:
	$(DC) down

logs:
	$(DC) logs -f api

ingest:            ## fetch, clean, chunk, embed every URL in data/urls.txt (idempotent)
	$(PY) -m app.ingest.run

stats:             ## document / chunk counts from the database
	$(PY) -m app.ingest.run stats

ask:               ## make ask Q="What caused the 2017 S3 outage?"
	curl -s localhost:8000/ask -H 'content-type: application/json' \
	  -d "$$(python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$(Q)")" | python3 -m json.tool

search:            ## make search Q="expired certificate"
	curl -s -G localhost:8000/search --data-urlencode "q=$(Q)" | python3 -m json.tool

eval:              ## full eval (retrieval + faithfulness if ANTHROPIC_API_KEY is set)
	$(PY) -m eval.run

eval-retrieval:    ## retrieval + ablation only, no API key needed
	$(PY) -m eval.run --retrieval-only

calibrate:         ## pick RERANK_THRESHOLD from the golden set
	$(PY) -m eval.calibrate

judge-sample:      ## export 20 judged answers for human labelling
	$(PY) -m eval.judge_agreement sample

judge-score:       ## judge-human agreement -> latest.json
	$(PY) -m eval.judge_agreement score

verify-golden:     ## walk through golden.jsonl and confirm each item (sets verified_by)
	$(PY) scripts/verify_golden.py

report:            ## regenerate README results tables from eval/results/latest.json
	python3 scripts/report.py

baseline:          ## record eval/results/latest.json as the CI baseline for its suite
	python3 -m eval.gate eval/results/latest.json --update

gate:
	python3 -m eval.gate eval/results/latest.json

test:
	$(if $(LOCAL),.venv/bin/python -m pytest,$(DC) exec -T api python -m pytest)

lint:
	$(if $(LOCAL),.venv/bin/ruff check .,$(DC) exec -T api ruff check .)
