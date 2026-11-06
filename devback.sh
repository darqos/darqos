#! /bin/bash

TARGET=latest

sqlite3 run/storage.sqlite ".backup 'backup/$TARGET/storage.sqlite'"
sqlite3 run/history.sqlite ".backup 'backup/$TARGET/history.sqlite'"
sqlite3 run/index.sqlite ".backup 'backup/$TARGET/index.sqlite'"
sqlite3 run/kb.sqlite ".backup 'backup/$TARGET/kb.sqlite'"
cp -r  run/bootvec.json run/security.json backup/$TARGET/
