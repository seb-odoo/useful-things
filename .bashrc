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
alias gr11='git rebase odoo/11.0'
alias gr11.5='git rebase odoo/saas-11.5'
alias gr12='git rebase odoo/12.0'
alias grc='git rebase --continue'
alias gcrm='git commit --reuse-message=HEAD@{1}'
alias gca='git commit --amend'
alias gcad='git commit --amend --date="$(date -R)"'
alias gcadrm='git commit --amend --date="$(date -R)" --reuse-message=HEAD@{1}'
alias grs='git reset --soft HEAD~1'

alias tog="terminator -l 'odoo gits' </dev/null &>/dev/null &"
alias tos="terminator -l 'odoo shell' </dev/null &>/dev/null &"

alias tig="tig --max-count=100"

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
function gcb()
{
	BASE=$1
	BRANCH=$2
	git checkout "${BASE}-${BRANCH}-seb"
}

# kill stuck odoo process, by rde-odoo
function killodoo() {
    ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill
}

