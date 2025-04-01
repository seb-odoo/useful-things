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

alias gbd="git branch -D"
alias gis="git status"
alias gil="git log"
alias gfo='git fetch odoo -p'
alias gfa='git fetch -j2 --multiple odoo odoo-dev -p'
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
alias gr153='git rebase odoo/saas-15.3'
alias gr16='git rebase odoo/16.0'
alias gr163='git rebase odoo/saas-16.3'
alias gr164='git rebase odoo/saas-16.4'
alias gr17='git rebase odoo/17.0'
alias gr171='git rebase odoo/saas-17.1'
alias gr172='git rebase odoo/saas-17.2'
alias gr18='git rebase odoo/18.0'
alias gr181='git rebase odoo/saas-18.1'
alias gr182='git rebase odoo/saas-18.2'
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
alias ducks='while read -r line;do du -sh "$line";done < <(ls -1A) | sort -rh | head -n11'
alias qunit_fail="python qunit_until_fail.py -m mail -m mail_enterprise -m test_mail -m im_livechat -m whatsapp -m voip -m hr_expense -m account_accountant -m hr_holidays -m calendar -m documents -m test_mail_full --no-fail-fast -n 100"
alias pfb="python ~/repo/useful-things/fetch_bundle.py"
alias hoot='npm run start --'
alias hoot_mail='npm run start -- -m "@mail"'

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
	source ~/virtualenvs/odoo17/bin/activate
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
	FULL_NAME="${BASE}-${BRANCH}--seb"
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

function gnb15()
{
    gnb "15.0" $*
}

function gnbm()
{
    gnb "master" $*
}

# new local branch based on remote branch
function gnbr() {
	BRANCH=${1/odoo-dev:}
	git fetch odoo-dev +refs/heads/$BRANCH:refs/remotes/odoo-dev/$BRANCH
	git checkout -b $BRANCH "odoo-dev/${BRANCH}"
    git checkout $BRANCH

    read -p "About to reset --hard $BRANCH, type y to confirm: "  yes

    if [ "$yes" == "y" ]; then
        git reset --hard "odoo-dev/$BRANCH"
    else
        echo "not reseting"
    fi
	git push --set-upstream odoo-dev $BRANCH
}

# hard reset to remote branch
function grhr() {
	BRANCH=`git branch | grep \*`
	BRANCH=${BRANCH/\* }
	git fetch odoo-dev +refs/heads/$BRANCH:refs/remotes/odoo-dev/$BRANCH

	read -p "About to reset --hard $BRANCH, type y to confirm: "  yes

	if [ "$yes" == "y" ]; then
	    git reset --hard "odoo-dev/$BRANCH"
	else
	    echo "not reseting"
	fi
}

# checkout an existing branch
function gcb() { git checkout "${1}-${2}--seb"; }


# opens the PR creation page on odoo-dev for master-discuss-refactoring depending on the current branch
function prdr() {
    BRANCH=`git branch | grep \*`
    BRANCH=${BRANCH/\* }
    REMOTE=${PWD##*/}
    webbrowser "https://github.com/odoo-dev/${REMOTE}/compare/master-discuss-refactoring...odoo-dev:${BRANCH}?expand=1";
}

# kill stuck odoo process, by rde-odoo
function killodoo() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill; }

function killodoo9() { ps aux | grep 'odoo-bin' | grep -v grep | awk '{print $2}' | xargs -r kill -9; }

function webbrowser() { python3 -m webbrowser $1; }

function task() { webbrowser "https://www.odoo.com/web#id=${1}&action=333&active_id=587&model=project.task&view_type=form&menu_id=4720"; }
function runbot() { webbrowser "https://runbot.odoo.com/odoo/action-573/${1}"; }
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

