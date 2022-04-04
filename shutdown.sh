#! /bin/sh
########################################################################
# darqos
# Copyright (C) 2022 David Arnold

# Walk the name server, and shut down all active types/tasks.

kill_proc() {
  f="$TMPDIR/$1.pid"
  if test -e "$f"; then
    PID="`cat $f`"
    while test `ps -p $PID | grep "^[ ]*$PID[ ]" | wc -l` -eq 1 ; do
      echo "Found process $PID. Sending INT signal."
      kill $PID
      sleep 1
    done
    echo "No precess with $PID running"
    rm $f
    unset
  fi
}


# Shut down services.
kill_proc "history"
#kill_proc "index"
#kill_proc "metadata"
#kill_proc "security"
kill_proc "storage"
#kill_proc "terminal"
