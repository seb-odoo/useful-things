export VISUAL="subl -w"
export EDITOR="$VISUAL"

# git checkout override to only look at local branches (not working anymore??)
_git_checkout ()
{
	__git_has_doubledash && return

	case "$cur" in
	--conflict=*)
		__gitcomp "diff3 merge" "" "${cur##--conflict=}"
		;;
	--*)
		__gitcomp "
			--quiet --ours --theirs --track --no-track --merge
			--conflict= --orphan --patch
			"
		;;
	*)
		# check if --track, --no-track, or --no-guess was specified
		# if so, disable DWIM mode
		local flags="--track --no-track --no-guess" track=1
		if [ -n "$(__git_find_on_cmdline "$flags")" ]; then
			track=''
		fi
		if [ "$command" = "checkoutr" ]; then
			__gitcomp_nl "$(__git_refs '' $track)"
		else
			__gitcomp_nl "$(__git_heads '' $track)"
		fi
		;;
	esac
}

alias gis="git status"
alias gil="git log"
alias gfo='git fetch odoo'
alias gfa='git fetch --all'
alias grm='git rebase odoo/master'
alias gr10='git rebase odoo/10.0'
alias gr11='git rebase odoo/11.0'
alias gr11.5='git rebase odoo/saas-11.5'
alias gr12='git rebase odoo/12.0'
alias grc='git rebase --continue'
alias gcrm='git commit --reuse-message=HEAD@{1}'
alias gca='git commit --amend'
alias gcad='git commit --amend --date="$(date -R)"'
alias gcadne='git commit --amend --date="$(date -R)" --no-edit'
alias grs='git reset --soft HEAD~1'

alias tog="terminator -l 'odoo gits' </dev/null &>/dev/null &"
alias tos="terminator -l 'odoo shell' </dev/null &>/dev/null &"

alias tig="tig --max-count=100"

alias ubash="source ~/.bashrc"

alias hackchromeheadless="google-chrome --headless --remote-debugging-port=8071"

# fetch, create, and push a new branch
function gnb()
{
	BASE=$1
	BRANCH=$2
	FULL_NAME="${BASE}-${BRANCH}-seb"
	git fetch odoo $BASE
	git checkout -b $FULL_NAME "odoo/${BASE}" --no-track
	git push -u odoo-dev $FULL_NAME
}

# checkout an existing branch
function gcb() { git checkout "${1}-${2}-seb"; }

# kill stuck odoo process, by rde-odoo
function killodoo() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill; }

function killodoo9() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill -9; }

function webbrowser() { python3 -m webbrowser $1; }

function task() { webbrowser "https://www.odoo.com/web#id=${1}&action=333&active_id=587&model=project.task&view_type=form&menu_id=4720"; }

function open_github() { webbrowser "https://github.com/${1}/${2}/${3}/${4}/${5}"; }

# PR
function oopr() { open_github "odoo" "odoo" "pull" "${1}"; }
function oepr() { open_github "odoo" "enterprise" "pull" "${1}"; }
function otpr() { open_github "odoo" "design-themes" "pull" "${1}"; }

# commit
function ooc() { open_github "odoo" "odoo" "commit" "${1}"; }
function oec() { open_github "odoo" "enterprise" "commit" "${1}"; }
function otc() { open_github "odoo" "design-themes" "commit" "${1}"; }
# branch
function oob() { open_github "odoo" "odoo" "commits" "${1}"; }
function oeb() { open_github "odoo" "enterprise" "commits" "${1}"; }
function otb() { open_github "odoo" "design-themes" "commits" "${1}"; }
# blame
function oobl() { open_github "odoo" "odoo" "blame" "${1}" "${2}"; }
function oebl() { open_github "odoo" "enterprise" "blame" "${1}" "${2}"; }
function otbl() { open_github "odoo" "design-themes" "blame" "${1}" "${2}"; }
