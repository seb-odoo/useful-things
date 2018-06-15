#!/bin/bash

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
