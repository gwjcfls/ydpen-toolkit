# 笔上的 sshd：为什么"启动了却 Permission denied"，以及怎么修好

## 一、症状

应用里点了 `sshd` 按钮、日志也显示已启动，但电脑上：

```
$ ssh root@192.168.137.100 -p 2222
root@192.168.137.100: Permission denied (publickey,keyboard-interactive).
```

## 二、根因（两个叠在一起）

1. **应用把密码认证关掉了**：
   ```
   -o PasswordAuthentication=no
   ```
   而 app 调用是 `term.sshdStart(2222, '')` —— **没传公钥**，所以
   `/userdisk/ssh/authorized_keys` 根本没写。两种认证都没有 → 必然进不去。
   客户端那句 `(publickey,keyboard-interactive)` 就是服务端当时**只**提供了这两种。

2. **就算开了密码认证也进不去**：笔上 `/etc/shadow` 里 root 的口令是**原厂未知口令**
   （`$5$8HSwFJe6C7z$…`，试过 `ydpen2026` / `youdao` / `root` / `123456` 都不对）。
   注意：ADB 的 `auth` 口令 `ydpen2026` **不是** shadow 口令，两者无关。

## 三、修法（9.3.9 起内置）

### 3.1 打开密码认证 + 关掉 keyboard-interactive

```c
-o PasswordAuthentication=yes
-o KbdInteractiveAuthentication=no   // 无 PAM 时它没有后端，关掉客户端只问一次密码
-o PubkeyAuthentication=yes
```

> ⚠️ **千万别加 `-o UsePAM=no`** —— 这个 sshd（OpenSSH 8.8p1）**没编 PAM**，
> 传了会直接 `Unsupported option UsePAM` 起不来（我第一次就踩了这个）。
> 没有 PAM 时 OpenSSH 自己读 `/etc/shadow` 校验密码，正是我们要的。

### 3.2 把 `/etc/shadow` 盖成"口令已知"的副本

`/etc` 是只读 erofs，改不了 → 用 **bind mount**：

```sh
# 1) 只改 root 那一行的口令字段（其余原样），写到可写区
#    哈希 = sha256crypt("ydpen2026", salt=PenTermSalt2026)
#    $5$PenTermSalt2026$oKJZ71L5cOec.4YlgQCep1uhaBUf80m5kccwRk1Fpz0
# 2) 挂上去
mount --bind /userdisk/ssh/shadow /etc/shadow
```

**这步一定要在 C 里做**（`write_shadow_copy()`）：
把 `$5$…` 这种哈希交给 shell 处理极容易被二次展开搞坏 —— 我第一版用
`awk -v h="$H" ...` 生成，结果生成的 shadow **丢了 root 行**，所有密码都校验失败。
纯 C 读写没有这个问题。

要点：
- 生成源优先用 `/userdisk/ssh/shadow.orig`（第一次运行时保存的原始副本），
  并且**先检查里面有没有 `root:` 行**再当源用，避免"拿坏文件当源"滚雪球。
- 挂载前 `grep -q ' /etc/shadow ' /proc/mounts` 判重，重复点按钮不会挂两层。
- 副本 chmod 600；`/etc/shadow` 行数与原文件一致（只换字段）。

### 3.3 停止时还原

```sh
P=/tmp/sshd_term.pid; [ -f $P ] && kill $(cat $P); rm -f $P
grep -q ' /etc/shadow ' /proc/mounts && umount /etc/shadow
```

> ⚠️ **别用 `pkill -f 'sshd -D -p'`** —— 那串会匹配到本命令自己的 `sh -c` 命令行，
> 把自己杀掉（和 keeper 那个 `pkill -f skip_login.sh` 自杀坑同源）。按 pid 文件杀。

**重启自动还原**：bind mount 不持久，开机后 `/etc/shadow` 回到原厂口令
（要再用 ssh 就再点一次 sshd 按钮）。

## 四、实测（2026-10-03，YDPX7-1）

```
$ python tmp/test_ssh_login.py 192.168.137.100 2222 ydpen2026
[✓] 正确密码 登录成功（user=root pass=*********）
     uid=0(root) gid=0(root) groups=0(root),10(wheel)
     Linux YoudaoDictionaryPen-903 5.10.160 ... aarch64 GNU/Linux
     SSH-OK
[✗] 错误密码（应被拒） 认证被拒（user=root）
```

- 客户端可见认证方式：`publickey,password,keyboard-interactive`（修好后含 `password`）
- sshd 存活测试：**切到桌面、应用退到后台后 sshd 仍在**（`fork+setsid` 守护化有效），
  电脑端依然能连上
- `sshdStop` 后：sshd 进程 0、`/etc/shadow` 挂载 0、root 哈希回到 `$5$8HSwFJe6C7z$…`

## 五、更安全的用法：公钥免密

密码是给"随手连一下"用的；长期用建议公钥：

```sh
# 电脑上
ssh-keygen -t ed25519 -f ~/.ssh/pen
# 把 pen.pub 内容写进笔上这个文件（MTP 传输或文件管理器都能改）
#   /userdisk/ssh/authorized_keys
ssh -i ~/.ssh/pen -p 2222 root@<笔IP>
```

`sshdStart(port, pubkey)` 第二参数就是给这个用的：传入公钥时会自动写
`authorized_keys`（`AuthorizedKeysFile=/userdisk/ssh/authorized_keys`，`StrictModes=no`）。

## 六、安全提醒

密码版 sshd = **局域网内知道口令的人都能拿到 root**。只在可信网络里开；
不用了按「sshd」再点一次（停止）或直接重启笔，`/etc/shadow` 就还原了。
