# Paste this whole file into the cloud environment's "Setup script" field.
# It runs on the cloud VM before Claude Code launches, never on the owner's machine,
# and it always exits 0 so it can never block a session from starting.
# Everything it does is logged to /root/cloud-setup.log for the session to read.
exec > >(tee -a /root/cloud-setup.log) 2>&1
echo "== cloud setup $(date -u +%FT%TZ) pwd=$PWD CLAUDE_PROJECT_DIR=${CLAUDE_PROJECT_DIR:-unset} HOME=${HOME:-unset}"
BRANCH=feat/live-briefs-and-reader-notes
R=""
for d in "${CLAUDE_PROJECT_DIR:-}" "$PWD" $(find / -xdev -maxdepth 5 -type d -name .git 2>/dev/null | xargs -r -n1 dirname); do
  if [ -n "$d" ] && [ -d "$d/.git" ] && [ -f "$d/pyproject.toml" ] && grep -q "deep" "$d/pyproject.toml" 2>/dev/null; then R="$d"; break; fi
done
echo "repo=${R:-NOT FOUND}"
if [ -n "$R" ]; then
  cd "$R" || true
  git fetch -q origin "$BRANCH" 2>&1 && git checkout -q "$BRANCH" 2>&1 && echo "checked out $BRANCH at $(git rev-parse --short HEAD)"
  [ -f cloud-session/setup.sh ] && bash cloud-session/setup.sh --cloud || echo "cloud-session/setup.sh missing or failed"
fi
echo "== cloud setup end"
true
