#!/bin/sh
# Registers the image's Playwright MCP server with Claude Code (user scope, so in every folder). The agent can then
# open a Chromium window on this desktop that the user watches and takes over over noVNC.
# One profile in the home volume keeps logins across restarts and folders; screenshots and page snapshots go to /tmp
# instead of the open project. An existing "playwright" entry is left alone, so the user's own settings win.
# Runs after claude-code.sh (alphabetical order). Never stops the desktop from starting.
claude="$HOME/.local/bin/claude"
if [ -x "$claude" ] && command -v playwright-mcp >/dev/null 2>&1 \
   && ! jq -e '.mcpServers.playwright' "$HOME/.claude.json" >/dev/null 2>&1; then
  "$claude" mcp add --scope user playwright -- "$(command -v playwright-mcp)" \
    --browser chromium \
    --user-data-dir "$HOME/.cache/playwright-mcp/profile" \
    --output-dir /tmp/playwright-mcp \
    || echo "Registering the Playwright MCP server failed; it is retried on the next start." >&2
fi
exit 0
