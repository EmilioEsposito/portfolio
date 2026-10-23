#!/bin/sh
# Debian/Ubuntu: retain unaffected emSign roots. Safe if the distro removed G3.
set -eu
sed -i 's|^mozilla/emSign_ECC_Root_CA_-_G3.crt$|!mozilla/emSign_ECC_Root_CA_-_G3.crt|' /etc/ca-certificates.conf
update-ca-certificates --fresh
