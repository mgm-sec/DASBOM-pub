#!/usr/bin/env bash
# Fetch all org repo metadata → CSV

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

OUTPUT="$PROJECT_ROOT/output/repos_overview.csv"
mkdir -p "$PROJECT_ROOT/output"

log "Fetching $ORG org repos from GitHub API..."

gh api "/orgs/$ORG/repos?type=all" \
    --paginate \
    --jq '
      .[] |
      [
        .name,
        .full_name,
        (.description // "" | gsub(","; ";") | gsub("\n"; " ")),
        (.language // ""),
        (.topics // [] | join("|")),
        (.stargazers_count | tostring),
        (.forks_count | tostring),
        (.open_issues_count | tostring),
        (.watchers_count | tostring),
        (.size | tostring),
        .visibility,
        (.archived | tostring),
        (.disabled | tostring),
        (.fork | tostring),
        (.is_template // false | tostring),
        .created_at,
        .updated_at,
        .pushed_at,
        .default_branch,
        ((.license // {}) | (.name // "")),
        ((.license // {}) | (.spdx_id // "")),
        (.homepage // ""),
        (.has_wiki | tostring),
        (.has_issues | tostring),
        (.has_projects | tostring),
        (.allow_forking // false | tostring),
        .clone_url,
        .ssh_url
      ] | @csv
    ' > /tmp/repos_rows.csv

{
  echo '"name","full_name","description","language","topics","stars","forks","open_issues","watchers","size_kb","visibility","archived","disabled","fork","is_template","created_at","updated_at","pushed_at","default_branch","license_name","license_spdx_id","homepage","has_wiki","has_issues","has_projects","allow_forking","clone_url","ssh_url"'
  cat /tmp/repos_rows.csv
} > "$OUTPUT"

TOTAL=$(tail -n +2 "$OUTPUT" | wc -l | tr -d ' ')
ARCHIVED=$(tail -n +2 "$OUTPUT" | grep ',\"true\",' | wc -l | tr -d ' ')
log "Done: $TOTAL repos ($ARCHIVED archived) → $OUTPUT"
