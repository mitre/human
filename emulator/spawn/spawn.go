package spawn

import (
	"../util"
	"fmt"
	"os"
	"os/exec"
	"time"
)

var GuiBinary []string

func init() {
	GuiBinary = util.SelectOsOpenCmd()
}

func Open(file string) int {
	return run(build(GuiBinary[0], append(GuiBinary[1:], file)))
}

func OpenWith(application string, args []string) int {
	return run(build(application, args))
}

func Kill(pid int) error {
	proc, err := os.FindProcess(pid)
	if err == nil {
		err := proc.Kill()
		fmt.Println(err)
		if  err == nil {
			return nil
		}
		return fmt.Errorf("could not kill process with PID=%v", pid)
	}
	return fmt.Errorf("could find process with PID=%v", pid)
}


/* Private */

func build(application string, args []string) *exec.Cmd {
	return exec.Command(application, args...)
}

func run(cmd *exec.Cmd) int {
	err := cmd.Start()
	if err == nil && appearsSuccessful(cmd, 3 * time.Second) {
		return cmd.Process.Pid
	}
	return 0
}


// Copyright 2016 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.
// appearsSuccessful reports whether the command appears to have run successfully.
// If the command runs longer than the timeout, it's deemed successful.
// If the command runs within the timeout, it's deemed successful if it exited cleanly.
func appearsSuccessful(cmd *exec.Cmd, timeout time.Duration) bool {
	errc := make(chan error, 1)
	go func() {
		errc <- cmd.Wait()
	}()

	select {
	case <-time.After(timeout):
		return true
	case err := <-errc:
		return err == nil
	}
}