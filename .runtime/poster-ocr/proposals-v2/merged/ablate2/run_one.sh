#!/bin/bash
cd /c/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team
M=.runtime/poster-ocr/proposals-v2/merged
label=$1; flags=$2
POSTER_ABLATE=$flags .venv/Scripts/python.exe -m app.poster_ocr.rule_dev --extract $M/ablate/extract_flags2.py --splits dev test --out $M/ablate2/$label > $M/ablate2/$label.log 2>&1
echo "$label done $(head -1 $M/ablate2/$label.log)"
