# 群晖部署说明

本文档用于把团购数据指标系统部署到群晖 Container Manager。推荐方式是：GitHub Actions 构建 `report-web` 镜像，群晖拉取镜像运行，数据库和上传文件保存在群晖目录。

## 部署结构

服务：

| 服务 | 镜像 | 数据保存位置 |
| --- | --- | --- |
| report-web | `ghcr.io/lijl9696/bdsh-ds-report-web:latest` | 上传文件挂载到 `${TG_REPORT_DATA_DIR}/uploads` |
| collector | `ghcr.io/lijl9696/bdsh-ds-collector:latest` | 采集配置、登录态、下载文件和日志 |
| postgres | `postgres:16` | `${TG_REPORT_DATA_DIR}/postgres` |
| metabase | `metabase/metabase:v0.52.4` | `${TG_REPORT_DATA_DIR}/metabase` |
| backup | `postgres:16` | `${TG_REPORT_DATA_DIR}/backups` |

只要不删除 `${TG_REPORT_DATA_DIR}`，更新 `report-web` 镜像不会影响业务数据、Metabase 仪表盘、登录账号和上传文件。

## 首次部署

推荐把本系统单独放在 GitHub 仓库：

```text
https://github.com/lijl9696/BDSH-ds.git
```

如果从本机首次推送到新仓库，建议只提交 `data-platform-system/` 和 `.github/workflows/build-report-web-image.yml`，不要把旧版桌面工具的历史文件变更一起带进去。

示例：

```bash
git remote set-url origin git@github.com:lijl9696/BDSH-ds.git
git add data-platform-system .github/workflows/build-report-web-image.yml
git commit -m "Add data platform deployment"
git push -u origin main
```

如果当前工作区包含旧项目文件删除、移动或其他无关变更，先单独整理一个干净目录再推送更稳。

1. 在 GitHub 仓库确认 Actions 已成功生成镜像：

```text
ghcr.io/lijl9696/bdsh-ds-report-web:latest
```

2. 把仓库同步到群晖，例如放到：

```text
/volume1/docker/BDSH-ds
```

3. 进入部署目录：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
```

4. 复制环境变量文件：

```bash
cp .env.synology.example .env
```

5. 编辑 `.env`：

```text
POSTGRES_PASSWORD=换成一个足够长的密码
TG_REPORT_DATA_DIR=/volume1/docker/tg-report
REPORT_WEB_IMAGE=ghcr.io/lijl9696/bdsh-ds-report-web:latest
REPORT_WEB_PORT=8000
METABASE_PORT=3000
POSTGRES_PORT=5729
IMPORT_AUTH_USERNAME=admin
IMPORT_AUTH_PASSWORD=换成另一个导入页登录密码
```

`IMPORT_AUTH_USERNAME` 和 `IMPORT_AUTH_PASSWORD` 是导入页面 `http://群晖IP:8000` 的登录账号密码。建议数据库密码和导入页密码不要相同。

`POSTGRES_PORT=5729` 表示群晖宿主机对外暴露 `5729` 端口，避免和群晖自身或其他服务的 `5432` 冲突。容器内部访问 Postgres 仍然是 `postgres:5432`，所以 `DATABASE_URL` 不需要改成 5729。

6. 创建数据目录：

```bash
mkdir -p /volume1/docker/tg-report/postgres
mkdir -p /volume1/docker/tg-report/metabase
mkdir -p /volume1/docker/tg-report/uploads
mkdir -p /volume1/docker/tg-report/backups
mkdir -p /volume1/docker/tg-report/collector/config
mkdir -p /volume1/docker/tg-report/collector/downloads
mkdir -p /volume1/docker/tg-report/collector/state
mkdir -p /volume1/docker/tg-report/collector/logs
cp ../collector/config/jobs.example.yml /volume1/docker/tg-report/collector/config/jobs.yml
```

7. 启动：

