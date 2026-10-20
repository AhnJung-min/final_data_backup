#!/usr/bin/env bash
# 본 실행 대기열(chain.sh)이 끝나면 예비 52장을 같은 3경로 × 전처리 2종으로 실행한다.
cd "$(dirname "$0")/../.."
LOG=.runtime/logs/chain-reserve.log
until grep -q "chain finished" .runtime/logs/chain.log 2>/dev/null; do sleep 60; done
echo "$(date -Iseconds) start reserve" >> $LOG
.runtime/easyocr-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine easyocr --splits reserve --variants rgb_2x rgb_1x --out data/runtime/poster-ocr/runs/easyocr --model-dir .runtime/easyocr-models > .runtime/logs/run-reserve-easyocr.log 2>&1
echo "$(date -Iseconds) reserve easyocr exit=$?" >> $LOG
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine paddleocr --splits reserve --variants rgb_2x rgb_1x --out data/runtime/poster-ocr/runs/paddleocr --model-dir .runtime/paddle-models > .runtime/logs/run-reserve-paddle.log 2>&1
echo "$(date -Iseconds) reserve paddle exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid plan --easy data/runtime/poster-ocr/runs/easyocr --crops .runtime/poster-ocr/hybrid-crops-reserve --splits reserve >> $LOG 2>&1
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/paddle_rec_worker.py --crops .runtime/poster-ocr/hybrid-crops-reserve/crops.json --output .runtime/poster-ocr/hybrid-crops-reserve/paddle_rec.json > .runtime/logs/run-reserve-hybrid-rec.log 2>&1
echo "$(date -Iseconds) reserve hybrid rec exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid merge --easy data/runtime/poster-ocr/runs/easyocr --crops .runtime/poster-ocr/hybrid-crops-reserve --out data/runtime/poster-ocr/runs/hybrid-reserve-tmp >> $LOG 2>&1
echo "$(date -Iseconds) reserve hybrid merge exit=$?" >> $LOG
cp -r data/runtime/poster-ocr/runs/hybrid-reserve-tmp/r2-* data/runtime/poster-ocr/runs/hybrid/ && cp data/runtime/poster-ocr/runs/hybrid-reserve-tmp/run.json data/runtime/poster-ocr/runs/hybrid/run.reserve.json
echo "$(date -Iseconds) reserve hybrid copied exit=$?" >> $LOG
echo "$(date -Iseconds) reserve finished" >> $LOG
