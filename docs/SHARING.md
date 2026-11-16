# 项目介绍与分享素材

## GitHub About

建议 Description：

> 基于 Flask 与 SQLite 的研究资料管理平台：管理问卷、文档和访谈音视频，上传 Excel 自选字段生成六类图表。支持本地部署，附虚构示例数据与启动脚本。

建议 Topics：

`flask` `sqlite` `python` `data-visualization` `questionnaire` `research-data-management` `matplotlib` `excel` `self-hosted`

About 的 Website 字段暂不填写在线演示链接；当前仓库没有公网演示站。

## 分享文案

标题：用 Flask + SQLite 管理问卷与访谈资料：上传 Excel 自选字段生成图表

正文：

> 整理了一个适合小型研究项目本地使用的资料管理平台。可统一管理问卷、文档及学生 / 教师访谈音视频，支持分类、检索、预览和下载。
>
> 自定义可视化页可上传 Excel、选择工作表和字段，按计数、合计或平均值生成条形图、柱状图、饼图、折线图、直方图与散点图，支持 PNG 下载。附有 120 条完全虚构的模拟问卷，方便先试用再导入自己的数据。
>
> Python 3.11 环境下，Windows 双击 start.bat，macOS / Linux 执行 sh start.sh。首次运行自行创建管理员，没有共享默认密码。
>
> 仓库：https://github.com/yunsong421/research-data-platform
>
> 欢迎体验并通过 Issue 反馈可复现的问题。若项目对你有帮助，可以点 Star 收藏。

发布到社区前请遵守各平台的推广规则。此文件只是可供审阅的文案，不会自动发送消息或发布帖子。不要将真实研究资料或登录凭据加入截图和公开 Issue。

## 发布清单

1. 将这次源码包的内容更新至仓库根目录，包含 `docs/images`、`examples`、启动脚本及隐藏配置文件。
2. 在仓库 About 中手动填写上面的描述和 Topics。
3. 确认 Actions 测试通过。
4. 在 Releases 创建 `v1.0.0`，使用 `docs/RELEASE-v1.0.0.md` 的内容作为发布说明，可附上源码压缩包。
5. 分享仓库链接与模拟截图。源码发布不等于网站已部署上线。
