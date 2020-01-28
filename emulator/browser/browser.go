package browser

import (
	"../spawn"
	"math/rand"
	"strings"
)

var siteList []string

func init() {
	siteList = strings.Split(websiteString, "\n")
}

func OpenWebsite() int {
	var browsers [][]string
	browsers = append(browsers,
		spawn.GuiBinary,
		[]string{"chrome"},
		[]string{"google-chrome"},
		[]string{"chromium"},
		[]string{"chromium-browser"},
		[]string{"firefox"})

	for _, args := range browsers {
		if pid := spawn.OpenWith(args[0], append(args[1:], getRandomWebsite())); pid != 0 {
			return pid
		}
	}
	return 0
}

/* Private */

func getRandomWebsite() string {
	return "https://" + siteList[rand.Intn(len(siteList))]
}