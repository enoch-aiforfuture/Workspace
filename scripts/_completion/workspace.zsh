#compdef workspace workspace-backup workspace-calendar workspace-contacts workspace-cookbook workspace-docs workspace-gallery workspace-mail workspace-mcp workspace-memory workspace-notes workspace-personal workspace-preset workspace-research workspace-sessions workspace-signature workspace-skills workspace-tasks workspace-theme workspace-webhook
# Zsh tab-completion for the workspace umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/workspace-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `workspace <tab>` completes subcommands; `workspace mail <tab>`
# completes mail subcommands; `workspace-mail <tab>` works the same.

_workspace_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _workspace_subs

_workspace_refresh() {
    _workspace_subs=()
    local dir="$(_workspace_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/workspace-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#workspace-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _workspace_subs[$sub]="$commands"
    done
}

_workspace() {
    [[ ${#_workspace_subs} -eq 0 ]] && _workspace_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "workspace" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_workspace_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_workspace_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_workspace_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # workspace-foo <tab>
    local sub="${cmd#workspace-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_workspace_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_workspace "$@"
