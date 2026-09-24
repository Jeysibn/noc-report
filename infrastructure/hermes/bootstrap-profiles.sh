#!/bin/sh
# Seed repository-owned Hermes policies before starting the shared gateway.
# The log profile is a core dependency. Daily Report is optional and its
# failures must remain visible without taking down log analysis.
set -eu

profile_root=${HERMES_PROFILE_ROOT:-/opt/data/profiles}
config_root=${NOC_REPORT_HERMES_CONFIG_ROOT:-/opt/noc-report/hermes}
daily_enabled=${DAILY_REPORT_AI_ENABLED:-false}

case "$daily_enabled" in
  true|false) ;;
  *)
    printf 'DAILY_REPORT_AI_ENABLED must be exactly true or false (got %s)\n' "$daily_enabled" >&2
    exit 2
    ;;
esac

: "${API_SERVER_KEY:?API_SERVER_KEY is required for the Hermes gateway}"

bootstrap_profile() {
  profile=$1
  profile_dir="$profile_root/$profile"
  config_file="$config_root/$profile/config.yaml"
  created=false

  if [ ! -f "$profile_dir/SOUL.md" ]; then
    hermes profile create "$profile" || return $?
    created=true
  fi

  # Refresh NOC policy on each deployment while preserving runtime/provider
  # credentials already stored in the profile's private .env file.
  cp "$config_file" "$profile_dir/config.yaml" || return $?
  if [ "$profile" = noc-log-analysis ]; then
    touch "$profile_dir/.noc-report-seeded" || return $?
  elif [ "$created" = true ] && [ -f "$profile_root/noc-log-analysis/.env" ]; then
    cp "$profile_root/noc-log-analysis/.env" "$profile_dir/.env" || return $?
  fi

  if ! grep -q '^API_SERVER_KEY=' "$profile_dir/.env" 2>/dev/null; then
    printf '\nAPI_SERVER_KEY=%s\n' "$API_SERVER_KEY" >> "$profile_dir/.env" || return $?
  fi
}

# Core capability: any bootstrap failure must prevent the gateway from
# accepting log jobs with an unverified or missing policy.
bootstrap_profile noc-log-analysis

if [ "$daily_enabled" = true ]; then
  # Do not mask this error: make it explicit in container logs. However, the
  # shared gateway still starts for log analysis. The Daily worker's assigned
  # profile verification keeps that capability not-ready until repaired.
  if bootstrap_profile noc-daily-report; then
    printf 'Hermes Daily Report profile bootstrap succeeded\n'
  else
    printf 'ERROR: Hermes Daily Report profile bootstrap failed; Daily Report is degraded, continuing with log analysis\n' >&2
  fi
else
  printf 'Hermes Daily Report profile is disabled; bootstrap skipped\n'
fi

exec hermes gateway run
