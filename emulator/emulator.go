package main

import (
	"./browser"
	"./spawn"
	"./util"
	"flag"
	"fmt"
	"math/rand"
	"time"
)

const MAX_SLEEP = 10

func runTask(taskId int) int {
	switch taskId {
	case 0:
		fmt.Println("Opening website...")
		return browser.OpenWebsite()
	case 1:
		fmt.Println("Opening file...")
		return spawn.Open(util.SelectRandomFile())
	default:
		fmt.Println("Should not get here...")
	}
	return 0
}

func randomlyKillTask(taskPids []int) []int {
	i := rand.Intn(len(taskPids))
	err := spawn.Kill(taskPids[i])
	fmt.Println(err)
	if err == nil {
		fmt.Printf("Killed task with PID=%v...\n", taskPids[i])
		taskPids = append(taskPids[:i], taskPids[i+1:]...)
	}
	return taskPids
}


func emulationLoop(tasks []int) {
	var taskPids []int
	loopCount := 0
	for {
	if pid := runTask(rand.Intn(len(tasks))); pid != 0 {
		taskPids = append(taskPids, pid)
	}
	taskPids = randomlyKillTask(taskPids)
	if loopCount > 3 && (loopCount % 3 == 0 || loopCount % 7 == 0) {
		taskPids = randomlyKillTask(taskPids)
	}
	time.Sleep(time.Duration(rand.Intn(MAX_SLEEP)) * time.Second)
	fmt.Println("Sleeping... ")
	loopCount++
	}
}


func main() {
	var tasks util.TaskFlags
	rand.Seed(time.Now().UnixNano())
	flag.Var(&tasks, "tasks", "Comma separated list of tasks to execute")
	flag.Parse()
	emulationLoop(util.StringToIntSlice(tasks))
}
