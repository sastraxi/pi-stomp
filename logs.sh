#!/usr/bin/env bash
# View mod-ala-pi-stomp service logs on the pistomp device.
#
# Examples:
#   ./logs.sh -f              # (f)ollow live logs
#   ./logs.sh -n 100          # last 100 lines
#   ./logs.sh --since "5 min ago"
ssh pistomp@pistomp.local "sudo journalctl -u mod-ala-pi-stomp $*"
