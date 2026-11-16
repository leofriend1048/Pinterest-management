#!/bin/sh
# Pinterest Ads MCP installer.
#
#   curl -fsSL https://raw.githubusercontent.com/leofriend1048/Pinterest-management/mcp-server/install.sh | sh
#
# With a team setup code:
#
#   curl -fsSL .../install.sh | sh -s -- --code pam1_...
#
# Installs uv if it is missing, installs the `pinterest-ads-mcp` command, then
# starts the guided setup. Safe to run again to update.
set -eu

REPO="${PINTEREST_ADS_MCP_REPO:-https://github.com/leofriend1048/Pinterest-management}"
REF="${PINTEREST_ADS_MCP_REF:-mcp-server}"

say()  { printf '  %s\n' "$1"; }
fail() { printf '\n  \033[31m✗\033[0m %s\n\n' "$1" >&2; exit 1; }

printf '\n  \033[1;38;2;230;0;35mPinterest Ads MCP\033[0m\n\n'

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) fail "This installer supports macOS and Linux. On Windows, use WSL." ;;
esac

command -v git >/dev/null 2>&1 || fail "git is required. On a Mac, run: xcode-select --install"
command -v curl >/dev/null 2>&1 || fail "curl is required."

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (a small Python tool manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
    || fail "Could not install uv. See https://docs.astral.sh/uv/ and run this again."
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv was installed but is not on your PATH. Open a new terminal and run this again."

say "Installing pinterest-ads-mcp…"
uv tool install --force --quiet --python 3.12 "git+${REPO}@${REF}" \
  || fail "Install failed. If the repository is private, make sure you can 'git clone ${REPO}'."

BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")/pinterest-ads-mcp"
[ -x "$BIN" ] || fail "Installed, but could not find the command at $BIN."

case ":$PATH:" in
  *":$(dirname "$BIN"):"*) ;;
  *) uv tool update-shell >/dev/null 2>&1 || true ;;
esac

# The script is usually piped into sh, so give the setup the real terminal.
if (: </dev/tty) 2>/dev/null; then
  exec "$BIN" setup "$@" </dev/tty
else
  say "Installed. Now run: pinterest-ads-mcp setup"
fi
