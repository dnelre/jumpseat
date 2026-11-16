#!/bin/sh
# Installs Claude Code into the home volume on the first start; afterwards it keeps itself up to date.
# Never stops the desktop from starting.
if [ ! -x "$HOME/.local/bin/claude" ]; then
  echo "Installing Claude Code..."
  curl -fsSL https://claude.ai/install.sh | bash || echo "Claude Code install failed; it is retried on the next start." >&2
fi
exit 0
