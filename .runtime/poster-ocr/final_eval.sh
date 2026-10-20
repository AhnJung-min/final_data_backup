#!/usr/bin/env bash
# 규칙 v2 고정(FREEZE_V2) 뒤 한 번만 실행: v1·v2 추출 → 결합 → dev·test·예비분 채점.
set -e
cd "$(dirname "$0")/../.."
PY=.venv/Scripts/python.exe
OUT=data/runtime/poster-ocr/pred-final
test -f data/runtime/poster-ocr/FREEZE_V2
for e in easyocr paddleocr hybrid; do
  $PY -m app.poster_ocr.extract --ocr data/runtime/poster-ocr/runs/$e --out $OUT/$e | tr -d '\n' ; echo " <- v2 $e"
  $PY -m app.poster_ocr.extract_v1 --ocr data/runtime/poster-ocr/runs/$e --out $OUT/v1_$e | tr -d '\n' ; echo " <- v1 $e"
done
$PY -m app.poster_ocr.fusion --policy paddle2x1x --name fusion_p21 --pred-root $OUT
$PY -m app.poster_ocr.evaluate --gold data/benchmarks/gold-v2h --pred $OUT --splits dev test reserve \
  --pipeline v1_easyocr=easyocr --pipeline v1_paddleocr=paddleocr --pipeline v1_hybrid=hybrid --pipeline fusion_p21=paddleocr \
  --out data/runtime/poster-ocr/eval/final.json
echo "final eval finished"
