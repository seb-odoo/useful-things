#!/bin/bash
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

alias create_bundle="python $SCRIPTS_DIR/create_bundle.py"
alias delete_bundle="python $SCRIPTS_DIR/delete_bundle.py"
alias fetch_bundle="python $SCRIPTS_DIR/fetch_bundle.py"

_argcomplete_alias() {
    local script="$1"
    local IFS=$'\013'
    local SUPPRESS_SPACE=0
    compopt +o default 2>/dev/null && SUPPRESS_SPACE=1
    COMPREPLY=( $(IFS="$IFS" \
        COMP_LINE="$COMP_LINE" COMP_POINT="$COMP_POINT" \
        _ARGCOMPLETE_COMP_WORDBREAKS="$COMP_WORDBREAKS" \
        _ARGCOMPLETE=1 _ARGCOMPLETE_SUPPRESS_SPACE=$SUPPRESS_SPACE \
        python "$script" 8>&1 9>/dev/null 1>/dev/null 2>/dev/null) )
    [[ $? != 0 ]] && unset COMPREPLY
}

_complete_create_bundle() { _argcomplete_alias "$SCRIPTS_DIR/create_bundle.py"; }
_complete_delete_bundle() { _argcomplete_alias "$SCRIPTS_DIR/delete_bundle.py"; }

complete -o nospace -o default -F _complete_create_bundle create_bundle
complete -o nospace -o default -F _complete_delete_bundle delete_bundle
