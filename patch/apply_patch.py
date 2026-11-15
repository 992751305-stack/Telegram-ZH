#!/usr/bin/env python3
# tg-zh/ios/patch/apply_patch.py
#
# 给 Telegram-iOS 源码打「自动汉化」补丁。
# 改动只涉及 2 个文件、共 3 处，全部基于官方已有的翻译链路，不新增依赖。
#
# 用法（在仓库根目录执行）：
#   python3 patch/apply_patch.py          # 打补丁
#   python3 patch/apply_patch.py --revert # 撤销

import os
import sys
import shutil

ROOT = os.environ.get("AUTOZH_ROOT") or os.path.dirname(os.path.abspath(__file__))
BACKUP_SUFFIX = ".autozh.bak"

# ────────────────────────────────────────────────────────────
# 补丁 1：让 canTranslateChats 永远返回 true
#   官方逻辑依赖 app config 的 translationConfiguration.auto
#   服务器下发的配置若是 .alternative / .disabled（私聊场景常见）
#   整条翻译链直接被掐断 → 改成无条件放行
# ────────────────────────────────────────────────────────────
PATCH_1_FILE = "submodules/TranslateUI/Sources/Translate.swift"

PATCH_1_OLD = """public func canTranslateChats(context: AccountContext) -> Bool {
    let translationConfiguration = TranslationConfiguration.with(appConfiguration: context.currentAppConfiguration.with { $0 })
    var chatTranslationAvailable = true
    switch translationConfiguration.auto {
    case .system:
        if #available(iOS 18.0, *) {
        } else {
            chatTranslationAvailable = false
        }
    case .alternative, .disabled:
        chatTranslationAvailable = false
    default:
        break
    }
    return chatTranslationAvailable
}"""

PATCH_1_NEW = """public func canTranslateChats(context: AccountContext) -> Bool {
    // [AutoZH] 强制开启聊天翻译能力：不受服务器下发的 translationConfiguration.auto 限制。
    // 官方在 .alternative / .disabled 或 iOS < 18 时返回 false，导致私聊无法进入翻译链路。
    return true
}"""

# ────────────────────────────────────────────────────────────
# 补丁 2：把「服务器端自动翻译」变成「本地默认自动翻译」
#   ChatTranslation.swift 里 isEnabled 由 autoTranslateEnabled（服务端
#   toggleAutotranslation 的状态）决定。服务端开关只对频道开放，
#   私聊拿不到 → 改为永远 true，并对所有会话生效。
# ────────────────────────────────────────────────────────────
PATCH_2_FILE = "submodules/TranslateUI/Sources/ChatTranslation.swift"

PATCH_2_OLD = """                            let isEnabled: Bool
                            if let currentIsEnabled = cached?.isEnabled {
                                isEnabled = currentIsEnabled
                            } else if autoTranslateEnabled {
                                isEnabled = true
                            } else {
                                isEnabled = false
                            }"""

PATCH_2_NEW = """                            let isEnabled: Bool
                            // [AutoZH] 本地无条件开启聊天自动翻译。
                            // 官方依赖服务端 toggleAutotranslation（仅频道可用），
                            // 私聊/普通群没有该开关，所以强制 true。
                            let _ = autoTranslateEnabled
                            isEnabled = true"""

# ────────────────────────────────────────────────────────────
# 补丁 3：让渲染层始终认为"有目标语言"
#   ChatMessageTextBubbleContentNode 用 associatedData.translateToLanguage
#   是否非 nil 来判断要不要显示译文。该值来自会话级翻译状态。
#   这里在读取处兜底：为 nil 时用 baseLang 的目标语言替代。
# ────────────────────────────────────────────────────────────
PATCH_3_FILE = (
    "submodules/TelegramUI/Components/Chat/"
    "ChatMessageTextBubbleContentNode/Sources/ChatMessageTextBubbleContentNode.swift"
)

PATCH_3_OLD = "                    let translateToLanguage = item.associatedData.translateToLanguage"

PATCH_3_NEW = """                    // [AutoZH] 强制进入翻译显示分支：即使会话级 translateToLanguage 为 nil，
                    // 只要有 TranslationMessageAttribute，就用它的 toLang 作为目标语言。
                    var translateToLanguage = item.associatedData.translateToLanguage
                    if translateToLanguage == nil, let attr = item.message.attributes.first(where: { $0 is TranslationMessageAttribute }) as? TranslationMessageAttribute, !attr.text.isEmpty {
                        translateToLanguage = attr.toLang
                    }"""


PATCHES = [
    (PATCH_1_FILE, PATCH_1_OLD, PATCH_1_NEW, "canTranslateChats 强制放行"),
    (PATCH_2_FILE, PATCH_2_OLD, PATCH_2_NEW, "isEnabled 本地强制 true"),
    (PATCH_3_FILE, PATCH_3_OLD, PATCH_3_NEW, "translateToLanguage 兜底"),
]


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def backup(path):
    bak = path + BACKUP_SUFFIX
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def restore(path):
    bak = path + BACKUP_SUFFIX
    if os.path.exists(bak):
        shutil.copy2(bak, path)
        os.remove(bak)
        return True
    return False


def apply_all(revert=False):
    ok, fail = 0, 0
    for rel, old, new, desc in PATCHES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print(f"[SKIP] {rel} 不存在（是否已 sparse-checkout？）")
            fail += 1
            continue

        if revert:
            if restore(path):
                print(f"[REVERT] {desc}  ← {rel}")
                ok += 1
            else:
                print(f"[SKIP]   {rel} 无备份")
            continue

        content = read(path)

        if new in content and old not in content:
            print(f"[DONE]   {desc} 已打过，跳过")
            ok += 1
            continue

        if old not in content:
            print(f"[FAIL]   {desc}")
            print(f"         在 {rel} 里找不到锚点。官方代码可能已改动。")
            print(f"         锚点首行: {old.splitlines()[0][:80]}")
            fail += 1
            continue

        backup(path)
        write(path, content.replace(old, new, 1))
        print(f"[PATCH]  {desc}  → {rel}")
        ok += 1

    print(f"\n结果: {ok} 成功 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    revert = "--revert" in sys.argv
    if not os.path.isdir(os.path.join(ROOT, "submodules")):
        print(f"错误: {ROOT} 下没有 submodules/，请在仓库根目录执行")
        sys.exit(2)
    sys.exit(apply_all(revert))
