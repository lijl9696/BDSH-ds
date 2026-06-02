# 图标资源约定

程序会自动从这个目录读取 PNG 图标，并统一缩放到固定显示尺寸。

## 程序图标

把程序图标放在：

```text
assets/icons/app.png
```

建议源文件尺寸：`256x256` 或 `512x512`，PNG 透明背景最好。程序会按窗口图标需要自动缩放。

Windows 打包时，如果你只提供 `app.png`，打包脚本会自动生成并使用：

```text
assets/icons/app.ico
```

这个 `.ico` 会作为 exe 文件图标。

## UI 图标

可选替换这些图标：

```text
assets/icons/file.png
assets/icons/meituan.png
assets/icons/douyin.png
assets/icons/excel.png
assets/icons/image.png
assets/icons/refresh.png
```

建议源文件尺寸：`128x128` 或更大，PNG 透明背景。代码中统一按 `40x40` 显示，不要求你提供的文件大小一致。

如果某个图标不存在，程序会使用内置的统一线性图标兜底。
