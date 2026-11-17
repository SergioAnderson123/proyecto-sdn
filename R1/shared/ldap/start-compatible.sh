#!/bin/sh
# Existing migrated LDAP: preserve its configuration and replication verbatim.
# Empty deployment: retain the image's original bootstrap behavior.
set -eu
if [ -s /etc/ldap/slapd.d/cn=config.ldif ]; then
    exec /usr/sbin/slapd -d 0 -h 'ldap:/// ldapi:///' -F /etc/ldap/slapd.d -u openldap -g openldap
fi
if [ -n "$(find /etc/ldap/slapd.d /var/lib/ldap -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo 'LDAP has partial existing data; refusing to bootstrap over it.' >&2
    exit 1
fi
exec /container/tool/run --copy-service
