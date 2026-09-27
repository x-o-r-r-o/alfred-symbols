#!/bin/bash
# Passes the selected text through to Alfred's clipboard object, exactly (no trailing newline),
# and remembers it as recently used. $sym_rec is set by the Script Filter row: "u:2192", "k:(^_^)", "x:\alpha".
if [[ -n "$sym_rec" && "$sym_rec" != *$'\n'* && ${#sym_rec} -le 300 && "$sym_rec" =~ ^[ukx]: ]]; then
  dir="$alfred_workflow_data"
  if [[ "$dir" == /* ]]; then
    mkdir -p "$dir" && printf '%s\n' "$sym_rec" >> "$dir/recent.log"
  fi
fi
# Results over 50 KB arrive as "symfile:<path>" (written by symbols.js). Only files named
# results/<hex>.txt directly inside the workflow's cache are read; anything else passes through as text.
if [[ "$1" == symfile:* && -n "$alfred_workflow_cache" ]]; then
  f="${1#symfile:}"
  name="${f#"${alfred_workflow_cache%/}"/}"
  if [[ "$name" != "$f" && "$name" =~ ^results/[0-9a-f]+\.txt$ && -f "$f" ]]; then
    cat "$f"
    exit
  fi
fi
printf '%s' "$1"
