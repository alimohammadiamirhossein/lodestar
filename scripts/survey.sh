#!/usr/bin/env bash
# Read-only survey of a shared server for the atlas design (DESIGN.md §10).
# Lists metadata only: never reads file contents, never leaves the given roots.
# Usage: bash survey.sh [ROOT ...]        (default: $HOME)
# Output: survey-<host>-<date>.txt in the current directory.

set -u
ROOTS=("$@"); [ ${#ROOTS[@]} -eq 0 ] && ROOTS=("$HOME")
OUT="survey-$(hostname -s)-$(date +%Y%m%d).txt"
CAP=${CAP:-300}   # seconds per heavy step
NICE="nice -n 19"; command -v ionice >/dev/null && NICE="$NICE ionice -c3"

section() { printf '\n## %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1 && echo "yes ($(command -v "$1"))" || echo no; }
capped() { timeout "$CAP" $NICE "$@" 2>/dev/null; [ $? -eq 124 ] && echo "(stopped after ${CAP}s, counts are partial)" >&2; }

{
section "Machine"
uname -srm
grep -m1 PRETTY_NAME /etc/os-release 2>/dev/null
echo "cpus: $(nproc)  mem: $(free -g 2>/dev/null | awk '/Mem:/{print $2" GB"}')"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sort | uniq -c
echo "logged-in users: $(who | awk '{print $1}' | sort -u | wc -l)"

section "Tools"
for t in claude uv python3 git rg fd sqlite3 sbatch squeue qsub tmux docker; do printf '%-8s %s\n' "$t" "$(have $t)"; done
python3 -c 'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("create virtual table t using fts5(x)"); print("sqlite fts5: yes")' 2>/dev/null || echo "sqlite fts5: no"

section "Network (HEAD requests only)"
for u in https://api.anthropic.com https://api.deepinfra.com; do
  code=$(curl -s -o /dev/null -m 10 -I -w '%{http_code}' "$u"); echo "$u -> ${code:-blocked}"
done

for R in "${ROOTS[@]}"; do
  section "Root: $R"
  df -hT "$R" 2>/dev/null | tail -1
  command -v quota >/dev/null && quota -s 2>/dev/null | tail -n +2
  command -v lfs >/dev/null && lfs quota -h -u "$USER" "$R" 2>/dev/null | tail -n +2

  echo; echo "files per top-level entry (capped):"
  for d in "$R"/*/ "$R"/.[!.]*/; do
    [ -d "$d" ] || continue
    n=$(timeout 60 $NICE find "$d" -xdev -type f 2>/dev/null | wc -l)
    printf '%10s  %s\n' "$n" "$d"
  done | sort -rn | head -40

  echo; echo "git repos (depth <= 5):"
  capped find "$R" -xdev -maxdepth 5 -type d -name .git -prune -printf '%h\n' | head -100

  echo; echo "experiment markers:"
  printf '  wandb run dirs:      %s\n' "$(capped find "$R" -xdev -maxdepth 7 -type d -name 'run-*' -path '*wandb*' | wc -l)"
  printf '  hydra output dirs:   %s\n' "$(capped find "$R" -xdev -maxdepth 7 -type d -name .hydra | wc -l)"
  printf '  tensorboard events:  %s\n' "$(capped find "$R" -xdev -maxdepth 8 -name 'events.out.tfevents*' | wc -l)"
  printf '  checkpoints:         %s\n' "$(capped find "$R" -xdev -maxdepth 8 -type f \( -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' -o -name '*.safetensors' \) | wc -l)"

  echo; echo "top extensions (first 2M paths):"
  capped find "$R" -xdev -type f | head -2000000 | sed -n 's/.*\.\([A-Za-z0-9]\{1,8\}\)$/\1/p' \
    | sort | uniq -c | sort -rn | head -25
done
} | tee "$OUT"

echo; echo "Wrote $OUT"
