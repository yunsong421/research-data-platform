# GitHub 发布说明

只将本目录作为发布仓库。本次整理没有上传、创建远程仓库或推送文件。

宣传素材和 v1.0.0 草稿见 [SHARING.md](SHARING.md) 与 [RELEASE-v1.0.0.md](RELEASE-v1.0.0.md)。新的允许发布内容包括 `examples/sample-survey.xlsx` 及 `docs/images/` 中三个模拟展示文件；它们不包含真实数据。不要由此放开其他 Excel、图片或数据库文件的忽略规则。已有远程仓库请在独立克隆目录中更新本包的文件，不要把私人项目目录整体加入 Git。

在此目录中初始化 Git 并检查提交内容：

```bash
git init
git add --dry-run .
git add .
git diff --cached --stat
git diff --cached
```

确认只含代码与说明后提交：

```bash
git commit -m "Prepare source-only release"
git branch -M main
```

在 GitHub 创建空仓库后，将下面的示例地址替换为自己的地址，再自行推送：

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

## 部署说明

`deploy/` 示例需按服务器域名和路径修改。systemd 以 `/opt/data-platform` 存放代码、`/var/lib/data-platform` 存放数据。先创建数据目录并授权给 `www-data`，再以该用户、相同 `DATA_DIR` 执行 `init_admin.py`。安装服务后用 Nginx 和 HTTPS 提供访问。

Docker 配置已整理，发布整理过程中没有执行镜像构建或公网部署。运行服务前请阅读安全说明，并在自己的环境验收。
