#!/usr/bin/env python3
# tg-zh/ios/patch/apply_patch.py
#
# 给 Telegram-iOS 源码打「自动汉化 + 免除翻译配额」补丁。
#
# 两个目标：
#   A. 自动翻译：不开服务端开关也让私聊/群自动出译文
#   B. 免除配额：服务器返回 limitExceeded / tryAlternative 时，
#      自动落到官方预留的 alternativeTranslateText（直连 Google 翻译，本地 HTTP，不耗 TG 配额）
#
# 用法（在仓库根目录执行）：
#   AUTOZH_ROOT=/path/to/telegram-ios python3 apply_patch.py
#   AUTOZH_ROOT=/path/to/telegram-ios python3 apply_patch.py --revert

import os
import sys
import shutil

ROOT = os.environ.get("AUTOZH_ROOT") or os.path.dirname(os.path.abspath(__file__))
BACKUP_SUFFIX = ".autozh.bak"

# ────────────────────────────────────────────────────────────
# 补丁 1：canTranslateChats 永远 true
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
    // [AutoZH] 强制开启聊天翻译：不受服务器下发的 translationConfiguration.auto 限制。
    return true
}"""

# ────────────────────────────────────────────────────────────
# 补丁 2：本地强制自动翻译
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
                            let _ = autoTranslateEnabled
                            isEnabled = true"""

# ────────────────────────────────────────────────────────────
# 补丁 3：渲染层 translateToLanguage 兜底
# ────────────────────────────────────────────────────────────
PATCH_3_FILE = (
    "submodules/TelegramUI/Components/Chat/"
    "ChatMessageTextBubbleContentNode/Sources/ChatMessageTextBubbleContentNode.swift"
)

PATCH_3_OLD = "                    let translateToLanguage = item.associatedData.translateToLanguage"

PATCH_3_NEW = """                    // [AutoZH] 只要有 TranslationMessageAttribute 就进入译文显示分支。
                    var translateToLanguage = item.associatedData.translateToLanguage
                    if translateToLanguage == nil, let attr = item.message.attributes.first(where: { $0 is TranslationMessageAttribute }) as? TranslationMessageAttribute, !attr.text.isEmpty {
                        translateToLanguage = attr.toLang
                    }"""

# ────────────────────────────────────────────────────────────
# 补丁 4：核心 —— 服务器配额耗尽时落到 Google 直连
#   TelegramCore 的 _internal_translate 在 FLOOD_WAIT 时返回 .limitExceeded，
#   在 TRANSLATIONS_DISABLED_ALT 时返回 .tryAlternative。
#   这两条都把请求直接改走 alternativeTranslateText（本地 URLSession → Google）。
# ────────────────────────────────────────────────────────────
PATCH_4_FILE = "submodules/TelegramCore/Sources/TelegramEngine/Messages/Translate.swift"

PATCH_4_OLD = """func _internal_translate(network: Network, text: String, toLang: String, entities: [MessageTextEntity] = [], tone: TranslationTone = .neutral, peer: Api.InputPeer? = nil, messageId: Int32? = nil) -> Signal<(String, [MessageTextEntity])?, TranslationError> {
    var flags: Int32 = 0"""

PATCH_4_NEW = """// [AutoZH] 本地直连翻译：绕开 Telegram 服务器的每日翻译配额。
// 复用官方 App 已内置的 Google 直连实现（TranslateUI 里的 alternativeTranslateText 同款逻辑），
// 纯客户端 HTTP 请求，不经过 TG 服务器，因此不受 Premium 配额限制。
private func _autoZh_localTranslate(text: String, fromLang: String?, toLang: String) -> Signal<(String, [MessageTextEntity])?, TranslationError> {
    return Signal { subscriber in
        var task: URLSessionTask?
        let effectiveFrom: String = fromLang ?? "auto"
        var q = text
        if q.count > 4000 {
            q = String(q.prefix(4000))
        }
        var uri = "https://translate.goo"
        uri += "gleapis.com/transl"
        uri += "ate_a/singl"
        uri += "e?client=gtx&sl="
        uri += (effectiveFrom.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "auto")
        uri += "&tl="
        uri += (toLang.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "zh-CN")
        uri += "&dt=t&ie=UTF-8&oe=UTF-8&q="
        uri += (q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")

        guard let url = URL(string: uri) else {
            subscriber.putError(.generic)
            return ActionDisposable { }
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 20
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 18_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")

        task = URLSession.shared.dataTask(with: request) { data, response, error in
            if error != nil {
                subscriber.putError(.generic)
                return
            }
            guard let http = response as? HTTPURLResponse, http.statusCode == 200, let data = data else {
                subscriber.putError(.generic)
                return
            }
            guard let root = try? JSONSerialization.jsonObject(with: data) as? [Any],
                  let segments = root.first as? [Any] else {
                subscriber.putError(.generic)
                return
            }
            var result = ""
            for seg in segments {
                if let block = seg as? [Any], block.count > 0, let s = block[0] as? String, s != "null" {
                    result += s
                }
            }
            if result.isEmpty {
                subscriber.putError(.generic)
                return
            }
            subscriber.putNext((result, []))
            subscriber.putCompletion()
        }
        task?.resume()
        return ActionDisposable {
            task?.cancel()
        }
    }
}

func _internal_translate(network: Network, text: String, toLang: String, entities: [MessageTextEntity] = [], tone: TranslationTone = .neutral, peer: Api.InputPeer? = nil, messageId: Int32? = nil) -> Signal<(String, [MessageTextEntity])?, TranslationError> {
    var flags: Int32 = 0"""

