#!/usr/bin/env bash
# EasyOCR 전체 실행이 끝나면 Paddle → 혼합 경로를 순서대로 실행한다(CPU 경합을 피하려고 직렬).
cd "$(dirname "$0")/../.."
LOG=.runtime/logs/chain.log
until [ -f data/runtime/poster-ocr/runs/easyocr/run.rgb_2x-rgb_1x.json ]; do sleep 60; done
echo "$(date -Iseconds) easyocr done" >> $LOG
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine paddleocr --splits dev test --variants rgb_2x --out data/runtime/poster-ocr/runs/paddleocr --model-dir .runtime/paddle-models > .runtime/logs/run-paddle-2x.log 2>&1
echo "$(date -Iseconds) paddle rgb_2x exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid plan --easy data/runtime/poster-ocr/runs/easyocr --crops .runtime/poster-ocr/hybrid-crops >> $LOG 2>&1
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/paddle_rec_worker.py --crops .runtime/poster-ocr/hybrid-crops/crops.json --output .runtime/poster-ocr/hybrid-crops/paddle_rec.json > .runtime/logs/run-hybrid-rec.log 2>&1
echo "$(date -Iseconds) hybrid rec exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid merge --easy data/runtime/poster-ocr/runs/easyocr --crops .runtime/poster-ocr/hybrid-crops --out data/runtime/poster-ocr/runs/hybrid >> $LOG 2>&1
echo "$(date -Iseconds) hybrid merge exit=$?" >> $LOG
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine paddleocr --splits dev test --variants rgb_1x --out data/runtime/poster-ocr/runs/paddleocr --model-dir .runtime/paddle-models > .runtime/logs/run-paddle-1x.log 2>&1
echo "$(date -Iseconds) paddle rgb_1x exit=$?" >> $LOG
echo "$(date -Iseconds) chain finished" >> $LOG
