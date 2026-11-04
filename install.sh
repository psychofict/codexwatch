#!/usr/bin/env bash
# Install codexwatch for this user, from this clone or from GitHub.
#
#   ./install.sh               install the `codexwatch` command (and, on Linux, its app-menu entry)
#   ./install.sh --tray        also start the tray icon now and at every login (Linux)
#   ./install.sh --desktop     also put a shortcut on the desktop (Linux)
#   ./install.sh --swiftbar    also add the menu-bar plugin (macOS, SwiftBar)
#   ./install.sh --uninstall   remove the command, the shortcuts, the autostart entry and the plugin
#
# Your history (the codexwatch folder) is never deleted; `codexwatch config path`
# shows where it is.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE"
[ -f "$HERE/pyproject.toml" ] || SRC="codexwatch-cli"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/codexwatch-tray.desktop"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

say() { printf '%s\n' "$*"; }
has() { command -v "$1" >/dev/null 2>&1; }

tray=0 swiftbar=0 desktop=0 uninstall=0
for a in "$@"; do
  case "$a" in
    --tray) tray=1 ;;
    --desktop) desktop=1 ;;
    --swiftbar) swiftbar=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) say "unknown option: $a"; exit 2 ;;
  esac
done

swiftbar_dir() {
  defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true
}

if [ "$uninstall" = 1 ]; then
  pkill -f "codexwatch tray" 2>/dev/null || true
  # The package is codexwatch-cli on PyPI; early clones installed it as codexwatch.
  for pkg in codexwatch-cli codexwatch; do
    if has pipx && pipx list --short 2>/dev/null | grep -q "^$pkg "; then pipx uninstall "$pkg"; fi
    if has uv && uv tool list 2>/dev/null | grep -q "^$pkg "; then uv tool uninstall "$pkg"; fi
    python3 -m pip uninstall -y "$pkg" >/dev/null 2>&1 || true
  done
  rm -f "$AUTOSTART" "$APPS/codexwatch.desktop" "$DESKTOP_DIR/codexwatch.desktop" "$ICONS/scalable/apps/codexwatch.svg"
  d="$(swiftbar_dir)"; [ -n "$d" ] && rm -f "$d/codexwatch.30s.sh"
  say "codexwatch removed. Your history is kept; delete its folder yourself for a clean wipe."
  exit 0
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { say "codexwatch needs Python 3.9 or newer."; exit 1; }

# From a clone the install is editable: the command runs this folder's code.
editable=""
[ "$SRC" = "$HERE" ] && editable="-e"
if has pipx; then
  pipx install --force $editable "$SRC"
elif has uv; then
  uv tool install --force $editable "$SRC"
else
  python3 -m pip install --user --upgrade $editable "$SRC"
  say "Installed with pip --user. If \`codexwatch\` is not found, add ~/.local/bin to your PATH."
fi
BIN="$(command -v codexwatch || echo "$HOME/.local/bin/codexwatch")"

if [ "$(uname)" = "Linux" ]; then
  # An app-menu entry with the codexwatch icon. It opens the dashboard (or the
  # one already running), and its right-click menu starts the tray.
  mkdir -p "$APPS" "$ICONS/scalable/apps"
  icon="$HERE/codexwatch/web/icon.svg"
  [ -f "$icon" ] || icon="$(python3 -c 'import codexwatch, os; print(os.path.join(os.path.dirname(codexwatch.__file__), "web", "icon.svg"))' 2>/dev/null || true)"
  [ -f "$icon" ] && cp "$icon" "$ICONS/scalable/apps/codexwatch.svg"
  {
    printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=codexwatch" "GenericName=Codex usage" \
      "Comment=Your Codex plan limits, tokens and API value" "Exec=$BIN dashboard" "Icon=codexwatch" \
      "Terminal=false" "Categories=Development;Utility;Monitor;" "Keywords=codex;openai;usage;tokens;limits;quota;" \
      "Actions=tray;" "" "[Desktop Action tray]" "Name=Show the tray widget" "Exec=$BIN tray"
  } > "$APPS/codexwatch.desktop"
  has update-desktop-database && { update-desktop-database "$APPS" >/dev/null 2>&1 || true; }
  has gtk-update-icon-cache && { gtk-update-icon-cache -q -t "$ICONS" >/dev/null 2>&1 || true; }
  say "Added codexwatch to the app menu."
  if [ "$desktop" = 1 ] && [ -d "$DESKTOP_DIR" ]; then
    cp "$APPS/codexwatch.desktop" "$DESKTOP_DIR/codexwatch.desktop"
    chmod +x "$DESKTOP_DIR/codexwatch.desktop"
    has gio && { gio set "$DESKTOP_DIR/codexwatch.desktop" metadata::trusted true 2>/dev/null || true; }
    say "Put a codexwatch shortcut on the desktop."
  fi
fi

if [ "$tray" = 1 ]; then
  if [ "$(uname)" != "Linux" ]; then
    say "--tray is for Linux; on macOS use --swiftbar."
  else
    /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || \
      say "The tray needs GTK bindings: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 (or your distro's)."
    mkdir -p "$(dirname "$AUTOSTART")"
    {
      printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=codexwatch" "Comment=Codex plan limits in the tray" \
        "Exec=$BIN tray" "Icon=codexwatch" "Terminal=false" "X-GNOME-Autostart-enabled=true"
    } > "$AUTOSTART"
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && ! pgrep -f "codexwatch tray" >/dev/null 2>&1; then
      setsid "$BIN" tray >/dev/null 2>&1 < /dev/null &
      say "The tray is running, and starts at every login."
    else
      say "The tray starts at every login. Start it now with: codexwatch tray &"
    fi
  fi
fi

if [ "$swiftbar" = 1 ]; then
  d="$(swiftbar_dir)"
  if [ -z "$d" ]; then
    say "SwiftBar was not found. Install it (brew install --cask swiftbar), choose a plugin folder, then run this again."
  else
    cp "$HERE/extras/swiftbar/codexwatch.30s.sh" "$d/" && chmod +x "$d/codexwatch.30s.sh"
    say "Added the menu-bar plugin to $d."
  fi
fi

say ""
say "Done. Try:  codexwatch            the overview"
say "            codexwatch dashboard  the web dashboard"
say "            codexwatch demo       the dashboard on invented data"