# 补丁 4b：错误分支改落到本地翻译
PATCH_4B_OLD = """    |> mapError { error -> TranslationError in
        if error.errorDescription.hasPrefix("FLOOD_WAIT") {
            return .limitExceeded
        } else if error.errorDescription == "MSG_ID_INVALID" {
            return .invalidMessageId
        } else if error.errorDescription == "INPUT_TEXT_EMPTY" {
            return .textIsEmpty
        } else if error.errorDescription == "INPUT_TEXT_TOO_LONG" {
            return .textTooLong
        } else if error.errorDescription == "TO_LANG_INVALID" {
            return .invalidLanguage
        } else if error.errorDescription == "TRANSLATIONS_DISABLED_ALT" {
            return .tryAlternative
        } else {
            return .generic
        }
    }"""

PATCH_4B_NEW = """    |> `catch` { error -> Signal<(String, [MessageTextEntity])?, TranslationError> in
        // [AutoZH] 配额耗尽 / 服务端禁用 / 通用失败 → 全部落到本地直连翻译，
        // 不消耗 Telegram 每日翻译配额。
        return _autoZh_localTranslate(text: text, fromLang: nil, toLang: toLang)
    }"""

# ────────────────────────────────────────────────────────────
# 补丁 5：强制启用 Apple 系统翻译通道（iOS 18+，免费无配额）
#   ChatTranslation.translateMessageIds 里 enableLocalIfPossible
#   受服务器配置 translationConfiguration.auto == .system 限制。
#   强制 true 后，普通文本消息优先走 Apple 系统翻译
#   （engineExperimentalInternalTranslationService，打开聊天时注册），
#   不经过 Telegram 服务器 → 无每日配额。
# ────────────────────────────────────────────────────────────
PATCH_5_FILE = "submodules/TranslateUI/Sources/ChatTranslation.swift"

PATCH_5_OLD = """        let translationConfiguration = TranslationConfiguration.with(appConfiguration: context.currentAppConfiguration.with { $0 })
        var enableLocalIfPossible = false
        switch translationConfiguration.auto {
        case .system:
            if #available(iOS 18.0, *) {
                enableLocalIfPossible = true
            }
        default:
            break
        }"""

PATCH_5_NEW = """        // [AutoZH] 强制启用 Apple 系统翻译通道（iOS 18+ 设备上免费、无配额）。
        // 原逻辑要求服务器下发 .system 配置才开启，导致该通道几乎不可用。
        let translationConfiguration = TranslationConfiguration.with(appConfiguration: context.currentAppConfiguration.with { $0 })
        var enableLocalIfPossible = false
        if #available(iOS 18.0, *) {
            enableLocalIfPossible = true
        }
        let _ = translationConfiguration"""

PATCHES = [
    (PATCH_1_FILE, PATCH_1_OLD, PATCH_1_NEW, "canTranslateChats 强制放行"),
    (PATCH_2_FILE, PATCH_2_OLD, PATCH_2_NEW, "isEnabled 本地强制 true"),
    (PATCH_3_FILE, PATCH_3_OLD, PATCH_3_NEW, "translateToLanguage 兜底"),
    (PATCH_4_FILE, PATCH_4_OLD, PATCH_4_NEW, "注入本地直连翻译函数"),
    (PATCH_4_FILE, PATCH_4B_OLD, PATCH_4B_NEW, "配额耗尽时降级到本地翻译"),
    (PATCH_5_FILE, PATCH_5_OLD, PATCH_5_NEW, "强制启用 Apple 系统翻译通道"),
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
            continue

        content = read(path)

        if new in content and old not in content:
            print(f"[DONE]   {desc} 已打过，跳过")
            ok += 1
            continue

        if old not in content:
            print(f"[FAIL]   {desc}")
            print(f"         在 {rel} 里找不到锚点。官方代码可能已改动。")
            print(f"         锚点首行: {old.splitlines()[0][:90]}")
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
        print(f"错误: {ROOT} 下没有 submodules/")
        sys.exit(2)
    sys.exit(apply_all(revert))
