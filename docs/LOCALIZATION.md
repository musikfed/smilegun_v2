# Localization policy

SMILE·GUN keeps the implementation **English-first** so the source stays compact and maintainable.

- Source-code comments, docstrings, identifiers and architecture notes are written in English.
- All normal player-facing UI strings live in `static/i18n.js`.
- The current menu packs are **Russian (`ru`)**, **English (`en`)**, **Simplified Chinese (`zh`)**, **Kazakh (`kk`)** and **Korean (`ko`)**.
- The menu language selector persists the choice in browser `localStorage`.
- Short translated guides live in `docs/README.*.md`; we intentionally do not duplicate the full source documentation in every language.
- When adding a new UI phrase, add one stable key to every language pack instead of embedding translated text in game logic.

## Short notes / Короткие сноски / 简短说明 / Қысқаша ескерту / 짧은 안내

**Русский.** Основной код и комментарии остаются на английском. Русский интерфейс и остальные переводы хранятся отдельно в `static/i18n.js`, а краткая русская инструкция — в `docs/README.ru.md`.

**中文.** 源代码和代码注释以英语为主。中文界面文本集中在 `static/i18n.js`，简短中文说明位于 `docs/README.zh-CN.md`。

**Қазақша.** Негізгі код пен түсіндірме пікірлер ағылшын тілінде сақталады. Қазақша интерфейс `static/i18n.js` ішінде, қысқаша нұсқаулық `docs/README.kk.md` файлында.

**한국어.** 소스 코드와 코드 주석은 영어를 기본으로 유지합니다. 한국어 UI 문자열은 `static/i18n.js`, 짧은 안내서는 `docs/README.ko.md`에 있습니다.

This separation keeps the gameplay files from growing into five near-identical copies.
