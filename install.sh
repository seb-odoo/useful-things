#!/bin/bash

ssh-keygen
sudo apt install git

wget -qO - https://download.sublimetext.com/sublimehq-pub.gpg | sudo apt-key add -
echo "deb https://download.sublimetext.com/ apt/stable/" | sudo tee /etc/apt/sources.list.d/sublime-text.list
sudo apt-get update

sudo apt-get install sublime-text git-cola vim

git clone https://github.com/magicmonty/bash-git-prompt.git ~/.bash-git-prompt --depth=1

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if [ -z "$1" ]; then
    REPO="${DIR}/.."
else
    REPO="$1"
fi

ln -s "${DIR}/.odoorc-dev" ~/.odoorc
ln -s "${DIR}/.gitconfig" ~/.gitconfig
ln -s "${DIR}/.eslintrc" ~/.eslintrc
ln -s "${DIR}/.odoorc-dev" "${REPO}/.odoorc"
ln -s "${DIR}/.flake8" "${REPO}/.flake8"
ln -s "${DIR}/odoo.sublime-project" "${REPO}/odoo.sublime-project"
ln -s "${DIR}/.terminator-config" ~/.config/terminator/config

OC_REPO="${REPO}/odoo"
git clone git@github.com:odoo/odoo.git -v -o odoo "${OC_REPO}"
(cd "${OC_REPO}" && git remote add odoo-dev git@github.com:odoo-dev/odoo.git)

OE_REPO="${REPO}/enterprise"
git clone git@github.com:odoo/enterprise.git -v -o odoo "${OE_REPO}"
(cd "${OE_REPO}" && git remote add odoo-dev git@github.com:odoo-dev/enterprise.git)

OT="${REPO}/design-themes"
git clone git@github.com:odoo/design-themes.git -v -o odoo "${OT}"
(cd "${OT}" && git remote add odoo-dev git@github.com:odoo-dev/design-themes.git)
