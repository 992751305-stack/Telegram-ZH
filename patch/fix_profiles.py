#!/usr/bin/env python3
# tg-zh/ios/patch/fix_profiles.py
#
# 修复 Telegram-iOS 仓库自带 fake provisioning profiles 已过期的问题。
# 官方 fake-codesigning/profiles/*.mobileprovision 的 ExpirationDate 是写死的，
# 过期后 rules_apple 的 plisttool 会拒绝：
#   ERROR: provisioning profile ExpirationDate ("2026-10-30T17:06:30") is in the past.
#
# 处理：解出每个 profile 的 plist → 把 ExpirationDate 推到未来 → 用 keychain 里的
# SelfSigned 证书重新 CMS 签名（照抄官方 GenerateProfiles.process_provisioning_profile）。
#
# 前置：ImportCertificates.py 已跑过（temp.keychain 已建、已导入 SelfSigned.p12 并解锁）。
# 用法：python3 fix_profiles.py <fake-codesigning 根目录> [keychain 名称，默认 temp.keychain]

import os
import sys
import subprocess
import plistlib
import tempfile
from datetime import datetime, timedelta, timezone


def run(cmd, input_bytes=None, check=True):
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input_bytes is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(input_bytes)
    if check and proc.returncode != 0:
        print(f"CMD FAILED: {' '.join(cmd)}")
        print(err.decode('utf-8', 'replace')[:500])
        sys.exit(1)
    return out


def get_signing_identity(certs_path):
    """从 SelfSigned.p12 提取 CN（签名 identity 常名）。照抄官方 GenerateProfiles。"""
    p12 = os.path.join(certs_path, 'SelfSigned.p12')
    if not os.path.exists(p12):
        print(f"{p12} 不存在")
        sys.exit(1)

    # macOS 自带 LibreSSL 不需要 -legacy；官方脚本用 -legacy 以兼容 openssl 3.x
    try:
        pem = run(['openssl', 'pkcs12', '-in', p12, '-passin', 'pass:', '-nokeys', '-legacy'],
                  check=False)
        if not pem:
            pem = run(['openssl', 'pkcs12', '-in', p12, '-passin', 'pass:', '-nokeys'])
    except FileNotFoundError:
        pem = run(['openssl', 'pkcs12', '-in', p12, '-passin', 'pass:', '-nokeys'])

    proc = subprocess.Popen(['openssl', 'x509', '-noout', '-subject', '-nameopt', 'oneline,-esc_msb'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subject, _ = proc.communicate(pem)
    subject = subject.decode('utf-8', 'replace').strip()

    for marker in ('CN = ', 'CN='):
        if marker in subject:
            cn = subject.split(marker)[-1].split(',')[0].strip()
            return cn
    print(f"无法从 p12 提取 CN，subject={subject}")
    sys.exit(1)


def reprofile(source_file, destination_file, signing_identity, keychain):
    parsed = run(['security', 'cms', '-D', '-i', source_file]).decode('utf-8', 'replace')

    profile = plistlib.loads(parsed.encode('utf-8'))

    new_exp = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3650)
    old_exp = profile.get('ExpirationDate')
    profile['ExpirationDate'] = new_exp

    # DER-Encoded-Profile 是旧 CMS 签名，必须移除后重签
    profile.pop('DER-Encoded-Profile', None)

    parsed_file = tempfile.mktemp(suffix='.plist')
    with open(parsed_file, 'wb') as f:
        f.write(plistlib.dumps(profile, fmt=plistlib.FMT_XML))

    run(['security', 'cms', '-S', '-k', keychain, '-N', signing_identity,
         '-i', parsed_file, '-o', destination_file])

    os.unlink(parsed_file)
    print(f"  {os.path.basename(source_file)}: {old_exp} -> {new_exp.isoformat()}")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 fix_profiles.py <fake-codesigning 根目录> [keychain]")
        sys.exit(2)

    root = sys.argv[1]
    keychain = sys.argv[2] if len(sys.argv) > 2 else 'temp.keychain'
    certs_path = os.path.join(root, 'certs')
    profiles_path = os.path.join(root, 'profiles')

    if not os.path.isdir(profiles_path):
        print(f"{profiles_path} 不存在")
        sys.exit(2)

    identity = get_signing_identity(certs_path)
    print(f"签名 identity: {identity}")
    print(f"keychain: {keychain}")

    count = 0
    for name in sorted(os.listdir(profiles_path)):
        if not name.endswith('.mobileprovision'):
            continue
        src = os.path.join(profiles_path, name)
        tmp_out = tempfile.mktemp(suffix='.mobileprovision')
        reprofile(src, tmp_out, identity, keychain)
        shutil_copy(tmp_out, src)
        os.unlink(tmp_out)
        count += 1

    print(f"完成：{count} 个 profile 已续期并重签")


def shutil_copy(src, dst):
    import shutil
    shutil.copyfile(src, dst)


if __name__ == '__main__':
    main()
