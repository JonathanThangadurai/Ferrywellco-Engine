#!/usr/bin/env bash
# After a live source check in GitHub Actions: open or update one issue per failing source (label
# source-check), and close it when the source passes again. Exits 1 when the check failed, so the run is red too.
#
#   scripts/ci_source_issue.sh <source> <exit code of the check> <file with the check output>
set -euo pipefail

source_name="$1"
status="$2"
result_file="$3"
title="Source check failing: ${source_name}"
run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
reason="$(tail -n 1 "$result_file" 2>/dev/null || true)"
reason="${reason:-no output (the check crashed before printing a result)}"

gh label create source-check --color D93F0B --description "Live source check (nightly/weekly)" 2>/dev/null || true
number="$(gh issue list --label source-check --state open --limit 100 --json number,title \
  --jq ".[] | select(.title == \"${title}\") | .number" | head -n 1)"

if [ "$status" = "0" ]; then
  echo "${reason}"
  if [ -n "$number" ]; then
    gh issue close "$number" --comment "Passing again: ${reason} (${run_url})"
  fi
  exit 0
fi

body="$(printf 'The live access and format check for **%s** failed.\n\n```\n%s\n```\n\nRun: %s\n' \
  "$source_name" "$reason" "$run_url")"
if [ -n "$number" ]; then
  gh issue comment "$number" --body "$body"
else
  gh issue create --title "$title" --label source-check --body "$body"
fi
echo "${reason}"
exit 1
