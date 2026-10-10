#!/bin/sh
# Harmless end-to-end test action: proves the ARMED -> admin-confirm ->
# execute path works, without touching ES, recalbox.conf or power state.
echo "[test_action] ran at $(date -Iseconds)" >> /tmp/nfc-acr-action-test.log
