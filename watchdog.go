package main

import (
	"errors"
	"os"
	"strconv"
	"syscall"

	"golang.org/x/sys/unix"
)

func projectLock() (*os.File, bool, error) {
	// Every entry point shares the lock, including foreground and Cron starts.
	// --watchdog remains accepted for compatibility with existing Cron entries.
	var lock *os.File
	if value := os.Getenv("GATESTREAM_LOCK_FD"); value != "" {
		fd, err := strconv.ParseUint(value, 10, 32)
		if err == nil {
			inherited := os.NewFile(uintptr(fd), ".watchdog.lock")
			actual, statErr := inherited.Stat()
			expected, pathErr := os.Stat(".watchdog.lock")
			if statErr == nil && pathErr == nil && os.SameFile(actual, expected) {
				lock = inherited
			}
		}
	}
	if lock == nil {
		var err error
		lock, err = os.OpenFile(".watchdog.lock", os.O_CREATE|os.O_RDWR, 0600)
		if err != nil {
			return nil, false, errors.New("could not open the project lock")
		}
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, false, nil
		}
		return nil, false, errors.New("could not acquire the project lock")
	}
	if _, err := unix.FcntlInt(lock.Fd(), unix.F_SETFD, 0); err != nil {
		lock.Close()
		return nil, false, errors.New("could not preserve the project lock")
	}
	if err := os.Setenv("GATESTREAM_LOCK_FD", strconv.FormatUint(uint64(lock.Fd()), 10)); err != nil {
		lock.Close()
		return nil, false, err
	}
	return lock, true, nil
}
