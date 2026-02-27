# CyberSecurityRSS
[中文文档](https://github.com/zer0yu/CyberSecurityRSS/blob/master/README.zh-CN.md)

Introduction: RSS subscriptions related to cybersecurity, helping to establish personal intelligence sources and daily knowledge base updates.

The update frequency: every 2 months.

File description:

1. The others.md file adds some sites without rss subscription, but the site content is very good, so list them separately.

2. The tiny.opml file is a streamlined version of the secure RSS subscription for cyberspace.

3. The Cyber​​SecurityRSS.xml file is a rich version of cyberspace security RSS subscription, which involves all aspects of cyberspace security.

PS: If you encounter problems with the format of the imported file, you can modify the xml suffix to opml suffix, or modify the opml suffix to xml suffix.

```
cp CyberSecurityRSS.xml CyberSecurityRSS.opml
cp tiny.opml tiny.xml
```

## Usage 1-Reeder5 (macOS, IOS preferred)
import OMPL into Reeder `Subcsriptions -> Import from OMPL`

![image.png](https://i.loli.net/2021/12/03/gqE4OoGtCfS762D.png)

## Usage 2 - [yarr](https://github.com/nkanaev/yarr) (macOS, Windows, Linux)

![截屏2020-10-06 上午9.56.09.png](https://i.loli.net/2020/10/06/p9udsMkOQmHAtI8.png)

## Usage 3 - Leaf

Import the file directly to use it.

![屏幕快照 2019-04-02 下午4.04.14.png](https://i.loli.net/2019/04/02/5ca317954382b.png)

## Usage 4 - Feedly

![截屏2021-02-06 下午7.29.44.png](https://i.loli.net/2021/02/06/X6Jkat3O2YcFPvK.png)

## Usage 6 - Feeder(Recommended for Android users)

Use the same way as Usage 1

## Usage 6 - [Zr](https://www.coolapk.com/apk/176794)(Recommended for Android users)

Use the same way as Usage 1

## Usage 7 - [anyant](https://rss.anyant.com/)(Web Online)

Use the same way as Usage 1

![7.png](https://i.loli.net/2021/02/10/pHdIEztoOUeVxv3.png)

## Automation

This repository uses GitHub Actions to keep OPML files healthy and synced:

1. On `pull_request` to `master`, workflow runs in `check` mode and fails when OPML drift is detected.
2. On `push` to `master`, workflow runs in `apply` mode:
   - Validate RSS/Atom feed URLs in `tiny.opml` and `CyberSecurityRSS.opml`.
   - Remove dead feed entries and deduplicate by `xmlUrl`.
   - Sync valid feeds from `tiny.opml` into `CyberSecurityRSS.opml` (missing categories fall back to `Misc`).
   - Auto-commit OPML changes with `[skip ci]`.

## Contribution

If you find a great site, please submit an issue or pr

## Sponsor

[![Powered by DartNode](https://dartnode.com/branding/DN-Open-Source-sm.png)](https://dartnode.com "Powered by DartNode - Free VPS for Open Source")
