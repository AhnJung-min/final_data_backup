#!/usr/bin/env bash
# 다른 작업이 없을 때 test 25장(rgb_2x)으로 엔진별 속도·메모리를 따로 잰다. 결과: data/runtime/poster-ocr/timing/<engine>/
cd "$(dirname "$0")/../.."
IDS=$(cat .runtime/poster-ocr/timing-ids.txt)
T=data/runtime/poster-ocr/timing
LOG=.runtime/logs/timing.log
echo "$(date -Iseconds) start" > $LOG
.runtime/easyocr-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine easyocr --splits test --variants rgb_2x --only $IDS --out $T/easyocr --model-dir .runtime/easyocr-models >> $LOG 2>&1
echo "$(date -Iseconds) easyocr exit=$?" >> $LOG
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/ocr_worker.py --engine paddleocr --splits test --variants rgb_2x --only $IDS --out $T/paddleocr --model-dir .runtime/paddle-models >> $LOG 2>&1
echo "$(date -Iseconds) paddle exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid plan --easy $T/easyocr --crops .runtime/poster-ocr/hybrid-crops-timing --variants rgb_2x >> $LOG 2>&1
.runtime/paddle-env/Scripts/python.exe app/poster_ocr/paddle_rec_worker.py --crops .runtime/poster-ocr/hybrid-crops-timing/crops.json --output .runtime/poster-ocr/hybrid-crops-timing/paddle_rec.json >> $LOG 2>&1
echo "$(date -Iseconds) hybrid rec exit=$?" >> $LOG
.venv/Scripts/python.exe -m app.poster_ocr.hybrid merge --easy $T/easyocr --crops .runtime/poster-ocr/hybrid-crops-timing --out $T/hybrid >> $LOG 2>&1
echo "$(date -Iseconds) timing finished" >> $LOG
