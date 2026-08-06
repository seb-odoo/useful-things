#!/bin/bash
BASHRC="$HOME/.bashrc"
MARKER="# useful-things"

if ! grep -q "$MARKER" "$BASHRC"; then
    echo "useful-things not found in $BASHRC"
    exit 0
fi

python3 - <<'EOF'
import re, pathlib
p = pathlib.Path.home() / ".bashrc"
content = p.read_text()
content = re.sub(r"\n# useful-things\n.*?# end useful-things\n", "\n", content, flags=re.DOTALL)
p.write_text(content)
EOF

echo "useful-things removed from $BASHRC. Run 'source $BASHRC' to apply."
