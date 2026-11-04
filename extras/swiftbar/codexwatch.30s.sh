#!/usr/bin/env bash
# <xbar.title>codexwatch</xbar.title>
# <xbar.desc>Your Codex 5-hour and weekly limits, and today's API value, in the macOS menu bar.</xbar.desc>
# <xbar.author>Ebenworks</xbar.author>
# <xbar.author.github>psychofict</xbar.author.github>
# <xbar.dependencies>python3,codexwatch</xbar.dependencies>
# <xbar.abouturl>https://github.com/psychofict/codexwatch</xbar.abouturl>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
#
# Copy this file into your SwiftBar or xbar plugin folder. The "30s" in the
# name is how often it refreshes. Menu-bar apps start plugins with a short
# PATH, so the usual install folders are added here.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -n "$SWIFTBAR" ]; then
  exec codexwatch bar -f swiftbar
fi
exec codexwatch bar -f xbar
