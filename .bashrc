export PATH=$PATH:/home/seb/repo/odoo-ops-tools

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
alias gr12='git rebase odoo/12.0'
alias gr123='git rebase odoo/saas-12.3'
alias gr13='git rebase odoo/13.0'
alias gr14='git rebase odoo/14.0'
alias grc='git rebase --continue'
alias gcrm='git commit --reuse-message=HEAD@{1}'
alias gca='git commit --amend'
alias gcane='git commit --amend --no-edit'
alias gcad='git commit --amend --date="$(date -R)"'
alias gcadne='git commit --amend --date="$(date -R)" --no-edit'
alias grs='git reset --soft HEAD~1'
alias gwhere='git branch -r --contains'

function tog() {
	terminator -l 'odoo gits' </dev/null &>/dev/null &
}
function tos() {
	terminator -l 'odoo shell' </dev/null &>/dev/null &
}

alias tig="tig --max-count=100"

alias ubash="source ~/.bashrc"

alias hackchromeheadless="google-chrome --headless --remote-debugging-port=8071"

function odoo-venv() {
	source ~/virtualenvs/odoo/bin/activate
}

alias list-swap="smem -s swap"

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

function gnb14()
{
    gnb "14.0" $*
}

function gnbm()
{
    gnb "master" $*
}

# fetch, create, and push a new branch based on master mail owl
function gnbmmo()
{
    BASE='master-mail-owl'
    BRANCH=$1
    FULL_NAME="${BASE}-${BRANCH}-seb"
    git fetch odoo-dev $BASE
    git checkout -b $FULL_NAME "odoo-dev/${BASE}" --no-track
    if [ ! -z "$FULL_NAME" ]; then
        # we need to push because the upstream branch doesn't exist yet
        git push -u odoo-dev $FULL_NAME
    fi
}

# new local branch based on remote branch
function gnbr() {
	BRANCH=${1/odoo-dev:}
	git fetch odoo-dev $BRANCH
	git checkout -b $BRANCH "odoo-dev/${BRANCH}"
}

# hard reset to remote branch
function grhr() {
	BRANCH=`git branch | grep \*`
	BRANCH=${BRANCH/\* }
	git fetch odoo-dev $BRANCH

	read -p "About to reset --hard $BRANCH, type y to confirm: "  yes

	if [ "$yes" == "y" ]; then
	    git reset --hard "odoo-dev/$BRANCH"
	else
	    echo "not reseting"
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

function otrace() {
	PIDFile="/tmp/odoo-dev.pid"
	PID=$(<"$PIDFile")
	kill -3 ${PID}
}

# pyflame: 1 (time) 2 (interval)
function oflame() {
	PIDFile="/tmp/odoo-dev.pid"
	PID=$(<"$PIDFile")
	OUTPUT="/tmp/oflame.flame"
	GRAPH="/tmp/oflame.svg"
	sudo pyflame --exclude-idle -s ${1-10} -r ${2-0.0001} -p ${PID} -o ${OUTPUT}
	~/repo/FlameGraph/flamegraph.pl --width 1900 ${OUTPUT} > ${GRAPH}
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
	branch=${branch/\(HEAD detached at }
	branch=${branch/\(HEAD detached from odoo-dev\/}
	branch=${branch/\(HEAD detached from }
	branch=${branch/\(no branch, rebasing }
	branch=${branch/\(no branch, bisect started on }
	branch=${branch/\)}
	d=${branch/\* }
	edition=${1}
	shift 1
	rest=$*
	shell=""
	addons_path="~/repo/odoo/odoo/addons,~/repo/odoo/addons"
	if [[ "${edition}" == *"s"* ]]; then
		shell='shell '
	fi
	if [[ "${edition}" == *"e"* ]]; then
		addons_path="${addons_path},~/repo/enterprise/"
		d="${d}-e"
	fi
	if [[ "${edition}" == *"t"* ]]; then
		addons_path="${addons_path},~/repo/design-themes/"
		d="${d}-t"
	fi
	if [[ "${edition}" == *"d"* ]]; then
		addons_path="${addons_path},~/repo/big-data/"
		d="${d}-d"
	fi
	echo "${shell}-d ${d} --addons-path ${addons_path} ${rest}"
}

alias obet="odoo-bin et"

function otf() {
	odoo-bin et --stop-after-init --test-file $*
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
	allTimes=()
	shift 1
	for((i=0; i < ${max}; i++))
	do
		cur=$(curltime $*)
		echo $cur
		allTimes[i]=$cur
	done
	tot=0
	for i in ${allTimes[@]}
	do
		cmd="$tot+$i"
		tot=$(echo $cmd | sed -u 's/,/./g' | bc -l)
	done
	echo "$tot $max" | awk '{printf "%0.4f\n", $1 / $2}'
}

function gogogo() {
	tog &
	tos &
	subl &
	firefox &
	git-cola -r ~/repo/odoo &
	echo "All set, have a wonderful day!"
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

GIT_PROMPT_ONLY_IN_REPO=1
GIT_PROMPT_THEME=Single_line_Ubuntu

GIT_PROMPT_PREFIX="["
GIT_PROMPT_SUFFIX="]"
GIT_PROMPT_SEPARATOR="|"
GIT_PROMPT_STAGED="${Red}●${ResetColor}"
GIT_PROMPT_CONFLICTS="${Red}✖${ResetColor}"
GIT_PROMPT_CHANGED="${Blue}✚${ResetColor}"
GIT_PROMPT_UNTRACKED="${Cyan}…${ResetColor}"
GIT_PROMPT_STASHED="${BoldBlue}⚑${ResetColor}"
GIT_PROMPT_CLEAN="${BoldGreen}✔${ResetColor}"

GIT_PROMPT_COMMAND_OK="${Green}✔"
GIT_PROMPT_COMMAND_FAIL="${Red}✘"

GIT_PROMPT_END_USER="${ResetColor} $ "
GIT_PROMPT_START_USER="${Cyan}${PathShort}${ResetColor}"
GIT_PROMPT_END_ROOT="${BoldRed} # "

GIT_PROMPT_IGNORE_STASH="1"
GIT_PROMPT_BRANCH="${BoldBlue}"
GIT_PROMPT_SYMBOLS_AHEAD="${BoldGreen}↑ "
GIT_PROMPT_SYMBOLS_BEHIND="${BoldRed}↓ "

source ~/.bash-git-prompt/gitprompt.sh


function usbkeyopen() {
	sudo cryptsetup luksOpen /dev/sdb USBDrive
	sudo mount /dev/mapper/USBDrive /mnt/USBDrive
}

function usbkeyclose() {
	sudo umount /mnt/USBDrive
	sudo cryptsetup luksClose USBDrive
}

function usbkeysave() {
	rsync -aAXvi --progress --delete ~/.config/ /mnt/USBDrive/.config/
	rsync -aAXvi --progress --delete ~/.ssh/ /mnt/USBDrive/.ssh/
}

function owl-update() {
	cd ~/repo/owl
	git checkout master
	git rebase origin/master
	npm run build
	cp dist/owl.js ~/repo/odoo/addons/web/static/lib/owl/owl.js
}
