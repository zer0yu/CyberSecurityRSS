# CyberSecurityRSS
简介: 网络安全相关的RSS订阅，帮助建立个人情报来源和日常知识库更新
更新频率: 每2个月一次

文件说明:

1. 附录加一些可能没有rss的站点，但是是本人日常浏览的不错站点，重点写出来了。

2. tiny.opml是个人速览使用的一个精简版本。

3. CyberSecurityRSS.xml是集合了泉哥和rr的一个十分丰富的版本，也分好了类别。

PS: 如果遇到导入文件格式问题的可以将xml后缀修改为opml后缀，反之也可。

```
cp CyberSecurityRSS.xml CyberSecurityRSS.opml
cp tiny.opml tiny.xml
```

## 使用方法1 - Reeder5 (macOS, IOS的首选)

![image.png](https://i.loli.net/2021/12/03/gqE4OoGtCfS762D.png)

## 使用方法2 - [yarr](https://github.com/nkanaev/yarr) (macOS, Windows, Linux)

![截屏2020-10-06 上午9.56.09.png](https://i.loli.net/2020/10/06/p9udsMkOQmHAtI8.png)



## 使用方法3 - Leaf

直接导入提供的文件即可

![屏幕快照 2019-04-02 下午4.04.14.png](https://i.loli.net/2019/04/02/5ca317954382b.png)

## 使用方法4 - Feedly

这种是网页的形式(当然也有APP)，feedly的免费版本，tiny.opml可以直接导入并够用，但是CyberSecurityRSS.xml版本会超过免费的订阅限制。

![截屏2021-02-06 下午7.29.44.png](https://i.loli.net/2021/02/06/X6Jkat3O2YcFPvK.png)

## 使用方法6 - Feeder(Android推荐使用)

同1

## 使用方法6 - [Zr](https://www.coolapk.com/apk/176794)(Android推荐使用)

同1

## 使用方法7 - [蚁阅](https://rss.anyant.com/)(Web Online)

同1

![7.png](https://i.loli.net/2021/02/10/pHdIEztoOUeVxv3.png)

## 自动化工作流

仓库已配置 GitHub Actions 自动维护 OPML：

1. `pull_request -> master` 触发 `check` 模式，只校验不改文件；如发现需要变更会直接失败。
2. `push -> master` 触发 `apply` 模式，自动执行：
   - 校验 `tiny.opml` 与 `CyberSecurityRSS.opml` 的 RSS/Atom 链接可访问性。
   - 删除失效订阅，并按 `xmlUrl` 自动去重。
   - 将 `tiny.opml` 中有效且大表缺失的链接同步到 `CyberSecurityRSS.opml`。
   - 若分类不存在则归入 `Misc`，并自动提交修复结果（commit 含 `[skip ci]`）。

## 附录

others.md中是我一般会主动浏览的站点并且一些站点没有提供rss的都写在了里面。

## 贡献

如果大家还有很棒的站点欢迎提issue
