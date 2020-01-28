package util

import (
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

type TaskFlags []string

func FindAvailableFiles() []string {
	var files []string
	validDirs := []string{"Documents", "Desktop", "Downloads", "Pictures", "Music"}
	if homeDir, err := os.UserHomeDir(); err == nil {
		for _, d := range validDirs {
			filepath.Walk(filepath.Join(homeDir, d), func(path string, info os.FileInfo, err error) error {
				if err != nil {
					return err
				}
				if !info.IsDir() && info.Name()[0] != '.' {
					files = append(files, path)
				}
				return nil
			})
		}
	}
	return files
}

func SelectRandomFile() string {
	files := FindAvailableFiles()
	return files[rand.Intn(len(files))]
}

func SelectOsOpenCmd() []string {
	switch runtime.GOOS {
	case "darwin":
		return []string{"/usr/bin/open"}
	case "windows":
		return []string{"cmd.exe", "/c", "start"}
	default:
		if os.Getenv("DISPLAY") != "" {
			return []string{"xdg-open"}
		}
	}
	return []string{}
}

func (t *TaskFlags) Set(value string) error {
	for _, task := range strings.Split(value, ",") {
		*t = append(*t, task)
	}
	return nil
}

func (t *TaskFlags) String() string {
	return fmt.Sprint(*t)
}

func StringToIntSlice(vals []string) []int {
	ints := make([]int, len(vals))
	for index, value := range vals {
		ints[index], _ = strconv.Atoi(value)
	}
	return ints
}