#!/usr/bin/env python3
"""
ssh_exec.py - 通过 paramiko 执行远程命令 / 传输文件

为什么要它：Windows 自带的 ssh.exe 拒绝从管道读密码（它要真终端），
所以密码自动化用 ssh.exe 做不到。paramiko 直接讲 SSH 协议，没有这个限制。

密码从环境变量 DSH_SSH_PASS 读，不出现在命令行、不落盘。

用法：
  set DSH_SSH_PASS=... & python tools/ssh_exec.py exec <host> <user> <port> "<命令>"
  set DSH_SSH_PASS=... & set DSH_SSH_CMD_FILE=script.sh & python tools/ssh_exec.py exec <host> <user> <port> bash -s
  set DSH_SSH_PASS=... & python tools/ssh_exec.py put <host> <user> <port> <本地> <远程>
  set DSH_SSH_PASS=... & python tools/ssh_exec.py get <host> <user> <port> <远程> <本地>

长命令建议走 DSH_SSH_CMD_FILE：PowerShell 会把分号/引号拆坏，从文件流进去最稳。
"""

import os
import socket
import sys

import paramiko


def connect(host, user, port):
    password = os.environ.get("DSH_SSH_PASS")
    if not password:
        print("ERROR: DSH_SSH_PASS is not set", file=sys.stderr)
        sys.exit(2)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=int(port),
            username=user,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
            banner_timeout=30,
            auth_timeout=30,
        )
    except paramiko.AuthenticationException:
        print("ERROR: authentication failed (wrong password?)", file=sys.stderr)
        sys.exit(3)
    except (socket.timeout, socket.error) as exc:
        print("ERROR: could not connect: %s" % exc, file=sys.stderr)
        sys.exit(4)
    return client


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    mode = sys.argv[1]

    if mode == "exec":
        if len(sys.argv) < 7:
            print("usage: exec <host> <user> <port> <command> [timeout_sec]", file=sys.stderr)
            sys.exit(2)
        host, user, port, command = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        timeout = int(sys.argv[6]) if len(sys.argv) > 6 else 300

        cmd_file = os.environ.get("DSH_SSH_CMD_FILE")
        script_text = None
        if cmd_file:
            with open(cmd_file, "r", encoding="utf-8") as fh:
                script_text = fh.read()
            if not script_text.endswith("\n"):
                script_text += "\n"

        client = connect(host, user, port)
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            if script_text is not None:
                stdin.write(script_text)
            stdin.flush()
            stdin.close()

            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()

            if out:
                sys.stdout.write(out)
            if err:
                sys.stdout.write("\n[stderr]\n" + err)
            sys.stdout.flush()
            sys.exit(code)
        finally:
            client.close()

    elif mode in ("put", "get"):
        if len(sys.argv) < 7:
            print("usage: %s <host> <user> <port> <src> <dst>" % mode, file=sys.stderr)
            sys.exit(2)
        host, user, port, src, dst = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]

        client = connect(host, user, port)
        try:
            sftp = client.open_sftp()
            try:
                if mode == "put":
                    sftp.put(src, dst)
                else:
                    sftp.get(src, dst)
                print("%s done: %s -> %s" % (mode, src, dst))
            finally:
                sftp.close()
            sys.exit(0)
        finally:
            client.close()

    else:
        print("unknown mode: %s" % mode, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
