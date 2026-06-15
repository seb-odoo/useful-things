# Curated Odoo-dev subset of useful-things/.bashrc, adapted for the Claude sandbox dev container.
# Sourced from the container's ~/.bashrc (see devcontainer.json postCreateCommand).
# Only commands that work in the sandbox are kept; paths use /home/seb/... to match the mounts.

# --- git workflow aliases ---
alias gbd="git branch -D"
alias gis="git status"
alias gil="git log"
alias gfo='git fetch odoo -p'
alias gfod='git fetch odoo-dev -p'
alias gpf='git push --force-with-lease'
alias grm='git rebase odoo/master'
alias gr10='git rebase odoo/10.0'
alias gr11='git rebase odoo/11.0'
alias gr12='git rebase odoo/12.0'
alias gr123='git rebase odoo/saas-12.3'
alias gr13='git rebase odoo/13.0'
alias gr14='git rebase odoo/14.0'
alias gr15='git rebase odoo/15.0'
alias gr16='git rebase odoo/16.0'
alias gr17='git rebase odoo/17.0'
alias gr18='git rebase odoo/18.0'
alias gr182='git rebase odoo/saas-18.2'
alias gr183='git rebase odoo/saas-18.3'
alias gr184='git rebase odoo/saas-18.4'
alias gr19='git rebase odoo/19.0'
alias grdr='git rebase odoo-dev/master-discuss-refactoring'
alias grc='git rebase --continue'
alias gcrm='git commit --reuse-message=HEAD@{1}'
alias gca='git commit --amend'
alias gcane='git commit --amend --no-edit'
alias gcad='git commit --amend --date="$(date -R)"'
alias gcadne='git commit --amend --date="$(date -R)" --no-edit'
alias grs='git reset --soft HEAD~1'
alias gwhere='git branch -r --contains'
alias gpfl="git push --force-with-lease --force-if-includes"
alias tig="tig --max-count=100"

function gr()
{
	git rebase "odoo/${1}"
}

# checkout an existing branch
function gcb() { git checkout "${1}-${2}--seb"; }

function branchdb() {
    edition=${1}
    branch=`git branch | grep \*`
    branch=${branch/\(HEAD detached at odoo-dev\/}
    branch=${branch/\(HEAD detached at }
    branch=${branch/\(HEAD detached from odoo-dev\/}
    branch=${branch/\(HEAD detached from }
    branch=${branch/\(no branch, rebasing }
    branch=${branch/\(no branch, bisect started on }
    branch=${branch/\)}
    d=${branch/\* }
	if [ "$d" == "(no branch" ]; then
    	d=$(basename "${PWD%/odoo}")
	fi
    if [[ "${edition}" == *"e"* ]]; then
        d="${d}-e"
    fi
    if [[ "${edition}" == *"t"* ]]; then
        d="${d}-t"
    fi
    if [[ "${edition}" == *"d"* ]]; then
        d="${d}-d"
    fi
    echo "${d}"
}

# --- odoo-bin helpers ---
function odoo-bin() {
	RES="./odoo-bin $(odoo-bin-params $*)"
	echo "Executing: ${RES}"
	eval $RES
}

function odoo-bin-params() {
    edition=${1}
    shift 1
    rest=$*
    d="$(branchdb ${edition})"
	cli=""
	addons_path="./../odoo/odoo/addons,./../odoo/addons"
	if [[ "${edition}" == *"s"* ]]; then
		cli='shell '
	fi
    if [[ "${edition}" == *"p"* ]]; then
        cli='populate '
    fi
	if [[ "${edition}" == *"e"* ]]; then
		addons_path="${addons_path},./../enterprise/"
	fi
	if [[ "${edition}" == *"t"* ]]; then
		addons_path="${addons_path},./../design-themes/"
	fi
	if [[ "${edition}" == *"d"* ]]; then
		addons_path="${addons_path},~/repo/big-data/"
	fi
	if [[ "${edition}" == *"a"* ]]; then
		addons_path="${addons_path},~/repo/allbuilds.org/odoo-addons"
	fi
	echo "${cli}-d ${d} --addons-path ${addons_path} --dev replica ${rest}"
}

alias obet="odoo-bin et"

function otf() {
	odoo-bin et --stop-after-init --test-file $*
}

function ott() {
	odoo-bin et --stop-after-init --test-tags $*
}

function ottb() {
	rm ~/before.log
	odoo-bin et --stop-after-init --logfile ~/before.log --test-tags $*
}

function otta() {
	rm ~/after.log
	odoo-bin et --stop-after-init --logfile ~/after.log --test-tags $*
}

# kill stuck odoo process, by rde-odoo
function killodoo() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill; }
function killodoo9() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill -9; }

# --- virtualenvs (absolute path: mounted at /home/seb/virtualenvs in the container) ---
function odoo-venv-17() {
	source /home/seb/virtualenvs/odoo17/bin/activate
}

function odoo-venv() {
	source /home/seb/virtualenvs/odoo20/bin/activate
}
