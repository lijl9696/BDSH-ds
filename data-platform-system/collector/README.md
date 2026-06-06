# 自动采集服务

`collector` 是报表自动下载服务。它通过 Playwright 模拟浏览器操作平台后台，下载 Excel 后调用 `report-web` 的导入接口入库。

## 原则

- 只下载报表和调用导入接口，不直接写数据库。
- 第一次人工登录，保存 Playwright 登录态文件。
- 登录态失效时停止任务并写日志，不自动处理验证码或短信。
- 每个平台的页面地址和按钮选择器放在 `config/jobs.yml`。

## 目录

```text
collector/
  collector/
    browser_runner.py  # Playwright 下载流程
    import_client.py   # 调用 report-web 导入接口
    scheduler.py       # 定时任务
    cli.py             # 手动运行/保存登录态
  config/
    jobs.example.yml
```

## 配置任务

复制示例：

```bash
cp config/jobs.example.yml config/jobs.yml
```

把任务里的 URL 和 selector 改成真实平台页面：

```yaml
jobs:
  - code: meituan_daily
    enabled: true
    platform_code: meituan
    schedule_cron: "30 6 * * *"
    state_file: meituan_state.json
    report_page_url: "报表生成页面"
    download_mode: direct
    steps:
      - action: click
        selector: "text=使用模板"
      - action: wait
        seconds: 2
    download_selector: "text=下载"
```

`steps[].selector` 和 `download_selector` 是 Playwright locator。常用写法：

```text
text=生成报表
text=下载
button:has-text("下载")
[data-testid="download"]
```

下载模式：

| 模式 | 说明 |
| --- | --- |
| `direct` | 点击下载按钮后浏览器直接下载 Excel，适合美团当前报表中心 |
| `download_center` | 先触发生成，再进入下载中心点击下载，适合抖音这类分离流程 |

步骤动作：

| action | 字段 | 说明 |
| --- | --- | --- |
| `click` | `selector` | 点击元素 |
| `fill` | `selector`、`value` | 输入文本 |
| `wait` | `seconds` | 等待若干秒 |
| `goto` | `url` | 跳转页面 |

美团当前流程是直接下载型：

```text
报表中心 -> 使用模板 -> 弹窗选择时间范围 -> 下载 Excel
```

时间范围弹窗的选择器需要后续根据真实 DOM 再细化。

## 手动运行

进入容器后可以执行：

```bash
python -m collector.cli run meituan_daily
```

## 登录态

`login` 命令会打开浏览器让你人工登录，然后保存登录态：

```bash
python -m collector.cli login meituan_daily --login-url "登录页面 URL"
```

在 NAS 无图形界面时，更推荐先在本地可视化环境生成 `meituan_state.json`，再复制到 NAS 的 collector state 目录。

如果平台在 Playwright 浏览器里拒绝登录，可以改用真实 Chrome 调试：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/../runtime/collector/real-chrome-profile"
```

在这个 Chrome 里人工登录后台并进入报表中心，然后让 collector 连接它：

```bash
COLLECTOR_BROWSER_CDP_URL=http://127.0.0.1:9222 \
COLLECTOR_JOBS_PATH=config/jobs.local.yml \
COLLECTOR_DOWNLOADS_DIR=../runtime/collector/downloads \
.venv/bin/python -m collector.cli download meituan_daily
```

这种模式不让 Playwright 执行登录，只用真实 Chrome 会话做已登录后的报表下载。

## 导入接口

下载成功后会自动调用：

```text
POST /imports/files
GET /imports/{batch_id}/preview
POST /imports/{batch_id}/commit
```

接口账号密码使用：

```text
IMPORT_AUTH_USERNAME
IMPORT_AUTH_PASSWORD
```

## 后续落地步骤

1. 拿到美团/抖音后台真实页面。
2. 用浏览器开发者工具或 Playwright 调试出按钮 selector。
3. 先手动执行 `python -m collector.cli run <job>`。
4. 下载和入库都成功后，再启用定时任务。
