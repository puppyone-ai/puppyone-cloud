# VM / SSH Agent Access Architecture

This document defines the recommended architecture for letting a local terminal
agent access a remote VM filesystem and process environment through SSH.

## Purpose

PuppyOne can support agents that remain attached to a local terminal while
reading, writing, and executing work inside a remote VM. SSH is the default
transport because it provides command execution, file transfer, port forwarding,
authentication, and auditability through one mature protocol.

The key point is that SSH does not require the local agent to fully enter an
interactive remote terminal. The local agent can stay local and start remote
commands on demand:

```bash
ssh puppyhappy "hostname; whoami; pwd"
ssh puppyhappy "ls -la /data"
ssh puppyhappy "cat /data/report.md"
```

The remote VM executes each command and returns stdout/stderr to the local
agent.

## Architecture

```text
Local terminal
  Local agent process
    remote_exec(command)
      -> local ssh client
        -> SSH transport
          -> remote VM sshd
            -> remote process
            -> remote filesystem

    remote_read(path)
      -> ssh cat / sftp

    remote_write(path, content)
      -> sftp / scp / ssh heredoc

    remote_sync(local, remote)
      -> rsync over SSH
```

The local terminal remains the controlling environment. The remote VM is an
execution and storage target, not a replacement for the local terminal.

## Terminal Session Model

There are two distinct SSH modes:

1. Interactive login:

   ```bash
   ssh puppyhappy
   ```

   The current terminal enters a remote shell until the user exits.

2. Remote command execution:

   ```bash
   ssh puppyhappy "ls -la /root"
   ```

   The local agent starts a local `ssh` process, the remote VM runs one command,
   the output returns to the local agent, and the local session continues
   locally.

For agent integration, prefer remote command execution and structured helper
tools over long-lived interactive shells. A persistent SSH channel or remote
pseudo-terminal can be added later for workloads that need stateful interactive
programs.

## File Access Options

Use the narrowest SSH-based mechanism that fits the task:

| Need | Recommended mechanism | Notes |
|---|---|---|
| Run remote commands | `ssh host "command"` | Default for inspection, builds, tests, and service control. |
| Read one file | `ssh host "cat /path/file"` or SFTP | Good for small or medium text files. |
| Write one file | SFTP, SCP, or guarded shell write | Avoid embedding secrets in shell history or logs. |
| Sync a tree | `rsync -az --delete` over SSH | Best for incremental code/data sync. |
| Browse remote files as local paths | SSHFS | Useful for ad hoc browsing, but less reliable for builds and file watchers. |
| Expose remote service locally | SSH port forwarding | Example: `ssh -L 8080:127.0.0.1:8080 host`. |

SSHFS should not be the default execution model for complex agent work. It can
hide latency, permissions, file event, and disconnect behavior. Prefer running
commands on the remote VM where the files live.

## Credential Model

Do not give agents passwords or paste private keys into chat. The recommended
credential model is:

```text
Private key: local machine only
Public key: remote VM authorized_keys
Agent: invokes local ssh client
Remote VM: accepts only approved public keys
```

Generate a dedicated key for the VM or for the agent integration:

```bash
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/puppyhappy_codex -C "puppyone-agent-puppyhappy"
```

Install only the public key on the VM:

```bash
ssh-copy-id -i ~/.ssh/puppyhappy_codex.pub agent@143.198.223.32
```

Then configure the local SSH alias:

```sshconfig
Host puppyhappy
    HostName 143.198.223.32
    User agent
    IdentityFile ~/.ssh/puppyhappy_codex
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 120
```

After that, the agent can use:

```bash
ssh puppyhappy "hostname; whoami; pwd"
```

without receiving or storing the VM password.

## Security Defaults

The secure default is a dedicated non-root VM user for agent access.

Recommended server posture:

- Create a dedicated user such as `agent`.
- Grant access only to the directories that agent should manage.
- Avoid password authentication after bootstrapping the public key.
- Disable direct root SSH login for normal operation.
- Do not grant blanket `sudo` by default.
- If `sudo` is required, allow only specific commands.
- Rotate or remove the public key when access should end.
- Keep server-side command logs and SSH auth logs available for audit.

Recommended `sshd_config` posture:

```sshconfig
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin no
```

Reload SSH after changing server configuration:

```bash
sudo systemctl reload ssh
```

For production systems, prefer short-lived credentials when possible: SSH
certificates, Tailscale SSH, Teleport, or another access broker can issue
time-bound access instead of relying on long-lived keys.

## Agent Tool Contract

Expose remote access to the agent as explicit tools instead of arbitrary hidden
side effects:

```text
remote_exec(host, command, cwd?, timeout?)
remote_list(host, path)
remote_read(host, path, max_bytes?)
remote_write(host, path, content, mode?)
remote_sync(host, source, target, delete?)
remote_port_forward(host, local_port, remote_host, remote_port)
```

Each tool should record:

- target host alias
- command or path
- working directory
- exit code
- stdout/stderr summary
- start and end time
- whether the operation was read-only or mutating

This keeps the product model clear: the agent is local, the VM is remote, and
every remote action is observable.

## MCP Layer

If PuppyOne wants a standard tool protocol above SSH, use MCP as the agent
interface and SSH as the transport/backend:

```text
Agent
  -> MCP tool interface
    -> SSH/SFTP/rsync implementation
      -> remote VM
```

The MCP server can run locally and invoke `ssh`, or it can run on the remote VM
and be reached through an SSH tunnel. The local-MCP approach is simpler for the
first implementation because credentials stay in the user's existing SSH setup.

## Write Safety

Remote writes must be deliberate because the VM may hold production data or
long-lived state.

Recommended behavior:

- Treat `ls`, `cat`, `pwd`, `whoami`, `hostname`, `find`, and log inspection as
  read-only operations.
- Require explicit intent for write operations such as creating files, editing
  config, installing packages, restarting services, or deleting data.
- Write test files under `/tmp` unless a task names a target directory.
- Prefer atomic writes for important files: write to a temp path, validate, then
  move into place.
- Never run destructive commands such as `rm -rf`, disk formatting, or service
  replacement without an explicit user request.

Example safe write test:

```bash
ssh puppyhappy "printf '%s\n' 'hello from local agent' > /tmp/puppyone-ssh-test.txt"
ssh puppyhappy "cat /tmp/puppyone-ssh-test.txt"
```

## Recommended Default

For PuppyOne's agent-to-VM access, use:

```text
SSH key authentication
+ dedicated non-root VM user
+ local SSH config alias
+ remote command execution by default
+ SFTP/rsync for file movement
+ optional MCP tool wrapper
+ audit log for every remote action
```

This keeps the agent inside the local terminal while giving it controlled,
observable access to remote VM files and processes.
