#!/bin/sh
# If the GitHub CLI is logged in, let git use it for github.com. Runs on every start, so the credential helper in
# ~/.gitconfig always points at this image's gh (a home volume moved from another setup may point elsewhere).
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh auth setup-git || echo "gh auth setup-git failed" >&2
fi
exit 0
