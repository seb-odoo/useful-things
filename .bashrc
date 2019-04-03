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
alias gfo='git fetch odoo -p'
alias gfa='git fetch --all -p'
alias gfod='git fetch odoo-dev -p'
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

function gr()
{
	git rebase "odoo/${1}"
}

# fetch, create, and push a new branch
function gnb()
{
	BASE=$1
	BRANCH=$2
	FULL_NAME="${BASE}-${BRANCH}-seb"
	git fetch odoo $BASE
	git checkout -b $FULL_NAME "odoo/${BASE}" --no-track
	if [ ! -z "$FULL_NAME" ]; then
		# we need to push because the upstream branch doesn't exist yet
		git push -u odoo-dev $FULL_NAME
	fi
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

# pyflame: 1 (time) 2 (interval)
function oflame() {
	PIDFile="/tmp/odoo-dev.pid"
	PID=$(<"$PIDFile")
	OUTPUT="/tmp/oflame.flame"
	GRAPH="/tmp/oflame.svg"
	sudo pyflame --exclude-idle -s ${1-10} -r ${2-0.0001} -p ${PID} -o ${OUTPUT}
	~/FlameGraph/flamegraph.pl --width 1900 ${OUTPUT} > ${GRAPH}
	webbrowser ${GRAPH}
}

function odoo-bin() {
	RES="./odoo-bin $(odoo-bin-params $*)"
	echo "Executing: ${RES}"
	eval $RES
}

function odoo-bin-params() {
	branch=`git branch | grep \*`
	branch=${branch/\(HEAD detached at odoo-dev\/}
	branch=${branch/\(HEAD detached from odoo-dev\/}
	branch=${branch/\(no branch, rebasing }
	branch=${branch/\)}
	d=${branch/\* }
	edition=${1}
	shift 1
	rest=$*

	addons_path="~/repo/odoo/odoo/addons,~/repo/odoo/addons"
	if [[ ${edition} == *"e"* ]]; then
		addons_path="${addons_path},~/repo/enterprise/"
		d="${d}-e"
	fi
	if [[ ${edition} == *"t"* ]]; then
		addons_path="${addons_path},~/repo/design-themes/"
		d="${d}-t"
	fi
	if [[ ${edition} == *"d"* ]]; then
		addons_path="${addons_path},~/repo/big-data/"
		d="${d}-d"
	fi
	echo "-d ${d} --addons-path ${addons_path} ${rest}"
}

function clearsqllog() {
	sudo rm /var/log/postgresql/postgresql-9.5-main.log*
	sudo service postgresql restart
}

function obadger() {
	pgbadger /var/log/postgresql/postgresql-9.5-main.log -o /tmp/postgresql.html
	webbrowser /tmp/postgresql.html
}


function curltime() {
	curl -so /dev/null -w '%{time_total}\n' $*
}

function manycurltime() {
	max=${1}
	shift 1
	for((i=0; i<= ${max}; i++))
	do
		curltime $*
	done
}

function gogogo() {
	tog
	tos
	subl
	firefox &
}

function logmodules () {
	cat ${1} | grep "creating or updating database tables" | sed 's/.*registry\: module \(.*\):.*/\1/' > mod.log
}

function ocoverage() {
	# 1st param: the source to cover
	OPEN=$1
	SOURCE=$2
	CMD=$3
	shift 3

	RES="coverage run --branch --source=${SOURCE} ${CMD} $(odoo-bin-params $*)"
	echo "Executing: ${RES}"
	eval $RES

	# report
	htmldir=/tmp/htmlcov
	rm -r $htmldir
	coverage html -d $htmldir
	if [ "$OPEN" -eq "1" ]; then
		webbrowser "${htmldir}/index.html"
	fi
}

function otunnel() {
	ssh odoo-dev@stheys.com -f -N -T -R 18069:localhost:8069
}

function killotunnel() {
	ps aux | grep 'ssh odoo-dev@stheys.com' | grep -v grep | awk '{print $2}' | xargs -r kill;
}

