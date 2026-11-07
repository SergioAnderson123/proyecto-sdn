#!/bin/bash

set -e

mkdir -p /var/run/openvswitch

# Create the database if it doesn't exist
if [ ! -f /etc/openvswitch/conf.db ]; then
    ovsdb-tool create \
        /etc/openvswitch/conf.db \
        /usr/share/openvswitch/vswitch.ovsschema
fi

# Start ovsdb-server if it isn't already running
if ! pgrep -x ovsdb-server >/dev/null; then
    ovsdb-server \
        /etc/openvswitch/conf.db \
        --remote=punix:/var/run/openvswitch/db.sock \
        --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
        --pidfile \
        --detach
fi

# Initialize the database
ovs-vsctl --no-wait init

# Start ovs-vswitchd if it isn't already running
if ! pgrep -x ovs-vswitchd >/dev/null; then
    ovs-vswitchd \
        --pidfile \
        --detach
fi

echo "Open vSwitch is running."

ovs-vsctl show

exec "$@"

