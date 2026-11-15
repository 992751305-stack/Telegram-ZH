# Telegram-ZH

给 Telegram-iOS 打自动汉化补丁，云端编译出 IPA。

## 机制

复用官方已有的翻译链路，只改 3 处开关：

| 文件 | 改动 |
|---|---|
| `Translate.swift` | `canTranslateChats` 无条件返回 true，解除服务端配置限制 |
| `ChatTranslation.swift` | `isEnabled` 本地强制 true，不依赖服务端 `toggleAutotranslation`（仅频道可用） |
| `ChatMessageTextBubbleContentNode.swift` | `translateToLanguage` 为 nil 时用 `TranslationMessageAttribute.toLang` 兜底 |

翻译本身走 Telegram 服务器（`messages.translateRichMessage`），不消耗任何第三方 API 额度。

## 使用

1. Settings → Secrets and variables → Actions 添加：
   - `TG_API_ID`（[my.telegram.org](https://my.telegram.org) 申请）
   - `TG_API_HASH`
2. Actions → `build-telegram-zh` → Run workflow
3. 跑完在 Artifacts 下载 `TelegramZH-ipa`

## 签名

免费 Apple ID 用 Sideloadly 签，7 天有效；SideStore 可自动续签。

## 撤销补丁

```bash
cd telegram-ios
python3 apply_patch.py --revert
```
