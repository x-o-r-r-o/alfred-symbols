#!/bin/bash
# Passes the selected text through to Alfred's clipboard object, exactly (no trailing newline),
# and remembers it as recently used. $sym_rec is set by the Script Filter row: "u:2192", "k:(^_^)", "x:\alpha".
if [[ -n "$sym_rec" && "$sym_rec" != *$'\n'* && ${#sym_rec} -le 300 && "$sym_rec" =~ ^[ukx]: ]]; then
  dir="$alfred_workflow_data"
  if [[ "$dir" == /* ]]; then
    mkdir -p "$dir" && printf '%s\n' "$sym_rec" >> "$dir/recent.log"
  fi
fi
printf '%s' "$1"