function odoo-bin-params() {
    edition=${1}
    shift 1
    rest=$*
    d="$(branchdb ${edition})"
	cli=""
	addons_path="~/repo/odoo/odoo/addons,~/repo/odoo/addons"
	if [[ "${edition}" == *"s"* ]]; then
		cli='shell '
	fi
    if [[ "${edition}" == *"p"* ]]; then
        cli='populate '
    fi
	if [[ "${edition}" == *"e"* ]]; then
		addons_path="${addons_path},~/repo/enterprise/"
	fi
	if [[ "${edition}" == *"t"* ]]; then
		addons_path="${addons_path},~/repo/design-themes/"
	fi
	if [[ "${edition}" == *"d"* ]]; then
		addons_path="${addons_path},~/repo/big-data/"
	fi
	echo "${cli}-d ${d} --addons-path ${addons_path} ${rest}"
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
	firefox &
	tog &
	code &
	git-cola -r ~/repo/odoo &
	git-cola -r ~/repo/enterprise &
	git-cola -r ~/repo/upgrade &
	tos &
	nohup google-chrome-stable > /dev/null 2>&1 &
	nohup flatpak run com.discordapp.Discord > /dev/null 2>&1 &
	echo "All set, have a wonderful day!" &
	date
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

function fixwindow() {
    marco --no-composite --replace &
}

function fixusb() {
    if [[ $EUID != 0 ]] ; then
      echo This must be run as root!
      exit 1
    fi

    for xhci in /sys/bus/pci/drivers/?hci_hcd ; do

      if ! cd $xhci ; then
        echo Weird error. Failed to change directory to $xhci
        exit 1
      fi

      echo Resetting devices from $xhci...

      for i in ????:??:??.? ; do
        echo -n "$i" > unbind
        echo -n "$i" > bind
      done
    done
}

# Rebase progressive, the goal is to rebase commit by commit of the target
# branch to be able to resolve conflicts as soon as they happen instead of
# having a big batch of conflicts at the end.
# This is much slower but much easier to handle.
# When a conflict arise, after it is resolved, the same command can be called
# again.
# Typically example: grebaseprog HEAD~1 odoo/master (1 should be the number of commits to be rebased on the current branch)
function grebaseprog() {
    START=$1
    END=$2
    TMPFILE="/tmp/rebase.sh"
    git log --pretty=format:"echo \"rebasing onto %h\" && git rebase %h && \\" --reverse "${START}..${END}" > $TMPFILE
    chmod +x $TMPFILE
    . $TMPFILE
    rm $TMPFILE
}


# ./../upgrade/tools/test-upgrade.py -c saas-13.3 master-clean-notification-seb --auto-drop -i snailmail

# Reminder of what to type to get the JS env from the browser console
function getjsenv() {
    echo "
    odoo.define(function (require) { window.env = require('web.env'); window.tour = require('web_tour.tour'); } );"
}

# Get and install an Odoo db from a dump zip
# Example: odooget et https://runbot136.odoo.com/runbot/static/build/44255209-saas-16-3/logs/44255209-saas-16-3-all.zip
function odooget() {
    zipurl=$2
    dbname=$(branchdb $1)
    zipname=$(basename "$zipurl")
    echo "about to restore zip '$zipname' into db '$dbname'"
    mkdir /tmp/restore-$dbname
    echo "### downloading"
    wget $zipurl -P /tmp/restore-$dbname -q
    unzip -q /tmp/restore-$dbname/$zipname -d /tmp/restore-$dbname
    echo "### restoring filestore"
    rm -r ~/.local/share/Odoo/filestore/$dbname
    mkdir ~/.local/share/Odoo/filestore/$dbname
    mv /tmp/restore-$dbname/filestore/* ~/.local/share/Odoo/filestore/$dbname
    echo "### restoring db"
    dropdb $dbname
    createdb $dbname
    psql -q $dbname < /tmp/restore-$dbname/dump.sql
    echo "### cleaning"
    rm -r /tmp/restore-$dbname
}

function clean-remote-branches() {
	git branch -r |
	grep odoo-dev/ |
	grep -v '>' |
	grep -v 'seb' |
	xargs -L1 |
	awk '{sub(/origin\//,"");print}' |
	xargs git branch -dr
}

function start_sfu() {
	PUBLIC_IP="127.0.0.1"
	echo "PUBLIC_IP: $PUBLIC_IP"
	AUTH_KEY=abc123 DEBUG=mediasoup* LOG_COLOR=1 LOG_LEVEL=debug LOG_TIMESTAMP=1 PUBLIC_IP="$PUBLIC_IP" WORKER_LOG_LEVEL=debug npm run start
}