```bash
docker-compose -f docker-compose.synology.yml up -d
```

8. 访问：

```text
report-web: http://群晖IP:8000
Metabase: http://群晖IP:3000
```

打开 `report-web` 时浏览器会弹出登录框，输入 `.env` 里的 `IMPORT_AUTH_USERNAME` 和 `IMPORT_AUTH_PASSWORD`。

## Metabase 一直转圈

Metabase 第一次启动会下载/初始化一些内部组件，可能需要几分钟。超过 5 分钟仍一直转圈时，在群晖终端检查：

```bash
docker ps | grep metabase
docker logs --tail 200 tg-report-metabase
docker port tg-report-metabase
```

重点看日志里是否有：

- `/metabase-data` 目录权限错误。
- Java 内存不足或容器被杀。
- 端口没有映射到 `0.0.0.0:3000`。

如果是权限问题，先停容器，再给数据目录写入权限：

```bash
docker-compose -f docker-compose.synology.yml stop metabase
chmod -R 777 /volume1/docker/tg-report/metabase
docker-compose -f docker-compose.synology.yml up -d metabase
```

## 日常升级

本地改代码后推到 GitHub，Actions 会自动构建新镜像。群晖升级时执行：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
docker-compose -f docker-compose.synology.yml pull report-web
docker-compose -f docker-compose.synology.yml up -d report-web
```

这只会更新应用容器，不会删除 Postgres 数据、Metabase 数据和上传文件。

### 版本确认和镜像更新

`latest` 不是固定版本号，它会随着 GitHub Actions 构建移动。日常排查时用下面三类信息确认版本：

1. 代码仓库版本：

```bash
cd /volume1/docker/BDSH-ds
git log --oneline -1
```

2. 本地镜像版本：

```bash
docker image inspect ghcr.io/lijl9696/bdsh-ds-report-web:latest \
  --format 'created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} digest={{join .RepoDigests ","}}'

docker image inspect ghcr.io/lijl9696/bdsh-ds-collector:latest \
  --format 'created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} digest={{join .RepoDigests ","}}'
```

3. 正在运行的容器使用的镜像：

```bash
docker inspect tg-report-web \
  --format 'image_id={{.Image}} created={{.Created}}'

docker inspect tg-report-collector \
  --format 'image_id={{.Image}} created={{.Created}}'
```

确认 collector 是否是新版 CLI：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
docker-compose -f docker-compose.synology.yml run --rm collector python -m collector.cli -h
```

新版应该能看到：

```text
{run,download,login}
```

如果只看到 `{run,login}`，说明 collector 镜像或容器仍是旧版。

升级完整流程：

```bash
cd /volume1/docker/BDSH-ds
git fetch origin main --verbose
git reset --hard origin/main

cd /volume1/docker/BDSH-ds/data-platform-system/deploy
docker-compose -f docker-compose.synology.yml pull report-web collector
docker-compose -f docker-compose.synology.yml up -d --force-recreate report-web collector
```

