#!/bin/bash
# One Workspace, one branch. Installed as /usr/local/bin/git.
#
# Refuses a command that would move this Workspace off its branch BEFORE Git
# touches a file, so a refusal leaves the working directory exactly as it was.
# The HEAD lock behind this gate cannot do that: Git changes files first and
# only then fails to write HEAD.
#
# This stops mistakes, not intent; Workspaces are kept apart by their VM and
# private network. Platform Git calls the real binary and never comes here.
REAL=/usr/bin/git
ROOT=/workspace/repo

refuse() {
  printf 'puppyone: this Workspace stays on one branch, so `%s` is not available here.\n' "$1" >&2
  printf 'Commit on the current branch; your work is saved to the cloud automatically.\n' >&2
  exit 1
}

args=("$@")
global=()
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  case "${args[i]}" in
    -c)
      case "${args[i + 1]}" in alias.*) refuse 'git -c alias.*' ;; esac
      global+=("${args[i]}" "${args[i + 1]}")
      i=$((i + 2))
      ;;
    -C | --git-dir | --work-tree | --namespace | --super-prefix | --config-env)
      global+=("${args[i]}" "${args[i + 1]}")
      i=$((i + 2))
      ;;
    -*)
      global+=("${args[i]}")
      i=$((i + 1))
      ;;
    *) break ;;
  esac
done
[ "$i" -lt "${#args[@]}" ] || exec "$REAL" "$@"

real() { "$REAL" ${global[@]+"${global[@]}"} "$@"; }

sub=${args[i]}
rest=("${args[@]:i+1}")

# An alias from the repository's own config resolves to what it would run.
expansion=$(real config --get "alias.$sub" 2>/dev/null)
case "$expansion" in
  '' | '!'*) ;;
  *)
    read -r -a words <<<"$expansion"
    sub=${words[0]}
    rest=("${words[@]:1}" ${rest[@]+"${rest[@]}"})
    ;;
esac

in_workspace() { [ "$(real rev-parse --show-toplevel 2>/dev/null)" = "$ROOT" ]; }

# checkout both moves HEAD and restores files; only the first is refused.
checkout_moves_head() {
  local positional=() x
  for x in ${rest[@]+"${rest[@]}"}; do
    case "$x" in
      --) return 1 ;;
      -b | -B | --orphan | --orphan=* | --detach | -d | -t | --track | --track=*) return 0 ;;
      -p | --patch | --ours | --theirs | --pathspec-from-file | --pathspec-from-file=*) return 1 ;;
      -) return 0 ;;
      -*) ;;
      *) positional+=("$x") ;;
    esac
  done
  # With a path after it, the first name is only where to restore from.
  [ "${#positional[@]}" -eq 1 ] || return 1
  x=${positional[0]}
  [ "$x" = HEAD ] && return 1
  [ "refs/heads/$x" = "$(real symbolic-ref -q HEAD 2>/dev/null)" ] && return 1
  real rev-parse --verify -q "$x^{commit}" >/dev/null 2>&1
}

blocked=
case "$sub" in
  switch | rebase | bisect | update-ref) blocked="git $sub" ;;
  symbolic-ref)
    # Reading the current branch is ordinary; only pointing HEAD elsewhere moves it.
    positional=0
    for x in ${rest[@]+"${rest[@]}"}; do
      case "$x" in
        -d | --delete) positional=2 ;;
        -m) ;;
        -*) ;;
        *) positional=$((positional + 1)) ;;
      esac
    done
    [ "$positional" -lt 2 ] || blocked='git symbolic-ref'
    ;;
  worktree) [ "${rest[0]}" != add ] || blocked='git worktree add' ;;
  stash) [ "${rest[0]}" != branch ] || blocked='git stash branch' ;;
  branch)
    for x in ${rest[@]+"${rest[@]}"}; do
      case "$x" in -m | -M | --move) blocked='git branch -m' ;; esac
    done
    ;;
  checkout) checkout_moves_head && blocked='git checkout' ;;
esac

if [ -n "$blocked" ] && in_workspace; then refuse "$blocked"; fi
exec "$REAL" "$@"
