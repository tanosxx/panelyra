# Updates and developer news

[Home](../README.md) · [GitHub publication](github.md) · [Privacy](privacy.md)

The bell in the Linux window contains notices about newer stable Panelyra
versions and messages from the maintainer. Its counter shows unread notices.
You can refresh the list manually, mark notices as read and turn automatic
checks on or off. Opening a notice's link is an explicit action; Panelyra does
not download packages, install updates or execute commands from a notice.

Checks run in the background when the window is open, at startup if due and
then about every six hours. These are periodic checks, not instant push
messages or desktop notifications while the app is closed. Offline operation
and a failed check do not interrupt USB display sharing. Previously fetched
notices remain available locally. CLI streaming and the Android receiver do
not check for news.

**This unpublished source tree has no configured public repository.** The bell
shows that its source is not configured and makes no Internet requests. The
root `announcements.json` is deliberately empty; example news is not delivered
to users.

## Configure the source before the first public build

1. Create the public repository under the account you control, as described in
   [the GitHub guide](github.md). Confirm its actual `OWNER/REPOSITORY` name and
   the branch that will hold news; the author credit **TanosX** alone does not
   establish ownership of any GitHub account.
2. Edit `usbdisplay/notification_source.py` before building the distributed app:

   ```python
   GITHUB_REPOSITORY = "YOUR_ACCOUNT/Panelyra"
   NEWS_BRANCH = "main"
   ```

   Replace the example account, repository and branch with real values. The
   repository string has no URL prefix or `.git` suffix. Leave it empty in
   builds that should make no update/news requests.
3. Keep `announcements.json` in the root of that branch, initially empty if
   there is no news to publish. Check it locally, without accessing GitHub:

   ```bash
   /usr/bin/python3 scripts/validate-announcements.py
   ```

   When testing a feed before configuring the app, pass the intended repository:

   ```bash
   /usr/bin/python3 scripts/validate-announcements.py \
     --repository YOUR_ACCOUNT/Panelyra
   ```

4. Commit and push the reviewed configuration and feed to the real repository.
   Build and test the candidate after those constants are set. Check the bell's
   manual refresh against the published source and verify its off switch.

The distributed app reads two HTTPS resources without a GitHub account or token:

- `https://api.github.com/repos/OWNER/REPOSITORY/releases/latest`
- `https://raw.githubusercontent.com/OWNER/REPOSITORY/BRANCH/announcements.json`

Keep the repository and news branch available. Changing these constants only
in a later source commit does not change already installed applications; users
need a new app build to change the source they use.

## Publish a new version

Build, test and sign the release using the [distribution guide](distribution.md).
Use a version tag such as `v0.3.1`, with a numeric `MAJOR.MINOR.PATCH` version
greater than the installed app. Publish a GitHub Release for that tag with its
release notes, packages and checksums. A Git tag by itself is insufficient.

The app consults GitHub's latest published full release. Drafts and prereleases
are excluded by that endpoint; keep testing candidates marked accordingly.
See GitHub's [latest release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
and [release management guide](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

Users receive a notice on their next successful check when the release version
is newer. The link opens the release page so they can choose the appropriate
package. No change to `announcements.json` is required for a normal version
notice, and publishing news does not require rebuilding the application.

## Publish a developer message

Add an item to the root `announcements.json` in the configured news branch.
This is a **format example**, not a claim that a new release or public repository
already exists:

```json
{
  "schema_version": 1,
  "items": [
    {
      "id": "2026-10-06-project-news",
      "published_at": "2026-10-06",
      "title": {
        "en": "A note from TanosX",
        "ru": "Сообщение от TanosX"
      },
      "body": {
        "en": "Thank you for testing Panelyra. Please include your Linux and Android versions in bug reports.",
        "ru": "Спасибо за проверку Panelyra. Указывайте версии Linux и Android в сообщениях об ошибках."
      }
    }
  ]
}
```

The entire UTF-8 JSON file must fit within 256 KiB. The schema has these limits:

| Field | Rule |
| --- | --- |
| `schema_version` | Integer `1` |
| `items` | At most 50 notices |
| `id` | Unique and permanent; 1–80 ASCII letters, digits, `.`, `_` or `-`; first character must be a letter or digit |
| `published_at` | Valid date in `YYYY-MM-DD` form |
| `title` | Translation object; English `en` required, Russian `ru` recommended; at most 140 characters per translation |
| `body` | Translation object; English `en` required, Russian `ru` recommended; at most 1,600 characters per translation |
| Translation keys | Up to 8 languages; forms such as `en`, `ru` or `pt-BR` |
| `url` | Optional HTTPS link within this same GitHub repository; at most 2,048 characters |

Use plain text, without HTML or Markdown formatting. The application displays
text, not executable or rich web content. An optional `url` can point to an
existing issue, discussion or documentation page under the configured repository,
for example `https://github.com/YOUR_ACCOUNT/Panelyra/discussions/1` after such a
page actually exists. External domains and other repositories are rejected.
The window uses the selected language when available and otherwise English.

Keep the same `id` when correcting a typo: read state is tied to that identity.
Use a new ID for a new announcement. Avoid reusing an old ID for
unrelated information. Remove older entries as needed to stay within the limit;
the feed is a recent-news list, not a complete archive.

Validate locally, review the diff, then commit and push the feed to the
configured branch. CI runs the same offline validator. Users receive the
message after their next successful check, without a new Linux or Android
build. Publishing the JSON is public publication: exclude private data and
unreleased credentials.

## Privacy and troubleshooting

When a source is configured, automatic checks use the PC's Internet connection.
GitHub receives normal request information including the public IP address and
a User-Agent identifying Panelyra. No screen contents, USB
identifiers, account, analytics event or installation identifier are included.
Turning automatic checks off stops scheduled requests; pressing refresh still
explicitly requests a check. Links open in the user's browser only after a
click and then follow the browser's normal session and privacy settings.

A rate limit, unavailable network, missing feed or invalid JSON is shown as a
check problem, not as proof that there is no newer version. Keep working over
USB and try manually later. If the source is unconfigured, the maintainer must
configure and ship it first. Full data handling is in [Privacy](privacy.md).

## Для TanosX: как отправить новость пользователям

1. После создания публичного репозитория укажите его настоящее имя и ветку в
   `usbdisplay/notification_source.py`, затем соберите приложение. Пока поле
   `GITHUB_REPOSITORY` пустое, колокольчик не обращается в Интернет.
2. Для обычной новости добавьте запись в `announcements.json`: постоянный
   уникальный `id`, дату, заголовок и текст на `ru` и `en`. Пример выше можно
   использовать как образец. Ссылка необязательна; разрешены страницы внутри
   вашего настроенного GitHub-репозитория.
3. Выполните `/usr/bin/python3 scripts/validate-announcements.py`. Исправьте
   ошибки, проверьте изменения, затем сделайте commit и push файла в указанную
   ветку. Для каждой новости пересобирать программу не требуется.
4. Для новой версии выпустите стабильный GitHub Release с тегом вроде `v0.3.1`
   и файлами установки. Версия должна быть новее установленной. Черновики и
   prerelease не приходят как уведомления о стабильном обновлении.

У пользователя появится счётчик на колокольчике при следующей проверке: при
открытом приложении примерно раз в шесть часов или после нажатия обновления.
Это не мгновенная push-рассылка; закрытая программа проверок не выполняет.
Пользователь может отключить автоматические проверки и отметить всё прочитанным.
Скачивание и установка новой версии остаются действием пользователя.