如果只更新 collector：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
docker-compose -f docker-compose.synology.yml stop collector
docker-compose -f docker-compose.synology.yml rm -f collector
docker-compose -f docker-compose.synology.yml pull collector
docker-compose -f docker-compose.synology.yml up -d --force-recreate --no-deps collector
```

更新后检查：

```bash
docker-compose -f docker-compose.synology.yml run --rm collector python -m collector.cli -h
docker logs --tail 80 tg-report-collector
```

如果仓库里的示例配置看起来还是旧的，先检查代码版本：

```bash
cd /volume1/docker/BDSH-ds
git log --oneline -1
grep -n "trigger_selector\|steps:\|download_selector" data-platform-system/collector/config/jobs.example.yml
```

新版 `jobs.example.yml` 不应该出现 `trigger_selector`，应该出现 `steps:` 和 `download_selector:`。

## 自动采集服务

`collector` 默认会随 compose 启动，但如果 `jobs.yml` 里没有启用任务，只会写一条“没有启用的采集任务”的日志，不会自动访问平台。

配置文件在：

```text
/volume1/docker/tg-report/collector/config/jobs.yml
```

启用采集前，需要把里面的页面 URL 和按钮选择器改成真实平台后台信息，并把任务的 `enabled` 改成 `true`。

查看日志：

```bash
docker logs --tail 100 tg-report-collector
tail -n 100 /volume1/docker/tg-report/collector/logs/collector.log
```

手动执行某个任务：

```bash
docker exec -it tg-report-collector python -m collector.cli run meituan_daily
```

只下载不入库，用来排查页面选择和下载逻辑：

```bash
docker exec -it tg-report-collector python -m collector.cli download meituan_daily
```

如果要检查当天早上自动任务是否真的成功，按这个顺序看：

```bash
grep -n "开始采集任务\|采集任务完成\|采集任务失败" /volume1/docker/tg-report/collector/logs/collector.log
ls -lh /volume1/docker/tg-report/collector/downloads | tail
docker exec -it tg-report-postgres psql -U tg_report -d tg_report -c "
SELECT metric_date, COUNT(*)
FROM metric_values
WHERE platform_code = 'meituan'
  AND metric_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY metric_date
ORDER BY metric_date DESC;
"
```

判断方式：

```text
日志没有开始采集任务：collector 调度没有运行，检查容器和 jobs.yml。
日志失败：看失败行后面的 traceback，通常是登录态失效或页面元素变化。
有下载文件但 metric_values 没有对应日期：下载成功，入库或日期选择有问题。
metric_values 有对应日期但 Metabase 空：优先检查 Metabase 筛选器绑定和 SQL。
```

collector 必须使用上海时区。`docker-compose.synology.yml` 已设置：

```text
TZ=Asia/Shanghai
COLLECTOR_TIMEZONE=Asia/Shanghai
```

这两个都不要删。否则页面里的“昨天”可能按 UTC 计算，在早上 8 点前会选到前一天的数据。

第一次登录态可以在有图形界面的机器生成，也可以后续单独做远程登录流程。登录态文件放在：

```text
/volume1/docker/tg-report/collector/state
```

采集服务详细说明见：

```text
../collector/README.md
```

## 重要注意

不要执行：

```bash
docker-compose -f docker-compose.synology.yml down -v
```

`-v` 会删除 Docker volume。虽然本项目主要使用群晖目录挂载，但仍建议避免这个习惯，防止误删其他卷。

不要删除：

```text
${TG_REPORT_DATA_DIR}/postgres
${TG_REPORT_DATA_DIR}/metabase
${TG_REPORT_DATA_DIR}/uploads
${TG_REPORT_DATA_DIR}/backups
```

## 备份和恢复

备份服务会每天生成压缩 SQL：

```text
${TG_REPORT_DATA_DIR}/backups/tg_report_YYYY-MM-DD_HHMMSS.sql.gz
```

手动备份：

```bash
docker exec tg-report-postgres pg_dump -U tg_report tg_report | gzip > /volume1/docker/tg-report/backups/manual_$(date +%F_%H%M%S).sql.gz
```

恢复前建议先停应用：

```bash
docker-compose -f docker-compose.synology.yml stop report-web metabase backup
```

恢复数据库：

```bash
gunzip -c /volume1/docker/tg-report/backups/备份文件.sql.gz | docker exec -i tg-report-postgres psql -U tg_report -d tg_report
```

恢复后启动：

```bash
docker-compose -f docker-compose.synology.yml up -d
```

## GHCR 权限

如果群晖拉取镜像失败，先到 GitHub Package 页面确认镜像是否公开。

如果保持私有镜像，需要在群晖登录 GHCR：

```bash
docker login ghcr.io
```

用户名填 GitHub 用户名，密码填 GitHub Personal Access Token，Token 至少需要 `read:packages` 权限。
