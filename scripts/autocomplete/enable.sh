#!/bin/bash
MARKER="# useful-things"
BASHRC="$HOME/.bashrc"
AUTOCOMPLETE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if grep -q "$MARKER" "$BASHRC"; then
    echo "useful-things already configured in $BASHRC"
    exit 0
fi

cat >> "$BASHRC" << BLOCK

$MARKER
source "$AUTOCOMPLETE_DIR/completions.sh"
# end useful-things
BLOCK

echo "useful-things added to $BASHRC. Run 'source $BASHRC' to apply."
