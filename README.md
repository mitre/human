# Human

Plugin supplying CALDERA with human emulation capabilities.

## Overview

The Human plugin allows you to build “Humans” that will perform user actions on a target system as a means to obfuscate red actions by Caldera. Each human is built for a specific operating system and leverages the Chrome browser along with other native OS applications to perform a variety of tasks. Additionally, these humans can have various aspects of their behavior “tuned” to add randomization to the behaviors on the target system.

## Requirements

### CALDERA Server
On the CALDERA server, there are additional python packages required to use the Human plugin. These python packages can be installed by navigating to the plugins/human/directory and running the command pip3 install -r requirements.txt

### Target Machine 
* Linux, MacOS, or Windows (with Powershell)
* Python3
* Python package `virtualenv`
* Google Chrome

[Read the full docs](https://github.com/mitre/caldera/wiki/Plugins-human)
