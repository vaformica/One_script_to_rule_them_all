#!/usr/bin/env bash
set -u

RUN_DIR="${1:-}"
JOB_ID="${2:-}"

if [[ -z "$RUN_DIR" ]]; then
  echo "Usage: $0 RUN_DIR [JOB_ID]" >&2
  exit 2
fi

echo "============================================================"
echo "PIPELINE RUN DIAGNOSTICS"
echo "============================================================"
echo "Generated: $(date --iso-8601=seconds)"
echo "Host: $(hostname)"
echo "User: $(whoami)"
echo "Run directory: $RUN_DIR"
echo

echo "---- Run directory ----"
if [[ -d "$RUN_DIR" ]]; then
  find "$RUN_DIR" -maxdepth 3 -printf '%y\t%TY-%Tm-%Td %TH:%TM:%TS\t%p\n' \
    2>/dev/null | sort
else
  echo "MISSING RUN DIRECTORY"
fi
echo

echo "---- Run metadata ----"
if [[ -f "$RUN_DIR/run_metadata.json" ]]; then
  cat "$RUN_DIR/run_metadata.json"
else
  echo "run_metadata.json not found"
fi
echo

echo "---- Recorded job IDs ----"
if [[ -f "$RUN_DIR/job_ids.env" ]]; then
  cat "$RUN_DIR/job_ids.env"
  # shellcheck disable=SC1090
  source "$RUN_DIR/job_ids.env"
else
  echo "job_ids.env not found"
  IDTRACKER_JOB=""
  POSTPROCESS_JOB=""
  COLLECTOR_JOB=""
fi
echo

if [[ -n "$JOB_ID" ]]; then
  IDS="$JOB_ID"
else
  IDS="${IDTRACKER_JOB:-},${POSTPROCESS_JOB:-},${COLLECTOR_JOB:-}"
  IDS="${IDS#,}"
  IDS="${IDS%,}"
  IDS="$(printf '%s' "$IDS" | sed 's/,,*/,/g')"
fi

echo "---- Current queue state ----"
if [[ -n "$IDS" ]]; then
  squeue -j "$IDS" \
    -o '%.18i %.12P %.22j %.10T %.12M %.35R' 2>&1 || true
else
  echo "No job IDs available"
fi
echo

echo "---- Accounting history ----"
if [[ -n "$IDS" ]]; then
  sacct -j "$IDS" -X -P \
    --format=JobIDRaw,JobName,Partition,State,ExitCode,Elapsed,NodeList,Reason \
    2>&1 || true
else
  echo "No job IDs available"
fi
echo

echo "---- Dependency details for active jobs ----"
if [[ -n "$IDS" ]]; then
  OLDIFS="$IFS"
  IFS=','
  for id in $IDS; do
    [[ -n "$id" ]] || continue
    echo "JOB $id"
    scontrol show job "$id" 2>&1 \
      | tr ' ' '\n' \
      | grep -E '^(JobId|JobName|JobState|Reason|Dependency|ExitCode|RunTime|TimeLimit|NodeList|WorkDir|StdOut|StdErr)=' \
      || true
    echo
  done
  IFS="$OLDIFS"
fi

echo "---- Session discovery ----"
if [[ -f "$RUN_DIR/session_path.txt" ]]; then
  echo "session_path.txt:"
  cat "$RUN_DIR/session_path.txt"
  SESSION_PATH="$(cat "$RUN_DIR/session_path.txt")"
  if [[ -d "$SESSION_PATH" ]]; then
    echo "Session directory exists."
    find "$SESSION_PATH" -maxdepth 3 -type f \
      \( -name 'trajectories*.npy' -o -name 'trajectories*.h5' \
         -o -name 'session.json' -o -name 'attributes.json' \) \
      -printf '%TY-%Tm-%Td %TH:%TM:%TS\t%p\n' 2>/dev/null | sort
  else
    echo "Recorded session directory does not exist: $SESSION_PATH"
  fi
else
  echo "session_path.txt not found"
fi

echo
echo "Recent session directories under run:"
find "$RUN_DIR" -type d -name 'session_*' \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS\t%p\n' 2>/dev/null \
  | sort -r | head -20
echo

echo "---- Logs ----"
if [[ -d "$RUN_DIR/logs" ]]; then
  for log in "$RUN_DIR"/logs/*.out "$RUN_DIR"/logs/*.err; do
    [[ -f "$log" ]] || continue
    echo
    echo "===== $log ====="
    tail -n 200 "$log"
  done
else
  echo "No logs directory"
fi

echo
echo "---- Likely failure messages ----"
if [[ -d "$RUN_DIR/logs" ]]; then
  grep -RniE \
    'critical|error|failed|traceback|exception|no session|unrecognized|permission denied|not found|dependencyneversatisfied' \
    "$RUN_DIR/logs" 2>/dev/null | tail -100 || true
else
  echo "No logs to search"
fi
