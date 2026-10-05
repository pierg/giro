#!/bin/sh
# Build the wheel and check that every shipped skill and prompt is inside it.
set -eu
rm -rf dist
uv build -q
wheel=$(ls dist/giro-*.whl)
for s in giro giro-setup spec plan grill; do
  unzip -l "$wheel" | grep -q "giro/_skills/$s/SKILL.md" || { echo "missing skill $s"; exit 1; }
done
for p in worker planner review conformance; do
  unzip -l "$wheel" | grep -q "giro/_prompts/$p.md" || { echo "missing prompt $p"; exit 1; }
done
echo "wheel bundles skills and prompts OK"
